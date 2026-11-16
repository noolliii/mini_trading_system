"""Week 10 — 뉴럴 네트워크(MLP·1D-CNN·LSTM·GRU·Transformer)로 다음날 밤수익률을
예측해 Top-5 종목을 고르고, 룰베이스 최고 전략 · 전통 ML 최고 모델과 같은
Train(IS) / Val / Out-of-Sample(OOS) 구간에서 공정 비교하는 모듈입니다.

전원 통일 실험 규격(9주차 해설서 기준)을 그대로 따릅니다.
  - 구간: Train 2020~2023(IS 성과 측정) · Val 2024(얼리스토핑 전용) ·
    OOS 2025~2026.10(표본밖 성과 측정). 날짜 고정 — 학생마다 비율이 다르면
    통합분석이 공정하지 않습니다.
  - 매매: Top-5 동일비중 고정(best-K 탐색 아님 — selection.aggregate_top_k 로
    K 를 고정해서 집계합니다. 기존 best_k_result/8대 전략 리더보드는 그대로 둡니다).
  - 누수 방지: 스케일러를 fit 하지 않고, 그날 유니버스 안에서 횡단면 백분위
    순위(-0.5~+0.5)로 변환합니다 — 미래 날짜를 한 번도 보지 않으므로 구조적으로
    안전합니다.
  - 타깃: 그날 밤 실현수익률에서 그날 유니버스 평균 밤수익률을 뺀 시장중립
    초과수익률(y_excess)을 씁니다.

가중치는 /tmp/nn-models/ 에만 남깁니다(kis-tokens·ml-models 와 같은 패턴 —
볼륨도 git 도 아닙니다). 전통 ML 비교용 재학습은 운영용 /tmp/ml-models 를
건드리지 않도록 별도 경로(/tmp/ml-models-is/)에 저장합니다.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import asyncpg
import joblib
import torch
from torch import nn

from . import ml_selection, selection
from .schemas import Candle

# ──────────────────────────────────────────────────────────────
# 전원 통일 실험 규격 — 고정값
# ──────────────────────────────────────────────────────────────
#   daily_candles.time 는 TIMESTAMPTZ(거래일 00:00 Asia/Seoul)라 asyncpg 가 tz-aware
#   datetime 으로 돌려줍니다 — naive datetime 과 비교하면 TypeError 가 나므로 UTC로
#   맞춥니다. 경계가 연 단위라 KST/UTC 9시간 차이가 연도 판정을 흔들지 않습니다.
TRAIN_END = datetime(2023, 12, 31, tzinfo=timezone.utc)
VAL_END = datetime(2024, 12, 31, tzinfo=timezone.utc)
SEQ_LEN = 20  # 계열 B(시계열) 입력 창 — 최근 20거래일
FIXED_K = 5  # Top-5 고정(best-K 탐색 아님)
DEFAULT_COST = 0.001  # 왕복 10bp

F_DIM = 8  # factors.py 파생 피처 개수 (ml_selection.FEATURE_NAMES 와 동일)

NN_MODEL_DIR = Path("/tmp/nn-models")
ML_COMPARE_MODEL_DIR = Path("/tmp/ml-models-is")  # 운영용 /tmp/ml-models 와 분리된 비교 전용 경로

_SEED = 42


def _set_seed(seed: int = _SEED) -> None:
    random.seed(seed)
    torch.manual_seed(seed)


# ──────────────────────────────────────────────────────────────
# 횡단면 정규화 — 날짜별로 그날 유니버스 안에서만 순위를 매깁니다(미래 비참조)
# ──────────────────────────────────────────────────────────────


def _rank_pct(values: dict[str, float]) -> dict[str, float]:
    """같은 날짜 안에서 값을 백분위 순위(-0.5~+0.5)로 바꿉니다. 동점은 평균 순위.

    종목이 하나뿐이면 비교 대상이 없어 0.0 으로 둡니다.
    """
    n = len(values)
    if n <= 1:
        return {t: 0.0 for t in values}

    sorted_items = sorted(values.items(), key=lambda kv: kv[1])
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_items[j + 1][1] == sorted_items[i][1]:
            j += 1
        avg_rank = (i + j) / 2
        for idx in range(i, j + 1):
            ranks[sorted_items[idx][0]] = avg_rank
        i = j + 1
    return {t: (ranks[t] / (n - 1)) - 0.5 for t in values}


def _cross_sectional_normalize(day_features: dict[str, list[float]]) -> dict[str, list[float]]:
    """그날 유니버스 안에서 피처 컬럼마다 따로 백분위 순위를 매깁니다.

    StandardScaler 를 전체 기간에 fit 하면 미래 평균·분산이 과거로 새어 들어가는데,
    이 함수는 그날 하루치 횡단면만 보므로 구조적으로 누수가 불가능합니다.
    """
    tickers = list(day_features.keys())
    if not tickers:
        return {}
    n_features = len(day_features[tickers[0]])
    ranked_columns: list[dict[str, float]] = []
    for col in range(n_features):
        values = {t: day_features[t][col] for t in tickers}
        ranked_columns.append(_rank_pct(values))
    return {t: [ranked_columns[col][t] for col in range(n_features)] for t in tickers}


# ──────────────────────────────────────────────────────────────
# 하루씩 걷기 — ml_selection._walk 의 원시 피처를 그대로 재사용해 정규화·타깃·
# 시퀀스 윈도우를 한 번에 만듭니다(미래참조 없는 전진 루프, 한 번만 계산).
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _WalkData:
    all_dates: list[datetime]  # ml_selection._walk 반환 그대로(선택 집계 함수가 요구하는 모양)
    dates_scored: list[datetime]  # all_dates[:-1] — 실제로 점수를 매기는 날
    norm_features: list[dict[str, list[float]]]  # 계열 A 입력(당일 스냅샷, 정규화됨)
    seq_features: list[dict[str, list[list[float]]]]  # 계열 B 입력(최근 SEQ_LEN일 정규화 시퀀스)
    excess_targets: list[dict[str, float]]  # 시장중립 초과수익률(학습 타깃)
    realized_per_day: list[dict[str, float]]  # 원시 밤수익률(집계·채점용)
    universe_returns: list[float]  # 그날 유니버스 평균 밤수익률


def _walk_all(universe: dict[str, list[Candle]]) -> _WalkData:
    all_dates, features_per_day, realized_per_day = ml_selection._walk(universe)
    if not all_dates:
        return _WalkData([], [], [], [], [], [], [])

    norm_features: list[dict[str, list[float]]] = []
    seq_features: list[dict[str, list[list[float]]]] = []
    excess_targets: list[dict[str, float]] = []
    universe_returns: list[float] = []
    ticker_hist: dict[str, list[list[float]]] = {}

    for day_features, realized in zip(features_per_day, realized_per_day):
        norm = _cross_sectional_normalize(day_features)
        norm_features.append(norm)

        mean_r = sum(realized.values()) / len(realized) if realized else 0.0
        universe_returns.append(mean_r)
        excess_targets.append({t: r - mean_r for t, r in realized.items()})

        seq_today: dict[str, list[list[float]]] = {}
        for ticker, fv in norm.items():
            hist = ticker_hist.setdefault(ticker, [])
            hist.append(fv)
            if len(hist) >= SEQ_LEN:
                seq_today[ticker] = [row[:] for row in hist[-SEQ_LEN:]]
        seq_features.append(seq_today)

    return _WalkData(
        all_dates=all_dates,
        dates_scored=all_dates[:-1],
        norm_features=norm_features,
        seq_features=seq_features,
        excess_targets=excess_targets,
        realized_per_day=realized_per_day,
        universe_returns=universe_returns,
    )


def _collect_family_a(
    walk: _WalkData, *, after: datetime | None = None, upto: datetime | None = None
) -> tuple[list[list[float]], list[float]]:
    X: list[list[float]] = []
    y: list[float] = []
    for date_i, feats, targets in zip(walk.dates_scored, walk.norm_features, walk.excess_targets):
        if after is not None and date_i <= after:
            continue
        if upto is not None and date_i > upto:
            continue
        for ticker, fv in feats.items():
            if ticker in targets:
                X.append(fv)
                y.append(targets[ticker])
    return X, y


def _collect_family_b(
    walk: _WalkData, *, after: datetime | None = None, upto: datetime | None = None
) -> tuple[list[list[list[float]]], list[float]]:
    X: list[list[list[float]]] = []
    y: list[float] = []
    for date_i, feats, targets in zip(walk.dates_scored, walk.seq_features, walk.excess_targets):
        if after is not None and date_i <= after:
            continue
        if upto is not None and date_i > upto:
            continue
        for ticker, seq in feats.items():
            if ticker in targets:
                X.append(seq)
                y.append(targets[ticker])
    return X, y


def _score_all(
    walk: _WalkData, model: nn.Module, family: str
) -> tuple[list[list[tuple[str, float]]], list[dict[str, float]], list[float]]:
    """학습된 모델로 전체 연속 기간(Train+Val+OOS)을 끊김없이 채점합니다.

    ml_selection._score_with_model 과 같은 모양(all_dates 는 호출부가 이미 들고
    있으므로 빼고) — selection.aggregate_top_k 에 그대로 넘길 수 있습니다.
    """
    model.eval()
    feats_per_day = walk.norm_features if family == "A" else walk.seq_features
    sorted_candidates: list[list[tuple[str, float]]] = []
    with torch.no_grad():
        for feats in feats_per_day:
            if not feats:
                sorted_candidates.append([])
                continue
            tickers = list(feats.keys())
            batch = torch.tensor([feats[t] for t in tickers], dtype=torch.float32)
            preds = model(batch).tolist()
            candidates = sorted(zip(tickers, preds), key=lambda x: x[1], reverse=True)
            sorted_candidates.append(candidates)
    return sorted_candidates, walk.realized_per_day, walk.universe_returns


# ──────────────────────────────────────────────────────────────
# PyTorch 아키텍처 5종 — 계열 A(당일 스냅샷) · 계열 B(최근 20일 시퀀스)
# ──────────────────────────────────────────────────────────────


class MlpScorer(nn.Module):
    """계열 A — 당일 스냅샷 (N, F) 만 봅니다. 시퀀스 입력이 아닙니다."""

    def __init__(self, hidden: int = 32, dropout: float = 0.3) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(F_DIM, hidden), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(hidden, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (B, F)
        return self.net(x).squeeze(-1)


class Cnn1dScorer(nn.Module):
    """계열 B — 최근 SEQ_LEN일 (N, L, F) 궤적을 시간축으로 합성곱합니다."""

    def __init__(self, hidden: int = 32, dropout: float = 0.3) -> None:
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(F_DIM, hidden, kernel_size=3, padding=1), nn.ReLU(), nn.Dropout(dropout),
            nn.Conv1d(hidden, hidden, kernel_size=3, padding=1), nn.ReLU(),
        )
        self.pool = nn.AdaptiveAvgPool1d(1)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (B, L, F)
        h = x.transpose(1, 2)  # (B, F, L)
        h = self.conv(h)
        h = self.pool(h).squeeze(-1)  # (B, hidden)
        return self.head(h).squeeze(-1)


class RecurrentScorer(nn.Module):
    """계열 B — LSTM 또는 GRU 로 (N, L, F) 궤적의 마지막 은닉상태를 씁니다."""

    def __init__(self, cell: str, hidden: int = 32, dropout: float = 0.3) -> None:
        super().__init__()
        rnn_cls = nn.LSTM if cell == "lstm" else nn.GRU
        self.rnn = rnn_cls(input_size=F_DIM, hidden_size=hidden, batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (B, L, F)
        out, _ = self.rnn(x)
        last = out[:, -1, :]
        return self.head(self.dropout(last)).squeeze(-1)


class TransformerScorer(nn.Module):
    """계열 B — Self-Attention 으로 (N, L, F) 궤적 전체를 한 번에 봅니다."""

    def __init__(
        self, seq_len: int = SEQ_LEN, hidden: int = 32, dropout: float = 0.3, nhead: int = 2, layers: int = 2
    ) -> None:
        super().__init__()
        self.proj = nn.Linear(F_DIM, hidden)
        self.pos = nn.Parameter(torch.zeros(seq_len, hidden))
        nn.init.normal_(self.pos, std=0.02)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden, nhead=nhead, dim_feedforward=hidden * 2, dropout=dropout, batch_first=True
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=layers)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:  # x: (B, L, F)
        h = self.proj(x) + self.pos.unsqueeze(0)
        h = self.encoder(h)
        pooled = h.mean(dim=1)
        return self.head(pooled).squeeze(-1)


@dataclass(frozen=True)
class NnModelSpec:
    label: str
    family: str  # "A"(당일 스냅샷) | "B"(20일 시퀀스)
    build: Callable[[], nn.Module]
    hyperparams: dict


NN_MODEL_REGISTRY: dict[str, NnModelSpec] = {
    "mlp": NnModelSpec("MLP", "A", lambda: MlpScorer(), {"hidden_dim": 32, "dropout": 0.3}),
    "cnn1d": NnModelSpec("1D-CNN", "B", lambda: Cnn1dScorer(), {"hidden_dim": 32, "dropout": 0.3}),
    "lstm": NnModelSpec("LSTM", "B", lambda: RecurrentScorer("lstm"), {"hidden_dim": 32, "dropout": 0.3}),
    "gru": NnModelSpec("GRU", "B", lambda: RecurrentScorer("gru"), {"hidden_dim": 32, "dropout": 0.3}),
    "transformer": NnModelSpec(
        "Transformer", "B", lambda: TransformerScorer(), {"hidden_dim": 32, "dropout": 0.3, "nhead": 2, "layers": 2}
    ),
}


# ──────────────────────────────────────────────────────────────
# 학습 — HuberLoss · AdamW(weight_decay) · Val 기준 얼리스토핑 · 시드 고정
# ──────────────────────────────────────────────────────────────


def _train_model(
    model: nn.Module,
    X_train: list,
    y_train: list[float],
    X_val: list,
    y_val: list[float],
    *,
    lr: float = 1e-3,
    weight_decay: float = 1e-3,
    batch_size: int = 512,
    max_epochs: int = 40,
    patience: int = 5,
) -> tuple[nn.Module, list[dict], int]:
    Xt = torch.tensor(X_train, dtype=torch.float32)
    yt = torch.tensor(y_train, dtype=torch.float32)
    has_val = len(X_val) > 0
    Xv = torch.tensor(X_val, dtype=torch.float32) if has_val else None
    yv = torch.tensor(y_val, dtype=torch.float32) if has_val else None

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    loss_fn = nn.HuberLoss()

    n = Xt.shape[0]
    best_val = float("inf")
    best_state = {k: v.clone() for k, v in model.state_dict().items()}
    best_epoch = 0
    stale_epochs = 0
    loss_curve: list[dict] = []

    for epoch in range(1, max_epochs + 1):
        model.train()
        perm = torch.randperm(n)
        total_loss = 0.0
        for start in range(0, n, batch_size):
            idx = perm[start : start + batch_size]
            xb, yb = Xt[idx], yt[idx]
            opt.zero_grad()
            pred = model(xb)
            loss = loss_fn(pred, yb)
            loss.backward()
            opt.step()
            total_loss += loss.item() * len(idx)
        train_loss = total_loss / n

        model.eval()
        with torch.no_grad():
            # Val 표본이 없는 극소 합성 데이터(스모크 테스트)에서는 train_loss 로 대신합니다 —
            # 실제 서비스 데이터(수년치)에서는 항상 Val 구간에 표본이 있습니다.
            val_loss = loss_fn(model(Xv), yv).item() if has_val else train_loss

        loss_curve.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})

        if val_loss < best_val - 1e-6:
            best_val = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch
            stale_epochs = 0
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                break

    model.load_state_dict(best_state)
    return model, loss_curve, best_epoch


@dataclass(frozen=True)
class NnResult:
    model_key: str
    model_label: str
    family: str
    k: int
    param_count: int
    hyperparams: dict
    loss_curve: list[dict]
    is_summary: selection.Top5Summary
    is_points: list[selection.Top5Point]
    val_summary: selection.Top5Summary
    val_points: list[selection.Top5Point]
    oos_summary: selection.Top5Summary
    oos_points: list[selection.Top5Point]


def train_and_score(
    universe: dict[str, list[Candle]],
    model_key: str,
    train_end: datetime = TRAIN_END,
    val_end: datetime = VAL_END,
    cost: float = DEFAULT_COST,
    k: int = FIXED_K,
    max_epochs: int = 40,
    patience: int = 5,
) -> NnResult:
    """model_key 하나를 Train 구간만으로 학습(Val 은 얼리스토핑에만 사용)하고,
    전체 연속 기간을 채점해 Train(IS)/Val/OOS 3구간 성과를 돌려줍니다.
    """
    if model_key not in NN_MODEL_REGISTRY:
        raise ValueError(f"알 수 없는 신경망 모델입니다: {model_key}")
    spec = NN_MODEL_REGISTRY[model_key]

    walk = _walk_all(universe)
    if not walk.all_dates:
        raise ValueError("학습할 데이터가 부족합니다 — 20일 이상 역사가 쌓인 날이 더 필요합니다")

    if spec.family == "A":
        X_train, y_train = _collect_family_a(walk, upto=train_end)
        X_val, y_val = _collect_family_a(walk, after=train_end, upto=val_end)
    else:
        X_train, y_train = _collect_family_b(walk, upto=train_end)
        X_val, y_val = _collect_family_b(walk, after=train_end, upto=val_end)

    if len(X_train) < 30:
        raise ValueError("학습할 데이터가 부족합니다 — Train 구간에 20일 이상 역사가 쌓인 표본이 더 필요합니다")

    _set_seed()
    model = spec.build()
    model, loss_curve, best_epoch = _train_model(
        model, X_train, y_train, X_val, y_val, max_epochs=max_epochs, patience=patience
    )

    sorted_candidates, realized_per_day, universe_returns = _score_all(walk, model, spec.family)
    result = selection.aggregate_top_k(
        spec.label, walk.all_dates, sorted_candidates, realized_per_day, universe_returns, k=k, cost=cost
    )
    (is_summary, is_points), (val_summary, val_points), (oos_summary, oos_points) = selection.summarize_periods(
        result, [train_end, val_end]
    )

    param_count = sum(p.numel() for p in model.parameters())
    hyperparams = {
        **spec.hyperparams,
        "weight_decay": 1e-3,
        "lr": 1e-3,
        "batch_size": 512,
        "max_epochs": max_epochs,
        "patience": patience,
        "seq_len": SEQ_LEN if spec.family == "B" else 1,
        "early_stopped_epoch": best_epoch,
        "seed": _SEED,
        "loss_fn": "HuberLoss",
        "optimizer": "AdamW",
    }

    path = NN_MODEL_DIR / f"{model_key}.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), path)
    os.chmod(path, 0o600)

    return NnResult(
        model_key=model_key,
        model_label=spec.label,
        family=spec.family,
        k=k,
        param_count=param_count,
        hyperparams=hyperparams,
        loss_curve=loss_curve,
        is_summary=is_summary,
        is_points=is_points,
        val_summary=val_summary,
        val_points=val_points,
        oos_summary=oos_summary,
        oos_points=oos_points,
    )


def train_all_nn_and_compare(
    universe: dict[str, list[Candle]],
    cost: float = DEFAULT_COST,
    max_epochs: int = 40,
    patience: int = 5,
) -> dict[str, NnResult]:
    """NN_MODEL_REGISTRY 5종을 전부 순서대로 학습·채점합니다."""
    return {
        key: train_and_score(universe, key, cost=cost, max_epochs=max_epochs, patience=patience)
        for key in NN_MODEL_REGISTRY
    }


# ──────────────────────────────────────────────────────────────
# 룰베이스 최고 · 전통 ML 최고 — 같은 Train(IS)/Val/OOS 구간으로 공정 비교 기준선을 만듭니다
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GenerationChoice:
    generation: str  # "rule_based" | "traditional_ml" | "neural_net"
    model_key: str
    model_label: str
    k: int
    is_summary: selection.Top5Summary
    is_points: list[selection.Top5Point]
    val_summary: selection.Top5Summary
    val_points: list[selection.Top5Point]
    oos_summary: selection.Top5Summary
    oos_points: list[selection.Top5Point]


def select_best_rule_based(
    universe: dict[str, list[Candle]],
    cost: float = DEFAULT_COST,
    train_end: datetime = TRAIN_END,
    val_end: datetime = VAL_END,
    k: int = FIXED_K,
) -> GenerationChoice:
    """8대 전략을 전부 Top-5 고정으로 돌려보고, Val 구간 샤프가 가장 좋은 전략을
    대표로 선정합니다. 학습이 없는 전략이라 학습 전/후 구분이 없고, 전체기간
    백테스트 결과를 사후에 구간만 쪼갭니다(선정 자체도 OOS 를 들여다보지 않습니다
    — IS 가 아니라 Val 로 고르는 이유이기도 합니다).
    """
    candidates = []
    for strategy in selection.STRATEGIES:
        result = selection.backtest_top_k(universe, strategy, k=k, cost=cost)
        if not result.points:
            continue
        (is_s, is_p), (val_s, val_p), (oos_s, oos_p) = selection.summarize_periods(result, [train_end, val_end])
        candidates.append((strategy, is_s, is_p, val_s, val_p, oos_s, oos_p))

    if not candidates:
        raise ValueError("채점할 날짜가 없습니다 — 20일 이상 역사가 쌓인 날이 더 필요합니다")

    strategy, is_s, is_p, val_s, val_p, oos_s, oos_p = max(candidates, key=lambda c: c[3].sharpe)
    return GenerationChoice("rule_based", strategy, strategy, k, is_s, is_p, val_s, val_p, oos_s, oos_p)


def select_best_traditional_ml(
    universe: dict[str, list[Candle]],
    cost: float = DEFAULT_COST,
    train_end: datetime = TRAIN_END,
    val_end: datetime = VAL_END,
    k: int = FIXED_K,
    model_dir: Path = ML_COMPARE_MODEL_DIR,
) -> GenerationChoice:
    """전통 ML 4종(Ridge/RF/ExtraTrees/GBM)을 Train 구간만으로 재학습(비교 전용 경로 —
    운영용 /tmp/ml-models 는 건드리지 않습니다)하고, 전체 기간을 채점해 구간을 쪼갠 뒤
    Val 샤프가 가장 좋은 모델을 대표로 선정합니다.
    """
    train_universe = {t: [c for c in cs if c.time <= train_end] for t, cs in universe.items()}
    train_universe = {t: cs for t, cs in train_universe.items() if cs}

    candidates = []
    for model_key, spec in ml_selection.MODEL_REGISTRY.items():
        path = model_dir / f"{model_key}.joblib"
        try:
            ml_selection.train_and_save(train_universe, model_key=model_key, model_path=path)
        except ValueError:
            continue  # 표본 부족 — 이번 비교에서만 건너뜁니다

        saved = joblib.load(path)
        model = saved["model"]
        all_dates, sorted_candidates, realized_per_day, universe_returns = ml_selection._score_with_model(
            universe, model
        )
        if not all_dates:
            continue
        result = selection.aggregate_top_k(
            spec.label, all_dates, sorted_candidates, realized_per_day, universe_returns, k=k, cost=cost
        )
        (is_s, is_p), (val_s, val_p), (oos_s, oos_p) = selection.summarize_periods(result, [train_end, val_end])
        candidates.append((model_key, spec.label, is_s, is_p, val_s, val_p, oos_s, oos_p))

    if not candidates:
        raise ValueError("전통 ML 비교 모델을 하나도 학습하지 못했습니다 — Train 구간 표본이 부족합니다")

    model_key, label, is_s, is_p, val_s, val_p, oos_s, oos_p = max(candidates, key=lambda c: c[4].sharpe)
    return GenerationChoice("traditional_ml", model_key, label, k, is_s, is_p, val_s, val_p, oos_s, oos_p)


@dataclass(frozen=True)
class ComparisonReport:
    rule_based: GenerationChoice
    traditional_ml: GenerationChoice
    neural_net_all: dict[str, NnResult]
    neural_net_best_key: str  # NN_MODEL_REGISTRY 키 — 5개 중 Val 샤프 최고


def run_full_comparison(universe: dict[str, list[Candle]], cost: float = DEFAULT_COST) -> ComparisonReport:
    """Week 10 공식 규격(날짜·K 고정)으로 3세대(룰베이스/전통ML/신경망)를 전부 비교합니다.

    Train(IS)/Val/OOS 날짜 경계는 "전원 통일 실험 규격"이라 인자로 받지 않고
    모듈 상수(TRAIN_END·VAL_END)를 고정으로 씁니다 — 학생마다 구간이 다르면
    교수자의 11주차 통합분석이 공정하지 않습니다.
    """
    rule_based = select_best_rule_based(universe, cost=cost)
    traditional_ml = select_best_traditional_ml(universe, cost=cost)
    neural_net_all = train_all_nn_and_compare(universe, cost=cost)
    best_key = max(neural_net_all, key=lambda key: neural_net_all[key].val_summary.sharpe)
    return ComparisonReport(
        rule_based=rule_based,
        traditional_ml=traditional_ml,
        neural_net_all=neural_net_all,
        neural_net_best_key=best_key,
    )


# ──────────────────────────────────────────────────────────────
# DB 저장 — generation·model_key·period 조합마다 한 줄, 재실행 시 덮어씁니다
# ──────────────────────────────────────────────────────────────


def _points_json(points: list[selection.Top5Point]) -> str:
    return json.dumps([{"time": p.time.isoformat(), "cum_portfolio": p.cum_portfolio, "cum_universe": p.cum_universe} for p in points])


async def save_experiments(conn: asyncpg.Connection, report: ComparisonReport, cost: float) -> None:
    rows: list[tuple] = []

    def add(
        generation: str,
        model_key: str,
        model_label: str,
        k: int,
        period: str,
        summary: selection.Top5Summary,
        points: list[selection.Top5Point],
        hyperparams: dict | None = None,
        param_count: int | None = None,
        loss_curve: list[dict] | None = None,
    ) -> None:
        rows.append(
            (
                generation,
                model_key,
                model_label,
                period,
                k,
                cost,
                summary.final_portfolio,
                summary.final_universe,
                summary.win_rate,
                summary.trading_nights,
                summary.mdd,
                summary.sharpe,
                summary.calmar,
                summary.cagr,
                summary.universe_mdd,
                summary.universe_sharpe,
                summary.universe_calmar,
                summary.universe_cagr,
                json.dumps(hyperparams) if hyperparams is not None else None,
                param_count,
                json.dumps(loss_curve) if loss_curve is not None else None,
                _points_json(points),
            )
        )

    rb = report.rule_based
    add(rb.generation, rb.model_key, rb.model_label, rb.k, "is", rb.is_summary, rb.is_points)
    add(rb.generation, rb.model_key, rb.model_label, rb.k, "val", rb.val_summary, rb.val_points)
    add(rb.generation, rb.model_key, rb.model_label, rb.k, "oos", rb.oos_summary, rb.oos_points)

    tm = report.traditional_ml
    add(tm.generation, tm.model_key, tm.model_label, tm.k, "is", tm.is_summary, tm.is_points)
    add(tm.generation, tm.model_key, tm.model_label, tm.k, "val", tm.val_summary, tm.val_points)
    add(tm.generation, tm.model_key, tm.model_label, tm.k, "oos", tm.oos_summary, tm.oos_points)

    for model_key, nn_result in report.neural_net_all.items():
        add(
            "neural_net", model_key, nn_result.model_label, nn_result.k, "is",
            nn_result.is_summary, nn_result.is_points,
            hyperparams=nn_result.hyperparams, param_count=nn_result.param_count, loss_curve=nn_result.loss_curve,
        )
        add(
            "neural_net", model_key, nn_result.model_label, nn_result.k, "val",
            nn_result.val_summary, nn_result.val_points,
            hyperparams=nn_result.hyperparams, param_count=nn_result.param_count, loss_curve=nn_result.loss_curve,
        )
        add(
            "neural_net", model_key, nn_result.model_label, nn_result.k, "oos",
            nn_result.oos_summary, nn_result.oos_points,
            hyperparams=nn_result.hyperparams, param_count=nn_result.param_count, loss_curve=nn_result.loss_curve,
        )

    async with conn.transaction():
        # 트랜잭션 하나로 묶어 모든 줄이 같은 run_at(now())을 공유하게 합니다 —
        # GET /latest 가 generation 별로 "가장 최근 run_at" 하나만 골라야 하는데,
        # is/val/oos 세 줄의 시각이 미세하게 어긋나면 그 판단이 흔들릴 수 있습니다.
        await conn.executemany(
            """
            INSERT INTO model_experiments (
                generation, model_key, model_label, period, k, cost,
                final_portfolio, final_universe, win_rate, trading_nights,
                mdd, sharpe, calmar, cagr,
                universe_mdd, universe_sharpe, universe_calmar, universe_cagr,
                hyperparams, param_count, loss_curve, points
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19::jsonb, $20, $21::jsonb, $22::jsonb
            )
            ON CONFLICT (generation, model_key, period) DO UPDATE SET
                run_at = now(), model_label = excluded.model_label, k = excluded.k, cost = excluded.cost,
                final_portfolio = excluded.final_portfolio, final_universe = excluded.final_universe,
                win_rate = excluded.win_rate, trading_nights = excluded.trading_nights,
                mdd = excluded.mdd, sharpe = excluded.sharpe, calmar = excluded.calmar, cagr = excluded.cagr,
                universe_mdd = excluded.universe_mdd, universe_sharpe = excluded.universe_sharpe,
                universe_calmar = excluded.universe_calmar, universe_cagr = excluded.universe_cagr,
                hyperparams = excluded.hyperparams, param_count = excluded.param_count,
                loss_curve = excluded.loss_curve, points = excluded.points
            """,
            rows,
        )
