"""머신러닝(RandomForest) 전 구간 학습 — Week 8 미션. DB 없이 돕니다.

8대 비머신러닝 전략과 똑같이 "그날 알 수 있는 정보만" 피처로 쓰는지, 모델을
저장·재사용해도 결과가 재현되는지를 봅니다.
"""

import random
from datetime import datetime, timedelta

import pytest

from app import ml_selection
from app.schemas import Candle

TICKERS = ["000001", "000002"]


def _make_universe(days: int = 45, seed: int = 0) -> dict[str, list[Candle]]:
    rng = random.Random(seed)
    universe: dict[str, list[Candle]] = {}
    for ticker in TICKERS:
        candles: list[Candle] = []
        price = 10_000.0
        for i in range(days):
            # 전일 종가 대비 갭을 둬야 밤수익률(타깃)이 매번 0이 되지 않습니다
            o = price * (1 + rng.uniform(-0.01, 0.01))
            c = o * (1 + rng.uniform(-0.02, 0.02))
            h = max(o, c) * (1 + rng.uniform(0, 0.01))
            l = min(o, c) * (1 - rng.uniform(0, 0.01))
            v = rng.randint(500_000, 1_500_000)
            candles.append(
                Candle(
                    time=datetime(2026, 1, 1) + timedelta(days=i),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=v,
                )
            )
            price = c
        universe[ticker] = candles
    return universe


def test_build_training_set_needs_twenty_days_of_history() -> None:
    universe = _make_universe(days=45)
    X, y = ml_selection.build_training_set(universe)
    assert len(X) > 0
    assert len(X) == len(y)
    assert all(len(row) == len(ml_selection.FEATURE_NAMES) for row in X)


def test_too_little_history_yields_no_training_rows() -> None:
    universe = _make_universe(days=10)  # 20일 창을 채우기엔 모자랍니다
    X, y = ml_selection.build_training_set(universe)
    assert X == []
    assert y == []


def test_features_never_look_at_a_later_candle() -> None:
    """미래참조 편향 검증: 마지막 날 캔들만 바꿔도, 그 전까지의 피처는 전부 그대로여야 합니다.

    바뀐 값이 등장하는 자리는 "마지막 전환의 실현수익(타깃)" 하나뿐입니다 —
    test_selection.py 의 future-candle 변조 테스트와 같은 방식입니다.
    """
    universe = _make_universe(days=45)
    _, features_before, realized_before = ml_selection._walk(universe)

    mutated = {t: list(cs) for t, cs in universe.items()}
    last = mutated[TICKERS[0]][-1]
    mutated[TICKERS[0]][-1] = Candle(
        time=last.time, open=1.0, high=999_999.0, low=0.5, close=500_000.0, volume=999_999_999
    )

    _, features_after, realized_after = ml_selection._walk(mutated)

    assert features_before == features_after
    assert realized_before[-1][TICKERS[0]] != realized_after[-1][TICKERS[0]]


def test_train_and_save_then_load_round_trip(tmp_path) -> None:
    # min_samples_leaf=20 으로 실제로 가지가 갈라지려면 표본이 충분해야 합니다
    universe = _make_universe(days=150)
    model_path = tmp_path / "model.joblib"

    trained = ml_selection.train_and_save(universe, cost=0.001, model_path=model_path)
    assert model_path.exists()
    assert 1 <= trained.k <= 20
    assert trained.rows > 0
    assert len(trained.feature_importances) == len(ml_selection.FEATURE_NAMES)
    assert sum(f.importance for f in trained.feature_importances) == pytest.approx(1.0, abs=1e-6)

    loaded = ml_selection.load_and_score(universe, cost=0.001, model_path=model_path)
    assert loaded.rows == trained.rows
    assert loaded.result.summary.sharpe == pytest.approx(trained.result.summary.sharpe)
    assert loaded.k == trained.k


def test_load_and_score_without_a_saved_model_fails(tmp_path) -> None:
    universe = _make_universe(days=45)
    with pytest.raises(FileNotFoundError):
        ml_selection.load_and_score(universe, model_path=tmp_path / "missing.joblib")


def test_training_needs_at_least_some_candles() -> None:
    with pytest.raises(ValueError):
        ml_selection.train_and_save({})
