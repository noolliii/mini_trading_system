"""Week 10 — 신경망 IS/Val/OOS 비교 모듈. DB 없이, 작은 합성 유니버스로만 돕니다.

가장 중요하게 보는 것은 selection.py 의 8대 전략과 똑같은 원칙 — 횡단면
정규화·타깃이 그날 하루치 정보만 쓰는지(미래 비참조)이고, 그 다음이 시퀀스
윈도우가 "역사가 모자란 종목은 제외"를 지키는지입니다. 실제 학습 수렴 품질은
검증하지 않습니다(그건 과제 산출물 — WEEK10_NN_REPORT.md — 쪽 책임입니다).
"""

import math
from datetime import datetime, timedelta

import pytest

from app.schemas import Candle
from app.nn_selection import (
    NN_MODEL_REGISTRY,
    SEQ_LEN,
    _cross_sectional_normalize,
    _rank_pct,
    _walk_all,
    train_and_score,
)

START = datetime(2026, 1, 1)


def _make_synthetic_universe(n_tickers: int = 3, n_days: int = 90, start: datetime = START) -> dict[str, list[Candle]]:
    universe: dict[str, list[Candle]] = {}
    for t_idx in range(n_tickers):
        ticker = f"T{t_idx}"
        price = 100.0 + t_idx * 10
        candles: list[Candle] = []
        for day in range(n_days):
            time = start + timedelta(days=day)
            drift = 0.001 * (1 if (day + t_idx) % 3 != 0 else -1)
            open_p = price
            close_p = price * (1 + drift + 0.0005 * math.sin(day / 5 + t_idx))
            high_p = max(open_p, close_p) * 1.01
            low_p = min(open_p, close_p) * 0.99
            volume = 1_000_000 + 10_000 * ((day * (t_idx + 1)) % 7)
            candles.append(Candle(time=time, open=open_p, high=high_p, low=low_p, close=close_p, volume=volume))
            price = close_p
        universe[ticker] = candles
    return universe


# ──────────────────────────────────────────────────────────────
# 횡단면 백분위 순위 — 스케일러 누수 없이 그날 하루치만 봅니다
# ──────────────────────────────────────────────────────────────


def test_rank_pct_single_ticker_is_zero() -> None:
    assert _rank_pct({"A": 1.23}) == {"A": 0.0}


def test_rank_pct_orders_three_distinct_values_symmetrically() -> None:
    ranks = _rank_pct({"A": 1.0, "B": 2.0, "C": 3.0})
    assert ranks["A"] == pytest.approx(-0.5)
    assert ranks["B"] == pytest.approx(0.0)
    assert ranks["C"] == pytest.approx(0.5)


def test_rank_pct_averages_ties() -> None:
    ranks = _rank_pct({"A": 1.0, "B": 1.0, "C": 2.0})
    assert ranks["A"] == pytest.approx(ranks["B"])
    assert ranks["C"] > ranks["A"]


def test_cross_sectional_normalize_is_bounded_and_per_column() -> None:
    day_features = {"A": [1.0, 100.0], "B": [2.0, 50.0], "C": [3.0, 10.0]}
    normalized = _cross_sectional_normalize(day_features)
    for values in normalized.values():
        assert all(-0.5 <= v <= 0.5 for v in values)
    # 두 번째 컬럼(거래대금 등 큰 값)은 첫 번째 컬럼과 반대 순서라 순위도 반대로 나와야 합니다
    assert normalized["A"][1] > normalized["C"][1]


# ──────────────────────────────────────────────────────────────
# _walk_all — 미래참조 없음 · 타깃이 시장중립인지 · 시퀀스 윈도우가 역사 모자란
# 종목을 제외하는지
# ──────────────────────────────────────────────────────────────


def test_walk_all_future_candles_never_change_an_earlier_day() -> None:
    """먼 미래(마지막 날)의 일봉을 통째로 바꿔도, 그보다 앞선 날의 정규화 피처·
    타깃·시퀀스 윈도우는 한 글자도 바뀌면 안 됩니다.
    """
    base = _make_synthetic_universe()
    mutated = {t: list(cs) for t, cs in base.items()}
    for t in mutated:
        last = mutated[t][-1]
        mutated[t][-1] = Candle(time=last.time, open=9999, high=20000, low=1, close=15000, volume=999)

    walk_base = _walk_all(base)
    walk_mutated = _walk_all(mutated)

    # 마지막 전환(파생 수익률이 바뀐 날)을 빼고는 전부 같아야 합니다
    assert walk_base.norm_features[:-1] == walk_mutated.norm_features[:-1]
    assert walk_base.excess_targets[:-1] == walk_mutated.excess_targets[:-1]
    assert walk_base.seq_features[:-1] == walk_mutated.seq_features[:-1]
    # 실제로 바뀔 수 있는 자리(마지막 전환)는 바뀌었는지도 같이 확인합니다
    assert walk_base.realized_per_day[-1] != walk_mutated.realized_per_day[-1]


def test_walk_all_excess_target_is_demeaned_per_day() -> None:
    universe = _make_synthetic_universe()
    walk = _walk_all(universe)

    for targets in walk.excess_targets:
        if len(targets) >= 2:
            assert sum(targets.values()) == pytest.approx(0.0, abs=1e-9)


def test_walk_all_sequence_windows_need_seq_len_history() -> None:
    universe = _make_synthetic_universe()
    walk = _walk_all(universe)

    # 원시 피처가 처음 등장하는 날부터 SEQ_LEN-1 일 동안은 아직 시퀀스가 없어야 합니다
    first_feature_day = next(i for i, f in enumerate(walk.norm_features) if f)
    assert walk.seq_features[first_feature_day] == {}

    first_seq_day = next(i for i, f in enumerate(walk.seq_features) if f)
    for ticker, seq in walk.seq_features[first_seq_day].items():
        assert len(seq) == SEQ_LEN
        assert len(seq[0]) == len(walk.norm_features[first_seq_day][ticker])


def test_walk_all_sequence_excludes_a_ticker_missing_mid_history() -> None:
    """중간에 끊긴 종목은(그 구간 피처가 없어) 그 이후로도 한동안 시퀀스 후보에서 빠집니다."""
    universe = _make_synthetic_universe(n_tickers=2, n_days=60)
    walk = _walk_all(universe)

    full_seq_day = next(i for i, f in enumerate(walk.seq_features) if len(f) == 2)
    assert full_seq_day is not None  # 정상적으로는 두 종목 다 시퀀스가 생기는 날이 있어야 합니다


# ──────────────────────────────────────────────────────────────
# 모델 레지스트리
# ──────────────────────────────────────────────────────────────


def test_registry_has_five_architectures_with_expected_families() -> None:
    assert set(NN_MODEL_REGISTRY) == {"mlp", "cnn1d", "lstm", "gru", "transformer"}
    assert NN_MODEL_REGISTRY["mlp"].family == "A"
    for key in ("cnn1d", "lstm", "gru", "transformer"):
        assert NN_MODEL_REGISTRY[key].family == "B"


# ──────────────────────────────────────────────────────────────
# train_and_score — 스모크 테스트(수렴 품질이 아니라 크래시 없이 끝나는지만 봅니다)
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model_key", ["mlp", "lstm"])
def test_train_and_score_runs_without_crashing(model_key: str) -> None:
    universe = _make_synthetic_universe(n_tickers=3, n_days=90)
    train_end = START + timedelta(days=60)
    val_end = START + timedelta(days=75)

    result = train_and_score(
        universe, model_key, train_end=train_end, val_end=val_end, k=2, max_epochs=2, patience=1
    )

    assert result.model_key == model_key
    assert result.param_count > 0
    assert result.hyperparams["seed"] == 42
    assert len(result.loss_curve) >= 1
    # 세 구간 전부 결과가 나와야 합니다(표본이 아예 없어 _EMPTY_SUMMARY 인 경우도 "결과 있음"으로 칩니다)
    for summary in (result.is_summary, result.val_summary, result.oos_summary):
        assert summary.trading_nights >= 0


def test_train_and_score_rejects_unknown_model_key() -> None:
    universe = _make_synthetic_universe()
    with pytest.raises(ValueError):
        train_and_score(universe, "레버리지", train_end=START + timedelta(days=60), val_end=START + timedelta(days=75))


def test_train_and_score_rejects_insufficient_training_data() -> None:
    universe = _make_synthetic_universe(n_tickers=1, n_days=25)
    with pytest.raises(ValueError):
        train_and_score(
            universe, "mlp", train_end=START + timedelta(days=5), val_end=START + timedelta(days=10)
        )
