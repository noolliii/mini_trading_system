"""방식 B — 연도별 확장 윈도우 Walk-Forward. `docker compose exec backend python -m app.nn_walkforward`

방식 A(Train 2020~2023 · Val 2024 · OOS 2025~)는 "표본밖 구간이 하나"라 그 한 해가 운 좋게
좋았는지 알 수 없습니다. 방식 B 는 시험 연도를 2022 → 2026 으로 한 해씩 옮기며 매번 다시
학습·선택하고, 시험 구간을 이어 붙여 하나의 표본밖 곡선을 만듭니다.

  폴드(시험 연도 Y):  학습 = 처음 ~ Y-2 말  ·  Val = Y-1 (얼리스토핑·선택 전용)  ·  시험 = Y

폴드마다 아래를 처음부터 다시 합니다(이전 폴드의 가중치·선택을 가져오지 않습니다).

  - 룰베이스: 학습이 없음. 고정 전략(vol_surge_edge)과, 폴드마다 Val 샤프로 다시 고른 전략 둘 다
  - 전통ML 4종: 학습 구간으로 학습 → Val 샤프로 한 개 선택
  - 신경망 5종(기본 설정): 학습 구간으로 학습, Val 로 얼리스토핑 → Val 샤프로 한 개 선택
  - 탐색 최고 설정(GRU hidden 64 · dropout 0.4 · 시드 5개 순위평균) — 설정은 고정
  - 벤치마크: 전 종목 동일가중(같은 왕복 비용 차감)

공통 규격은 방식 A 와 같습니다: Top-5 동일비중 · 왕복 0.10% · 확장 피처 19개를 날짜별 종목 간
순위로 변환 · 타깃은 시장중립 초과수익률 · HuberLoss(delta 0.02) · 시드 고정.

한계(리포트에도 적습니다): 「탐색 최고」의 설정(hidden · dropout 등)은 방식 A 의 Val(2024) 과
OOS(2025~)를 본 뒤에 정해진 값이라, 폴드 2023~2026 에서는 그 설정을 고를 때 이미 본 해가
시험 구간에 섞여 있습니다. 순수한 표본밖 증거는 「폴드마다 다시 고른」 행(룰 재선택 ·
전통ML · 신경망 기본 5종 중 Val 최고)입니다.

DB 에는 쓰지 않습니다. 폴드가 하나 끝날 때마다 결과를 파일에 남겨, 중간에 끊겨도 끝난
폴드는 건너뛰고 이어서 돕니다(--fresh 면 처음부터).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import torch

from . import metrics, ml_selection, nn_search, nn_selection, selection
from .schemas import Candle

CACHE_DIR = Path("/tmp/nn-search")
RESULT_PATH = CACHE_DIR / "walkforward.json"
TEST_YEARS: tuple[int, ...] = (2022, 2023, 2024, 2025, 2026)
FEATURE_SET = "extended"
FIXED_RULE = "vol_surge_edge"  # 방식 A 의 룰베이스 최고(Val 샤프 1위)

# 방식 A 탐색에서 고른 승자의 설정 — 폴드마다 다시 탐색하지 않고 그대로 씁니다
WINNER_ARCH = "gru"
WINNER_HIDDEN = 64
WINNER_DROPOUT = 0.4
WINNER_WEIGHT_DECAY = 1e-3
WINNER_LR = 1e-3

# 신경망 기본 설정(방식 A 의 「신경망 5종」과 같은 값)
DEFAULT_HIDDEN = 32
DEFAULT_DROPOUT = 0.3
DEFAULT_WEIGHT_DECAY = 1e-3
DEFAULT_LR = 1e-3
DEFAULT_SEED = 42

SERIES_NAMES = (
    "rule_fixed",
    "rule_refit",
    "traditional_ml_refit",
    "neural_net_refit",
    "winner_config",
    "benchmark",
)

SERIES_LABELS = {
    "rule_fixed": f"룰베이스 고정({FIXED_RULE})",
    "rule_refit": "룰베이스 폴드마다 재선택",
    "traditional_ml_refit": "전통ML 폴드마다 재학습·재선택",
    "neural_net_refit": "신경망 5종 폴드마다 재학습·재선택",
    "winner_config": "탐색 최고 설정(GRU×5, 설정 고정)",
    "benchmark": "벤치마크(전 종목 동일가중, 비용 차감)",
}


# ──────────────────────────────────────────────────────────────
# 폴드 경계
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fold:
    test_year: int
    train_end: datetime  # 이 날까지가 학습
    val_end: datetime  # 그 다음날 ~ 이 날까지가 Val
    test_end: datetime  # 그 다음날 ~ 이 날까지가 시험

    @property
    def boundaries(self) -> list[datetime]:
        return [self.train_end, self.val_end, self.test_end]


def make_folds(test_years: tuple[int, ...] = TEST_YEARS) -> list[Fold]:
    """시험 연도마다 (학습 ~ Y-2 말, Val = Y-1, 시험 = Y) 경계를 만듭니다.

    경계는 nn_selection.TRAIN_END · VAL_END 와 같은 규약(UTC 12월 31일 00:00)입니다.
    """
    return [
        Fold(
            test_year=y,
            train_end=datetime(y - 2, 12, 31, tzinfo=timezone.utc),
            val_end=datetime(y - 1, 12, 31, tzinfo=timezone.utc),
            test_end=datetime(y, 12, 31, tzinfo=timezone.utc),
        )
        for y in test_years
    ]


# ──────────────────────────────────────────────────────────────
# 구간 요약 · 일별 수익률
# ──────────────────────────────────────────────────────────────


def _split(result: selection.Top5Result, fold: Fold) -> tuple[selection.Top5Summary, selection.Top5Summary, list[selection.Top5Point]]:
    """전체기간 백테스트를 (학습 · Val · 시험 · 이후) 네 구간으로 쪼개 Val 요약, 시험 요약, 시험 곡선을 돌려줍니다."""
    _, (val_s, _), (test_s, test_pts), _ = selection.summarize_periods(result, fold.boundaries)
    return val_s, test_s, test_pts


def _summary_dict(s: selection.Top5Summary) -> dict:
    return nn_search._summary_dict(s)


def _series_entry(points: list[selection.Top5Point], *, benchmark: bool = False) -> dict:
    """시험 구간 곡선을 일별 수익률로 되돌려 둡니다(폴드를 이어 붙일 때 씁니다)."""
    cum = [p.cum_universe if benchmark else p.cum_portfolio for p in points]
    return {
        "dates": [p.time.isoformat() for p in points],
        "returns": metrics.daily_returns_from_cumulative(cum),
    }


# ──────────────────────────────────────────────────────────────
# 학습 데이터 — 날짜 경계로 학습 · Val 텐서를 자릅니다
# ──────────────────────────────────────────────────────────────


@dataclass
class _Family:
    """계열(A 스냅샷 · B 시퀀스) 하나의 채점용 배치와 날짜별 타깃 텐서."""

    scoring: nn_search.Dataset
    dates: list[datetime]
    targets: list[torch.Tensor | None]


def _prepare_family(walk: nn_selection._WalkData, family: str, in_dim: int) -> _Family:
    scoring = nn_search.build_scoring_dataset(walk, family, in_dim)
    targets: list[torch.Tensor | None] = []
    for item, tgt in zip(scoring.day_batches, walk.excess_targets):
        if item is None:
            targets.append(None)
            continue
        tickers, _ = item
        targets.append(torch.tensor([tgt[t] for t in tickers], dtype=torch.float32))
    return _Family(scoring, list(walk.dates_scored), targets)


def _fold_tensors(fam: _Family, fold: Fold) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    train_x, train_y, val_x, val_y = [], [], [], []
    for date_i, item, y in zip(fam.dates, fam.scoring.day_batches, fam.targets):
        if item is None or y is None:
            continue
        _, batch = item
        if date_i <= fold.train_end:
            train_x.append(batch)
            train_y.append(y)
        elif date_i <= fold.val_end:
            val_x.append(batch)
            val_y.append(y)
    if not train_x:
        raise ValueError(f"{fold.test_year} 폴드: 학습 구간에 표본이 없습니다")
    empty = torch.empty(0)
    return (
        torch.cat(train_x),
        torch.cat(train_y),
        torch.cat(val_x) if val_x else empty,
        torch.cat(val_y) if val_y else empty,
    )


# ──────────────────────────────────────────────────────────────
# 폴드 하나
# ──────────────────────────────────────────────────────────────


def _evaluate(
    name: str, walk: nn_selection._WalkData, scores: nn_search.DayScores, cost: float, fold: Fold
) -> tuple[selection.Top5Summary, selection.Top5Summary, list[selection.Top5Point]]:
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
    return _split(result, fold)


def _train_nn(
    fam: _Family,
    in_dim: int,
    tensors: tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor],
    arch: str,
    hidden: int,
    dropout: float,
    weight_decay: float,
    lr: float,
    seed: int,
    max_epochs: int,
) -> tuple[nn_search.DayScores, dict]:
    x_train, y_train, x_val, y_val = tensors
    nn_selection._set_seed(seed)
    model = nn_search.build_model(arch, in_dim, hidden, dropout)
    model, loss_curve, best_epoch = nn_selection._train_model(
        model,
        x_train,
        y_train,
        x_val,
        y_val,
        lr=lr,
        weight_decay=weight_decay,
        max_epochs=max_epochs,
        patience=nn_search.PATIENCE,
    )
    info = {"best_epoch": best_epoch, "epochs": len(loss_curve)}
    return nn_search.score_days(fam.scoring, model), info


def _pick_best_by_val(candidates: dict[str, dict]) -> str:
    """Val 샤프가 가장 좋은 후보 이름 — 시험(OOS) 값은 보지 않습니다."""
    return max(candidates, key=lambda k: candidates[k]["val"]["sharpe"])


def run_fold(
    fold: Fold,
    walk: nn_selection._WalkData,
    families: dict[str, _Family],
    rule_results: dict[str, selection.Top5Result],
    cost: float,
    *,
    ml_keys: tuple[str, ...] | None = None,
    nn_archs: tuple[str, ...] | None = None,
    winner_seeds: tuple[int, ...] = nn_search.ENSEMBLE_SEEDS,
    max_epochs: int = nn_search.MAX_EPOCHS,
) -> dict:
    """폴드 하나의 모든 세대를 처음부터 학습·채점해 요약과 시험 구간 일별 수익률을 돌려줍니다."""
    ml_keys = ml_keys if ml_keys is not None else tuple(ml_selection.MODEL_REGISTRY)
    nn_archs = nn_archs if nn_archs is not None else tuple(nn_selection.NN_MODEL_REGISTRY)
    in_dim = nn_selection.feature_dim(FEATURE_SET)
    started = time.time()
    series: dict[str, dict] = {}

    # ── 룰베이스: 학습이 없으므로 전체기간 백테스트를 구간만 쪼갭니다 ──
    rule_candidates: dict[str, dict] = {}
    rule_points: dict[str, list[selection.Top5Point]] = {}
    rule_test: dict[str, selection.Top5Summary] = {}
    for strategy, result in rule_results.items():
        val_s, test_s, pts = _split(result, fold)
        rule_candidates[strategy] = {"val": _summary_dict(val_s), "test": _summary_dict(test_s)}
        rule_points[strategy] = pts
        rule_test[strategy] = test_s
    rule_pick = _pick_best_by_val(rule_candidates)
    series["rule_fixed"] = _series_entry(rule_points[FIXED_RULE])
    series["rule_refit"] = _series_entry(rule_points[rule_pick])
    series["benchmark"] = _series_entry(rule_points[rule_pick], benchmark=True)

    # ── 전통ML 4종: 학습 구간으로만 학습, Val 샤프로 선택 ──
    flat = families["A"]
    flat_tensors = _fold_tensors(flat, fold)
    ml_candidates: dict[str, dict] = {}
    ml_points: dict[str, list[selection.Top5Point]] = {}
    for key in ml_keys:
        spec = ml_selection.MODEL_REGISTRY[key]
        model = spec.build()
        model.fit(flat_tensors[0].numpy(), flat_tensors[1].numpy())
        scores: nn_search.DayScores = []
        for item in flat.scoring.day_batches:
            if item is None:
                scores.append({})
                continue
            tickers, batch = item
            scores.append({t: float(p) for t, p in zip(tickers, model.predict(batch.numpy()))})
        val_s, test_s, pts = _evaluate(spec.label, walk, scores, cost, fold)
        ml_candidates[key] = {"label": spec.label, "val": _summary_dict(val_s), "test": _summary_dict(test_s)}
        ml_points[key] = pts
        nn_search._log(event="wf_ml", fold=fold.test_year, model=key, val_sharpe=round(val_s.sharpe, 3), test_sharpe=round(test_s.sharpe, 3))
    ml_pick = _pick_best_by_val(ml_candidates)
    series["traditional_ml_refit"] = _series_entry(ml_points[ml_pick])

    # ── 신경망 5종(기본 설정): 학습 구간으로 학습, Val 로 얼리스토핑·선택 ──
    nn_candidates: dict[str, dict] = {}
    nn_points: dict[str, list[selection.Top5Point]] = {}
    for arch in nn_archs:
        family = nn_selection.NN_MODEL_REGISTRY[arch].family
        fam = families[family]
        tensors = flat_tensors if family == "A" else _fold_tensors(fam, fold)
        t0 = time.time()
        scores, info = _train_nn(
            fam, in_dim, tensors, arch, DEFAULT_HIDDEN, DEFAULT_DROPOUT, DEFAULT_WEIGHT_DECAY, DEFAULT_LR, DEFAULT_SEED, max_epochs
        )
        val_s, test_s, pts = _evaluate(arch, walk, scores, cost, fold)
        nn_candidates[arch] = {
            "label": nn_selection.NN_MODEL_REGISTRY[arch].label,
            "val": _summary_dict(val_s),
            "test": _summary_dict(test_s),
            **info,
        }
        nn_points[arch] = pts
        nn_search._log(
            event="wf_nn", fold=fold.test_year, arch=arch, val_sharpe=round(val_s.sharpe, 3),
            test_sharpe=round(test_s.sharpe, 3), seconds=round(time.time() - t0), **info,
        )
    nn_pick = _pick_best_by_val(nn_candidates)
    series["neural_net_refit"] = _series_entry(nn_points[nn_pick])

    # ── 탐색 최고 설정: GRU(64 · 0.4) 시드 5개 순위평균 — 설정은 고정, 가중치는 폴드마다 새로 ──
    fam_w = families[nn_selection.NN_MODEL_REGISTRY[WINNER_ARCH].family]
    tensors_w = flat_tensors if fam_w is flat else _fold_tensors(fam_w, fold)
    member_scores: list[nn_search.DayScores] = []
    for seed in winner_seeds:
        t0 = time.time()
        scores, info = _train_nn(
            fam_w, in_dim, tensors_w, WINNER_ARCH, WINNER_HIDDEN, WINNER_DROPOUT, WINNER_WEIGHT_DECAY, WINNER_LR, seed, max_epochs
        )
        member_scores.append(scores)
        nn_search._log(event="wf_winner_member", fold=fold.test_year, seed=seed, seconds=round(time.time() - t0), **info)
    ens = nn_search.rank_average(member_scores) if len(member_scores) > 1 else member_scores[0]
    w_val, w_test, w_pts = _evaluate("winner_config", walk, ens, cost, fold)
    series["winner_config"] = _series_entry(w_pts)

    bench = rule_test[rule_pick]  # 벤치마크 값은 전략과 무관(같은 날짜·같은 비용)이라 어느 것이든 같습니다
    return {
        "test_year": fold.test_year,
        "train_end": fold.train_end.isoformat(),
        "val_end": fold.val_end.isoformat(),
        "test_end": fold.test_end.isoformat(),
        "cost": cost,
        "train_rows": int(flat_tensors[0].shape[0]),
        "val_rows": int(flat_tensors[2].shape[0]),
        "seconds": round(time.time() - started),
        "rule_based": {"candidates": rule_candidates, "fixed": FIXED_RULE, "refit_pick": rule_pick},
        "traditional_ml": {"candidates": ml_candidates, "pick": ml_pick},
        "neural_net": {"candidates": nn_candidates, "pick": nn_pick},
        "winner_config": {"val": _summary_dict(w_val), "test": _summary_dict(w_test), "seeds": list(winner_seeds)},
        "benchmark": {
            "test_sharpe": bench.universe_sharpe,
            "test_cumulative_return_pct": bench.final_universe,
            "test_mdd_pct": bench.universe_mdd,
        },
        "series": series,
    }


# ──────────────────────────────────────────────────────────────
# 폴드 이어 붙이기 — 시험 구간만으로 하나의 표본밖 곡선을 만듭니다
# ──────────────────────────────────────────────────────────────


def stitch(fold_results: list[dict]) -> dict:
    """폴드별 시험 구간 일별 수익률을 시간순으로 이어, 계열마다 통합 지표를 계산합니다.

    통합 곡선은 폴드 사이에 리셋 없이 복리로 이어 붙입니다(시험 구간끼리는 겹치지 않고 빈틈도
    없습니다). 샤프는 이어 붙인 일별 수익률 전체로 계산하고, 폴드별 샤프는 따로 남깁니다.
    """
    out: dict[str, dict] = {}
    for name in SERIES_NAMES:
        dates: list[str] = []
        returns: list[float] = []
        per_fold: list[dict] = []
        for fr in fold_results:
            entry = fr["series"][name]
            dates.extend(entry["dates"])
            returns.extend(entry["returns"])
            r = entry["returns"]
            cum = 1.0
            for x in r:
                cum *= 1 + x
            per_fold.append(
                {
                    "test_year": fr["test_year"],
                    "sharpe": metrics.sharpe_ratio(r),
                    "cumulative_return_pct": (cum - 1) * 100,
                    "nights": len(r),
                }
            )
        cum_pct: list[float] = []
        cum = 1.0
        for x in returns:
            cum *= 1 + x
            cum_pct.append((cum - 1) * 100)
        final = cum_pct[-1] if cum_pct else 0.0
        mdd = metrics.max_drawdown(cum_pct) if cum_pct else 0.0
        fold_sharpes = [f["sharpe"] for f in per_fold]
        out[name] = {
            "label": SERIES_LABELS[name],
            "sharpe": metrics.sharpe_ratio(returns),
            "cumulative_return_pct": final,
            "mdd_pct": mdd,
            "calmar": metrics.calmar_ratio(final, mdd),
            "cagr_pct": metrics.cagr(final, len(returns)) if returns else 0.0,
            "nights": len(returns),
            "per_fold": per_fold,
            "fold_sharpe_mean": sum(fold_sharpes) / len(fold_sharpes) if fold_sharpes else 0.0,
            "folds_beating_benchmark": None,  # 아래에서 채웁니다
            "dates": dates,
            "cum_pct": cum_pct,
        }
    bench = out["benchmark"]["per_fold"]
    for name in SERIES_NAMES:
        if name == "benchmark":
            continue
        out[name]["folds_beating_benchmark"] = sum(
            1 for f, b in zip(out[name]["per_fold"], bench) if f["sharpe"] > b["sharpe"]
        )
    return out


# ──────────────────────────────────────────────────────────────
# 전체 실행 — 폴드마다 파일에 저장해 끊겨도 이어서 돕니다
# ──────────────────────────────────────────────────────────────


def _fold_path(cache_dir: Path, year: int) -> Path:
    return cache_dir / f"walkforward_fold_{year}.json"


def _load_cached(cache_dir: Path, fold: Fold, cost: float) -> dict | None:
    path = _fold_path(cache_dir, fold.test_year)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("cost") != cost or data.get("train_end") != fold.train_end.isoformat():
        return None
    return data


def run_walkforward(
    universe: dict[str, list[Candle]],
    cost: float = nn_selection.DEFAULT_COST,
    test_years: tuple[int, ...] = TEST_YEARS,
    cache_dir: Path = CACHE_DIR,
    *,
    fresh: bool = False,
    ml_keys: tuple[str, ...] | None = None,
    nn_archs: tuple[str, ...] | None = None,
    winner_seeds: tuple[int, ...] = nn_search.ENSEMBLE_SEEDS,
    max_epochs: int = nn_search.MAX_EPOCHS,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    folds = make_folds(test_years)

    walk = nn_selection._walk_all(universe, FEATURE_SET)
    if not walk.all_dates:
        raise ValueError("채점할 날짜가 없습니다")
    in_dim = nn_selection.feature_dim(FEATURE_SET)
    families = {"A": _prepare_family(walk, "A", in_dim), "B": _prepare_family(walk, "B", in_dim)}

    # 룰베이스 8대 전략은 한 번만 전체기간으로 돌리고 폴드마다 구간만 쪼갭니다
    rule_results = {s: selection.backtest_top_k(universe, s, k=nn_selection.FIXED_K, cost=cost) for s in selection.STRATEGIES}
    for s, r in rule_results.items():
        if len(r.points) != len(walk.all_dates) - 1:
            raise ValueError(f"룰베이스 {s} 의 날짜축이 신경망 입력과 다릅니다")
    nn_search._log(event="wf_ready", tickers=len(universe), days=len(walk.all_dates), folds=[f.test_year for f in folds])

    results: list[dict] = []
    for fold in folds:
        cached = None if fresh else _load_cached(cache_dir, fold, cost)
        if cached is not None:
            nn_search._log(event="wf_fold_cached", fold=fold.test_year)
            results.append(cached)
            continue
        # 시험 구간에 거래일이 하나도 없으면(데이터가 거기까지 없으면) 건너뜁니다
        probe = next(iter(rule_results.values()))
        if not any(fold.val_end < p.time <= fold.test_end for p in probe.points):
            nn_search._log(event="wf_fold_skipped", fold=fold.test_year, reason="시험 구간에 데이터 없음")
            continue
        result = run_fold(
            fold, walk, families, rule_results, cost,
            ml_keys=ml_keys, nn_archs=nn_archs, winner_seeds=winner_seeds, max_epochs=max_epochs,
        )
        _fold_path(cache_dir, fold.test_year).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        nn_search._log(event="wf_fold_done", fold=fold.test_year, seconds=result["seconds"])
        results.append(result)

    if not results:
        raise ValueError("실행된 폴드가 없습니다")
    return {
        "finished_at": datetime.now().isoformat(),
        "protocol": {
            "scheme": "연도별 확장 윈도우 Walk-Forward (방식 B)",
            "fold": "학습 = 처음 ~ Y-2 말 · Val = Y-1 · 시험 = Y",
            "k": nn_selection.FIXED_K,
            "cost": cost,
            "feature_set": FEATURE_SET,
            "n_features": in_dim,
            "winner_config": {
                "arch": WINNER_ARCH, "hidden": WINNER_HIDDEN, "dropout": WINNER_DROPOUT,
                "weight_decay": WINNER_WEIGHT_DECAY, "lr": WINNER_LR, "seeds": list(winner_seeds),
            },
            "caveat": "탐색 최고 설정은 방식 A 의 Val(2024)·OOS(2025~)를 본 뒤 정해진 값이라 폴드 2023~2026 에서는 완전한 표본밖이 아닙니다",
        },
        "folds": [{k: v for k, v in r.items() if k != "series"} for r in results],
        "stitched": stitch(results),
    }


async def _main() -> None:
    from . import db
    from .main import _fetch_universe_candles

    fresh = "--fresh" in sys.argv
    await db.connect()
    try:
        async with db.acquire() as conn:
            universe = await _fetch_universe_candles(conn, days=3650, start=None, end=None)
    finally:
        await db.disconnect()
    nn_search._log(event="universe", tickers=len(universe))

    report = await asyncio.to_thread(lambda: run_walkforward(universe, fresh=fresh))
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    nn_search._log(event="done", path=str(RESULT_PATH))


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
