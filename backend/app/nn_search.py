"""Val 샤프지수가 가장 높은 신경망·앙상블을 찾는 1회성 탐색 도구.

`docker compose exec backend python -m app.nn_search` 로 돌립니다(ml_automl.py 와
같은 패턴 — API 없음, 평소 흐름에 영향 없음). 세 단계로 쌓아 올립니다.

  1단계  피처 세트(base 8개 · extended 19개) × 아키텍처 5종을 기본 설정으로 학습
  2단계  1단계 Val 샤프 상위 3개 조합에 대해 hidden · dropout · weight_decay · lr 격자 탐색
  3단계  최고 설정의 시드 앙상블, 상위 모델 앙상블, 전통ML·룰베이스와의 하이브리드

끝나면 승자 하나를 DB(model_experiments)와 파일(WINNER_MODEL_DIR — 가중치 · 설정)로
남깁니다. `--winner-only` 를 붙이면 전체 탐색 없이 result.json 의 승자만 다시 학습해
저장하고, score_with_winner 는 저장된 모델로 재학습 없이 채점합니다.

선택은 끝까지 Val(2024) 샤프로만 합니다. OOS(2025~)는 최종 선정된 후보 하나에
대해서만 마지막에 한 번 꺼내 봅니다 — 탐색 도중 OOS 를 보고 고르면 그 숫자는
더 이상 표본밖 성과가 아닙니다. 조합을 많이 시험할수록 Val 샤프는 우연히도
올라가므로, 시험한 조합 수를 결과에 함께 남깁니다.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import joblib
import torch
from torch import nn

from . import ml_selection, nn_selection, selection
from .schemas import Candle

RESULT_PATH = Path("/tmp/nn-search/result.json")
# 승자 모델 가중치 — 다른 모델 파일과 같은 규칙(컨테이너 안 /tmp, 0600)입니다
WINNER_MODEL_DIR = nn_selection.NN_MODEL_DIR / nn_selection.SEARCH_WINNER_KEY
MANIFEST_NAME = "manifest.json"
MAX_EPOCHS = 40
PATIENCE = 5
TOP_COMBOS = 3
HP_GRID = {
    "hidden": (16, 32, 64),
    "dropout": (0.2, 0.4),
    "weight_decay": (1e-3, 1e-2),
    "lr": (1e-3, 3e-4),
}
ENSEMBLE_SEEDS = (42, 1, 2, 3, 4)
# 전체 탐색이 훑는 피처 세트 — *_dedup 세트는 탐색 대상이 아니라 승자 재학습용입니다
SEARCH_FEATURE_SETS = ("base", "extended")

DayScores = list[dict[str, float]]


def _log(**fields: object) -> None:
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)


@dataclass
class Dataset:
    """피처 세트 · 계열 하나의 학습 텐서. 한 번 만들어 모든 설정이 같이 씁니다."""

    walk: nn_selection._WalkData
    in_dim: int
    x_train: torch.Tensor
    y_train: torch.Tensor
    x_val: torch.Tensor
    y_val: torch.Tensor
    day_batches: list[tuple[list[str], torch.Tensor] | None]


def build_dataset(walk: nn_selection._WalkData, family: str, in_dim: int) -> Dataset:
    feats_per_day = walk.norm_features if family == "A" else walk.seq_features
    day_batches: list[tuple[list[str], torch.Tensor] | None] = []
    train_x, train_y, val_x, val_y = [], [], [], []
    for date_i, feats, targets in zip(walk.dates_scored, feats_per_day, walk.excess_targets):
        if not feats:
            day_batches.append(None)
            continue
        tickers = list(feats.keys())
        batch = torch.tensor([feats[t] for t in tickers], dtype=torch.float32)
        day_batches.append((tickers, batch))
        y = torch.tensor([targets[t] for t in tickers], dtype=torch.float32)
        if date_i <= nn_selection.TRAIN_END:
            train_x.append(batch)
            train_y.append(y)
        elif date_i <= nn_selection.VAL_END:
            val_x.append(batch)
            val_y.append(y)
    return Dataset(
        walk=walk,
        in_dim=in_dim,
        x_train=torch.cat(train_x),
        y_train=torch.cat(train_y),
        x_val=torch.cat(val_x),
        y_val=torch.cat(val_y),
        day_batches=day_batches,
    )


def build_scoring_dataset(walk: nn_selection._WalkData, family: str, in_dim: int) -> Dataset:
    """채점만 할 때 쓰는 Dataset — 학습 텐서는 비워 둡니다(Train/Val 표본이 없어도 됩니다)."""
    feats_per_day = walk.norm_features if family == "A" else walk.seq_features
    day_batches: list[tuple[list[str], torch.Tensor] | None] = []
    for feats in feats_per_day:
        if not feats:
            day_batches.append(None)
            continue
        tickers = list(feats.keys())
        day_batches.append((tickers, torch.tensor([feats[t] for t in tickers], dtype=torch.float32)))
    empty = torch.empty(0)
    return Dataset(walk=walk, in_dim=in_dim, x_train=empty, y_train=empty, x_val=empty, y_val=empty, day_batches=day_batches)


def build_model(arch: str, in_dim: int, hidden: int, dropout: float) -> nn.Module:
    if arch == "mlp":
        return nn_selection.MlpScorer(hidden=hidden, dropout=dropout, in_dim=in_dim)
    if arch == "cnn1d":
        return nn_selection.Cnn1dScorer(hidden=hidden, dropout=dropout, in_dim=in_dim)
    if arch in ("lstm", "gru"):
        return nn_selection.RecurrentScorer(arch, hidden=hidden, dropout=dropout, in_dim=in_dim)
    if arch == "transformer":
        return nn_selection.TransformerScorer(hidden=hidden, dropout=dropout, in_dim=in_dim)
    raise ValueError(f"알 수 없는 아키텍처입니다: {arch}")


def score_days(ds: Dataset, model: nn.Module) -> DayScores:
    model.eval()
    out: DayScores = []
    with torch.no_grad():
        for item in ds.day_batches:
            if item is None:
                out.append({})
                continue
            tickers, batch = item
            out.append(dict(zip(tickers, model(batch).tolist())))
    return out


@dataclass(frozen=True)
class TrainedMember:
    """학습이 끝난 신경망 하나 — 다시 만들 설정, 가중치, 학습 기록을 같이 들고 다닙니다."""

    name: str
    arch: str
    feature_set: str
    hidden: int
    dropout: float
    weight_decay: float
    lr: float
    seed: int
    state_dict: dict[str, torch.Tensor]
    loss_curve: list[dict]
    best_epoch: int
    param_count: int

    def config(self) -> dict:
        return {
            "name": self.name,
            "arch": self.arch,
            "feature_set": self.feature_set,
            "hidden": self.hidden,
            "dropout": self.dropout,
            "weight_decay": self.weight_decay,
            "lr": self.lr,
            "seed": self.seed,
        }


def config_name(arch: str, feature_set: str, hidden: int, dropout: float, weight_decay: float, lr: float, seed: int) -> str:
    return f"{arch}|{feature_set}|h{hidden}|d{dropout}|wd{weight_decay}|lr{lr}|s{seed}"


def parse_config_name(name: str) -> tuple[str, str, int, float, float, float, int]:
    """config_name 의 역함수. 신경망 설정 이름이 아니면 ValueError 입니다."""
    parts = name.split("|")
    prefixes = ("h", "d", "wd", "lr", "s")
    if len(parts) != 7 or any(not part.startswith(pre) for part, pre in zip(parts[2:], prefixes)):
        raise ValueError(f"신경망 설정 이름이 아닙니다: {name}")
    arch, feature_set = parts[0], parts[1]
    if arch not in nn_selection.NN_MODEL_REGISTRY or feature_set not in nn_selection.FEATURE_SETS:
        raise ValueError(f"신경망 설정 이름이 아닙니다: {name}")
    return (
        arch,
        feature_set,
        int(parts[2][1:]),
        float(parts[3][1:]),
        float(parts[4][2:]),
        float(parts[5][2:]),
        int(parts[6][1:]),
    )


@dataclass
class Evaluated:
    """후보 하나의 채점 결과. oos 는 최종 선정 뒤에만 꺼내 봅니다."""

    name: str
    scores: DayScores
    is_summary: selection.Top5Summary
    val_summary: selection.Top5Summary
    _oos_summary: selection.Top5Summary
    # 구간별 (요약, 누적수익률 곡선) — 승자를 DB 에 남길 때만 씁니다(winner_rows)
    _periods: list[tuple[selection.Top5Summary, list[selection.Top5Point]]] = field(default_factory=list)
    # 이 후보를 이루는 구성원 이름(단일 설정이면 자기 자신 하나)
    members: tuple[str, ...] = ()
    # 구성원 중 신경망인 것들의 학습 결과(가중치 포함) — 승자를 저장할 때 씁니다
    trained: tuple[TrainedMember, ...] = ()

    def reveal_oos(self) -> selection.Top5Summary:
        return self._oos_summary


def evaluate(
    name: str,
    walk: nn_selection._WalkData,
    scores: DayScores,
    cost: float,
    members: tuple[str, ...] = (),
    trained: tuple[TrainedMember, ...] = (),
) -> Evaluated:
    sorted_candidates = [sorted(day.items(), key=lambda x: x[1], reverse=True) for day in scores]
    result = selection.aggregate_top_k(
        name,
        walk.all_dates,
        sorted_candidates,
        walk.realized_per_day,
        walk.universe_returns,
        k=nn_selection.FIXED_K,
        cost=cost,
    )
    periods = selection.summarize_periods(result, [nn_selection.TRAIN_END, nn_selection.VAL_END])
    (is_s, _), (val_s, _), (oos_s, _) = periods
    return Evaluated(name, scores, is_s, val_s, oos_s, periods, members or (name,), trained)


def winner_rows(winner: Evaluated, cost: float, configs_tried: int, candidates_compared: int) -> list[tuple]:
    """최종 승자를 model_experiments 세 줄(is · val · oos)로 만듭니다.

    신경망 5종과 구분되도록 model_key 는 항상 SEARCH_WINNER_KEY 하나입니다 — 탐색을
    다시 돌려 승자가 바뀌어도 줄이 늘지 않고 덮어씁니다. 몇 개를 시험해 고른 것인지는
    hyperparams 에 함께 남겨 화면이 그대로 보여줍니다.
    """
    hyperparams = {
        "members": list(winner.members),
        "configs_tried": configs_tried,
        "candidates_compared": candidates_compared,
        "selected_by": "val_sharpe",
        "combine": "rank_average",
        "huber_delta": nn_selection.HUBER_DELTA,
        "model_dir": str(WINNER_MODEL_DIR),
        # 신경망 구성원마다: 다시 만들 설정 · 파라미터 수 · 가중치를 고른 epoch · 그 epoch 의 손실
        "member_details": [
            {
                **m.config(),
                "param_count": m.param_count,
                "best_epoch": m.best_epoch,
                "epochs": len(m.loss_curve),
                "train_loss": m.loss_curve[m.best_epoch - 1]["train_loss"] if m.best_epoch else None,
                "val_loss": m.loss_curve[m.best_epoch - 1]["val_loss"] if m.best_epoch else None,
                "loss_curve": m.loss_curve,
            }
            for m in winner.trained
        ],
    }
    param_count = sum(m.param_count for m in winner.trained) if winner.trained else None
    return [
        nn_selection.experiment_row(
            "neural_net",
            nn_selection.SEARCH_WINNER_KEY,
            winner.name,
            nn_selection.FIXED_K,
            cost,
            period,
            summary,
            points,
            hyperparams=hyperparams,
            param_count=param_count,
        )
        for period, (summary, points) in zip(("is", "val", "oos"), winner._periods)
    ]


# ──────────────────────────────────────────────────────────────
# 승자 모델 저장 · 불러오기 — 수치만 남기면 그 모델을 다시 쓸 수 없습니다
# ──────────────────────────────────────────────────────────────


def save_winner_models(winner: Evaluated, model_dir: Path = WINNER_MODEL_DIR) -> Path:
    """승자의 신경망 구성원을 전부 파일로 남깁니다(구성원당 .pt 하나 + manifest.json).

    .pt 에는 가중치와 함께 모델을 다시 만드는 데 필요한 설정이 들어 있어, 이
    디렉터리만 있으면 load_winner_models 로 그대로 복원됩니다. 신경망이 아닌
    구성원(RandomForest · 룰베이스)은 이름만 manifest 의 other_components 에 적습니다.
    """
    model_dir.mkdir(parents=True, exist_ok=True)
    for old in model_dir.glob("member_*.pt"):
        old.unlink()

    files: list[str] = []
    for i, member in enumerate(winner.trained):
        path = model_dir / f"member_{i}.pt"
        torch.save({"config": member.config(), "state_dict": member.state_dict}, path)
        os.chmod(path, 0o600)
        files.append(path.name)

    trained_names = {m.name for m in winner.trained}
    manifest = {
        "name": winner.name,
        "saved_at": datetime.now().isoformat(),
        "combine": "rank_average",
        "k": nn_selection.FIXED_K,
        "seq_len": nn_selection.SEQ_LEN,
        "train_end": nn_selection.TRAIN_END.isoformat(),
        "val_end": nn_selection.VAL_END.isoformat(),
        "nn_members": [{**m.config(), "file": f} for m, f in zip(winner.trained, files)],
        "other_components": [n for n in winner.members if n not in trained_names],
    }
    manifest_path = model_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return model_dir


def load_winner_models(model_dir: Path = WINNER_MODEL_DIR) -> tuple[dict, list[tuple[dict, nn.Module]]]:
    """save_winner_models 가 남긴 디렉터리에서 (manifest, [(설정, 모델), ...])을 복원합니다."""
    manifest_path = model_dir / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"저장된 탐색 승자 모델이 없습니다: {model_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    models: list[tuple[dict, nn.Module]] = []
    for entry in manifest["nn_members"]:
        saved = torch.load(model_dir / entry["file"], map_location="cpu", weights_only=True)
        config = saved["config"]
        model = build_model(
            config["arch"], nn_selection.feature_dim(config["feature_set"]), config["hidden"], config["dropout"]
        )
        model.load_state_dict(saved["state_dict"])
        model.eval()
        models.append((config, model))
    return manifest, models


def score_with_winner(
    universe: dict[str, list[Candle]], model_dir: Path = WINNER_MODEL_DIR
) -> tuple[nn_selection._WalkData, DayScores]:
    """저장된 승자 모델로 universe 의 모든 날짜를 채점합니다(재학습 없음).

    새 일봉이 쌓인 뒤 이 함수만 다시 부르면 늘어난 기간까지 백테스트할 수 있습니다.
    _walk_all 이 "다음 거래일 결과가 있는 날"만 채점하므로, 가장 최근 일봉 하루(아직
    다음 날이 없는 날)의 순위는 여기 들어 있지 않습니다 — 오늘 살 종목을 뽑으려면
    그 하루치 피처를 만드는 코드가 따로 필요합니다. 신경망만으로 이뤄진 승자만 지원합니다.
    """
    manifest, models = load_winner_models(model_dir)
    if manifest["other_components"]:
        raise ValueError(f"신경망이 아닌 구성원이 섞인 승자는 지원하지 않습니다: {manifest['other_components']}")

    datasets: dict[tuple[str, str], Dataset] = {}
    walks: dict[str, nn_selection._WalkData] = {}
    components: list[DayScores] = []
    for config, model in models:
        feature_set = config["feature_set"]
        family = nn_selection.NN_MODEL_REGISTRY[config["arch"]].family
        if feature_set not in walks:
            walks[feature_set] = nn_selection._walk_all(universe, feature_set)
        if (feature_set, family) not in datasets:
            datasets[(feature_set, family)] = build_scoring_dataset(
                walks[feature_set], family, nn_selection.feature_dim(feature_set)
            )
        components.append(score_days(datasets[(feature_set, family)], model))

    walk = next(iter(walks.values()))
    return walk, rank_average(components) if len(components) > 1 else components[0]


def rank_average(components: list[DayScores]) -> DayScores:
    """날짜별로 각 구성원의 점수를 횡단면 순위로 바꿔 평균합니다.

    그날 어떤 구성원에게 점수가 없는 종목은 그 구성원 안에서 공동 최하위로 칩니다.
    """
    n_days = len(components[0])
    out: DayScores = []
    for i in range(n_days):
        # 정렬해 둡니다 — set 순서는 실행마다 달라서(문자열 해시), 평균 순위가 같은
        # 종목끼리의 Top-K 동점 처리가 실행할 때마다 바뀌었습니다(실제로 겪은 문제)
        tickers = sorted(set().union(*(c[i].keys() for c in components)))
        if not tickers:
            out.append({})
            continue
        ranked = [
            nn_selection._rank_pct({t: c[i].get(t, float("-inf")) for t in tickers}) for c in components
        ]
        out.append({t: sum(r[t] for r in ranked) / len(ranked) for t in tickers})
    return out


def run_config(
    ds: Dataset,
    feature_set: str,
    arch: str,
    hidden: int,
    dropout: float,
    weight_decay: float,
    lr: float,
    seed: int,
    cost: float,
) -> Evaluated:
    name = config_name(arch, feature_set, hidden, dropout, weight_decay, lr, seed)
    started = time.time()
    nn_selection._set_seed(seed)
    model = build_model(arch, ds.in_dim, hidden, dropout)
    model, loss_curve, best_epoch = nn_selection._train_model(
        model,
        ds.x_train,
        ds.y_train,
        ds.x_val,
        ds.y_val,
        lr=lr,
        weight_decay=weight_decay,
        max_epochs=MAX_EPOCHS,
        patience=PATIENCE,
    )
    member = TrainedMember(
        name=name,
        arch=arch,
        feature_set=feature_set,
        hidden=hidden,
        dropout=dropout,
        weight_decay=weight_decay,
        lr=lr,
        seed=seed,
        state_dict={k: v.detach().clone() for k, v in model.state_dict().items()},
        loss_curve=loss_curve,
        best_epoch=best_epoch,
        param_count=sum(p.numel() for p in model.parameters()),
    )
    evaluated = evaluate(name, ds.walk, score_days(ds, model), cost, trained=(member,))
    _log(
        event="config",
        name=name,
        is_sharpe=round(evaluated.is_summary.sharpe, 3),
        val_sharpe=round(evaluated.val_summary.sharpe, 3),
        best_epoch=best_epoch,
        epochs=len(loss_curve),
        seconds=round(time.time() - started),
    )
    return evaluated


def _summary_dict(s: selection.Top5Summary) -> dict:
    return {
        "sharpe": s.sharpe,
        "cagr": s.cagr,
        "mdd": s.mdd,
        "win_rate": s.win_rate,
        "final_portfolio": s.final_portfolio,
        "calmar": s.calmar,
        "trading_nights": s.trading_nights,
    }


def run_search(
    universe: dict[str, list[Candle]], cost: float = nn_selection.DEFAULT_COST
) -> tuple[dict, Evaluated]:
    """탐색 전체를 돌려 (결과 요약, 승자)를 돌려줍니다."""
    # ── 데이터셋: 피처 세트 2 × 계열 2 ──────────────────────────
    datasets: dict[tuple[str, str], Dataset] = {}
    walks: dict[str, nn_selection._WalkData] = {}
    for feature_set in SEARCH_FEATURE_SETS:
        walks[feature_set] = nn_selection._walk_all(universe, feature_set)
        for family in ("A", "B"):
            datasets[(feature_set, family)] = build_dataset(
                walks[feature_set], family, nn_selection.feature_dim(feature_set)
            )
        _log(event="dataset", feature_set=feature_set, train_rows=len(datasets[(feature_set, "A")].y_train))
    base_walk = walks["base"]

    def dataset_for(feature_set: str, arch: str) -> Dataset:
        return datasets[(feature_set, nn_selection.NN_MODEL_REGISTRY[arch].family)]

    all_configs: list[Evaluated] = []

    # ── 1단계: 피처 세트 × 아키텍처 (기본 설정) ─────────────────
    stage1: dict[tuple[str, str], Evaluated] = {}
    for feature_set in SEARCH_FEATURE_SETS:
        for arch in nn_selection.NN_MODEL_REGISTRY:
            ev = run_config(dataset_for(feature_set, arch), feature_set, arch, 32, 0.3, 1e-3, 1e-3, 42, cost)
            stage1[(feature_set, arch)] = ev
            all_configs.append(ev)

    top_combos = sorted(stage1, key=lambda k: stage1[k].val_summary.sharpe, reverse=True)[:TOP_COMBOS]
    _log(event="stage1_done", top_combos=top_combos)

    # ── 2단계: 상위 조합의 하이퍼파라미터 격자 ──────────────────
    best_params: dict[tuple[str, str], tuple[int, float, float, float]] = {}
    for feature_set, arch in top_combos:
        best_ev = stage1[(feature_set, arch)]
        best_params[(feature_set, arch)] = (32, 0.3, 1e-3, 1e-3)
        for hidden, dropout, wd, lr in itertools.product(*HP_GRID.values()):
            ev = run_config(dataset_for(feature_set, arch), feature_set, arch, hidden, dropout, wd, lr, 42, cost)
            all_configs.append(ev)
            if ev.val_summary.sharpe > best_ev.val_summary.sharpe:
                best_ev = ev
                best_params[(feature_set, arch)] = (hidden, dropout, wd, lr)

    ranked = sorted(all_configs, key=lambda e: e.val_summary.sharpe, reverse=True)
    best_single = ranked[0]
    best_combo = max(
        top_combos,
        key=lambda k: max(e.val_summary.sharpe for e in all_configs if e.name.startswith(f"{k[1]}|{k[0]}|")),
    )
    _log(event="stage2_done", best_single=best_single.name, val_sharpe=best_single.val_summary.sharpe)

    # ── 3단계: 앙상블 ────────────────────────────────────────
    feature_set, arch = best_combo
    hidden, dropout, wd, lr = best_params[best_combo]
    seed_members = [
        run_config(dataset_for(feature_set, arch), feature_set, arch, hidden, dropout, wd, lr, seed, cost)
        for seed in ENSEMBLE_SEEDS
    ]

    # 전통ML(RandomForest, Train 구간만 학습) · 룰베이스(vol_surge_edge) 점수
    train_universe = {
        t: [c for c in cs if c.time <= nn_selection.TRAIN_END] for t, cs in universe.items()
    }
    train_universe = {t: cs for t, cs in train_universe.items() if cs}
    rf_path = nn_selection.ML_COMPARE_MODEL_DIR / "random_forest.joblib"
    ml_selection.train_and_save(train_universe, model_key="random_forest", model_path=rf_path)
    _, rf_sorted, _, _ = ml_selection._score_with_model(universe, joblib.load(rf_path)["model"])
    rf_scores: DayScores = [dict(day) for day in rf_sorted]
    _, rule_sorted, _, _ = selection._score_all_days(universe, "vol_surge_edge")
    rule_scores: DayScores = [dict(day) for day in rule_sorted]

    seed_ens = rank_average([m.scores for m in seed_members])
    top3_ens = rank_average([e.scores for e in ranked[:3]])
    seed_names = tuple(m.name for m in seed_members)
    top3_names = tuple(e.name for e in ranked[:3])
    rf_name, rule_name = "random_forest", "vol_surge_edge"
    seed_trained = tuple(t for m in seed_members for t in m.trained)
    top3_trained = tuple(t for e in ranked[:3] for t in e.trained)
    candidates: list[Evaluated] = [
        best_single,
        evaluate("전통ML(RandomForest)", base_walk, rf_scores, cost, (rf_name,)),
        evaluate("룰베이스(vol_surge_edge)", base_walk, rule_scores, cost, (rule_name,)),
        evaluate("NN 시드앙상블", base_walk, seed_ens, cost, seed_names, seed_trained),
        evaluate("NN 상위3 앙상블", base_walk, top3_ens, cost, top3_names, top3_trained),
        evaluate("NN 시드앙상블 + RF", base_walk, rank_average([seed_ens, rf_scores]), cost, (*seed_names, rf_name), seed_trained),
        evaluate("NN 시드앙상블 + 룰", base_walk, rank_average([seed_ens, rule_scores]), cost, (*seed_names, rule_name), seed_trained),
        evaluate("NN 시드앙상블 + RF + 룰", base_walk, rank_average([seed_ens, rf_scores, rule_scores]), cost, (*seed_names, rf_name, rule_name), seed_trained),
        evaluate("NN 상위3 + RF", base_walk, rank_average([top3_ens, rf_scores]), cost, (*top3_names, rf_name), top3_trained),
        evaluate("NN 상위3 + 룰", base_walk, rank_average([top3_ens, rule_scores]), cost, (*top3_names, rule_name), top3_trained),
        evaluate("NN 상위3 + RF + 룰", base_walk, rank_average([top3_ens, rf_scores, rule_scores]), cost, (*top3_names, rf_name, rule_name), top3_trained),
        evaluate("RF + 룰", base_walk, rank_average([rf_scores, rule_scores]), cost, (rf_name, rule_name)),
    ]
    for c in candidates:
        _log(event="candidate", name=c.name, is_sharpe=round(c.is_summary.sharpe, 3), val_sharpe=round(c.val_summary.sharpe, 3))

    winner = max(candidates, key=lambda c: c.val_summary.sharpe)

    report = {
        "finished_at": datetime.now().isoformat(),
        "configs_tried": len(all_configs) + len(seed_members),
        "candidates_compared": len(candidates),
        "top_configs": [
            {"name": e.name, "is_sharpe": e.is_summary.sharpe, "val_sharpe": e.val_summary.sharpe}
            for e in ranked[:10]
        ],
        "candidates": [
            {"name": c.name, "is": _summary_dict(c.is_summary), "val": _summary_dict(c.val_summary)}
            for c in candidates
        ],
        "winner": {
            "name": winner.name,
            "members": list(winner.members),
            "is": _summary_dict(winner.is_summary),
            "val": _summary_dict(winner.val_summary),
            # OOS 는 여기서 처음, 최종 선정된 후보에 대해서만 꺼냅니다
            "oos": _summary_dict(winner.reveal_oos()),
        },
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(event="done", **report["winner"], configs_tried=report["configs_tried"])
    return report, winner


def rebuild_winner(
    universe: dict[str, list[Candle]],
    report: dict,
    cost: float = nn_selection.DEFAULT_COST,
    feature_set: str | None = None,
) -> Evaluated:
    """저장된 탐색 결과(result.json)의 승자만 다시 학습합니다 — 전체 탐색(1시간 이상)을
    다시 돌리지 않고 승자의 가중치·학습 기록을 얻는 길입니다.

    새로 고르는 것이 없으므로 선택 편향을 더하지 않습니다. 시드가 고정이라 같은
    데이터에서는 전체 탐색 때와 같은 모델이 나옵니다. 승자가 신경망만으로 이뤄져
    있을 때만 됩니다(구성원 이름에서 설정을 복원합니다).

    feature_set 을 주면 구성원의 피처 세트만 그것으로 바꿔 같은 설정·시드로 다시
    학습합니다(예: 중복 피처를 뺀 extended_dedup). 이때는 같은 데이터가 아니라
    수정된 입력이므로 결과가 전체 탐색 때와 달라지는 것이 정상입니다.
    """
    configs = [parse_config_name(name) for name in report["winner"]["members"]]
    if feature_set is not None:
        if feature_set not in nn_selection.FEATURE_SETS:
            raise ValueError(f"알 수 없는 피처 세트입니다: {feature_set}")
        configs = [(a, feature_set, h, d, wd, lr, s) for a, _, h, d, wd, lr, s in configs]

    walks: dict[str, nn_selection._WalkData] = {"base": nn_selection._walk_all(universe, "base")}
    datasets: dict[tuple[str, str], Dataset] = {}
    trained: list[Evaluated] = []
    for arch, feature_set, hidden, dropout, wd, lr, seed in configs:
        family = nn_selection.NN_MODEL_REGISTRY[arch].family
        if feature_set not in walks:
            walks[feature_set] = nn_selection._walk_all(universe, feature_set)
        if (feature_set, family) not in datasets:
            datasets[(feature_set, family)] = build_dataset(
                walks[feature_set], family, nn_selection.feature_dim(feature_set)
            )
        trained.append(run_config(datasets[(feature_set, family)], feature_set, arch, hidden, dropout, wd, lr, seed, cost))

    scores = rank_average([t.scores for t in trained]) if len(trained) > 1 else trained[0].scores
    # 전체 탐색과 똑같이 base 피처의 날짜축으로 채점합니다
    return evaluate(
        report["winner"]["name"],
        walks["base"],
        scores,
        cost,
        tuple(m.name for t in trained for m in t.trained),
        tuple(m for t in trained for m in t.trained),
    )


async def _load_winner_report() -> dict:
    """승자가 누구였는지 읽습니다 — result.json 이 있으면 그것, 없으면 DB.

    result.json 은 컨테이너 안 /tmp 라 컨테이너를 새로 만들면 사라집니다. 그래도 승자를
    복원할 수 있도록 DB(model_experiments)에 남긴 구성원 이름으로 같은 모양을 만듭니다.
    """
    from . import db

    if RESULT_PATH.exists():
        return json.loads(RESULT_PATH.read_text(encoding="utf-8"))

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT model_label, sharpe, hyperparams FROM model_experiments "
            "WHERE generation = 'neural_net' AND model_key = $1 AND period = 'val'",
            nn_selection.SEARCH_WINNER_KEY,
        )
    if row is None:
        raise SystemExit("저장된 탐색 승자가 없습니다 — 먼저 전체 탐색(python -m app.nn_search)을 돌려야 합니다")
    meta = json.loads(row["hyperparams"] or "{}")
    return {
        "configs_tried": meta.get("configs_tried", 0),
        "candidates_compared": meta.get("candidates_compared", 0),
        "winner": {"name": row["model_label"], "members": meta.get("members", []), "val": {"sharpe": row["sharpe"]}},
    }


async def _main(winner_only: bool = False, feature_set: str | None = None) -> None:
    from . import db
    from .main import _fetch_universe_candles

    await db.connect()
    try:
        async with db.acquire() as conn:
            universe = await _fetch_universe_candles(conn, days=3650, start=None, end=None)
        _log(event="universe", tickers=len(universe))
        # 학습은 이벤트 루프 밖에서 돌립니다(수십 분 걸립니다)
        if winner_only:
            report = await _load_winner_report()
            winner = await asyncio.to_thread(rebuild_winner, universe, report, feature_set=feature_set)
            _log(
                event="winner_rebuilt",
                name=winner.name,
                feature_set=feature_set or "원래 그대로",
                val_sharpe=winner.val_summary.sharpe,
                previous_val_sharpe=report["winner"]["val"]["sharpe"],
            )
            if feature_set is not None:
                _archive_previous_winner()
        else:
            report, winner = await asyncio.to_thread(run_search, universe)
        rows = winner_rows(winner, nn_selection.DEFAULT_COST, report["configs_tried"], report["candidates_compared"])
        model_dir = save_winner_models(winner)
        _log(event="models_saved", dir=str(model_dir), nn_members=len(winner.trained))
        # 승자 하나만 DB 에 남깁니다 — 화면(Top5.tsx Week 10)의 "탐색 최고" 줄이 이걸 읽습니다
        async with db.acquire() as conn:
            await nn_selection.upsert_experiment_rows(conn, rows)
        _log(event="saved", model_key=nn_selection.SEARCH_WINNER_KEY, rows=len(rows))
    finally:
        await db.disconnect()


def _archive_previous_winner() -> None:
    """피처 세트를 바꿔 다시 저장하기 전에, 지금 저장된 승자 가중치를 한 벌 보관합니다.

    보관 이름은 manifest 의 저장 시각을 붙여(search_winner_YYYYMMDD-HHMMSS) 몇 번을 다시
    저장해도 이전 것이 덮어써지지 않습니다.
    """
    manifest_path = WINNER_MODEL_DIR / MANIFEST_NAME
    if not manifest_path.exists():
        return
    saved_at = json.loads(manifest_path.read_text(encoding="utf-8")).get("saved_at", "unknown")
    stamp = "".join(ch for ch in saved_at[:19] if ch.isdigit() or ch == "T").replace("T", "-")
    archive = WINNER_MODEL_DIR.with_name(f"{WINNER_MODEL_DIR.name}_{stamp}")
    if not archive.exists():
        shutil.copytree(WINNER_MODEL_DIR, archive)
        _log(event="previous_winner_archived", dir=str(archive))


def _argument(flag: str) -> str | None:
    args = sys.argv[1:]
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


if __name__ == "__main__":
    # --winner-only: 전체 탐색 없이 result.json 의 승자만 다시 학습해 저장합니다
    # --feature-set NAME: (winner-only 와 함께) 구성원의 피처 세트를 바꿔 다시 학습합니다
    sys.exit(
        asyncio.run(
            _main(winner_only="--winner-only" in sys.argv[1:], feature_set=_argument("--feature-set"))
        )
    )
