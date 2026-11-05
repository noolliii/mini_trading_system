"""FLAML AutoML 모델 탐색 — DB 없이 돕니다.

실제 FLAML 탐색은 시간 예산을 아주 짧게 주고 가벼운 추정기 하나만 켜서, 테스트
스위트를 느리게 만들지 않으면서도 ml_selection.py 의 채점 파이프라인(MlResult)과
같은 모양으로 결과가 나오는지, 모델 파일이 저장되는지만 확인합니다.
"""

import random
from datetime import datetime, timedelta

import pytest

from app import ml_automl, ml_selection
from app.schemas import Candle

TICKERS = ["000001", "000002"]


def _make_universe(days: int = 45, seed: int = 0) -> dict[str, list[Candle]]:
    rng = random.Random(seed)
    universe: dict[str, list[Candle]] = {}
    for ticker in TICKERS:
        candles: list[Candle] = []
        price = 10_000.0
        for i in range(days):
            o = price * (1 + rng.uniform(-0.01, 0.01))
            c = o * (1 + rng.uniform(-0.02, 0.02))
            h = max(o, c) * (1 + rng.uniform(0, 0.01))
            l = min(o, c) * (1 - rng.uniform(0, 0.01))
            v = rng.randint(500_000, 1_500_000)
            candles.append(Candle(time=datetime(2026, 1, 1) + timedelta(days=i), open=o, high=h, low=l, close=c, volume=v))
            price = c
        universe[ticker] = candles
    return universe


def test_insufficient_history_raises_before_any_search() -> None:
    universe = _make_universe(days=10)  # 20일 창을 못 채움 — FLAML 을 돌리기도 전에 막혀야 함
    with pytest.raises(ValueError):
        ml_automl.run_automl_search(universe, time_budget=1, estimator_list=("extra_tree",))


def test_run_automl_search_returns_an_ml_selection_compatible_result(tmp_path) -> None:
    universe = _make_universe(days=60)
    model_path = tmp_path / "flaml_test.joblib"

    result = ml_automl.run_automl_search(
        universe,
        cost=0.001,
        time_budget=3,
        estimator_list=("extra_tree",),  # 가장 가벼운 추정기 하나만 — 테스트를 빠르게 유지
        model_path=model_path,
    )

    assert model_path.exists()
    assert isinstance(result, ml_selection.MlResult)
    assert result.model_key == "flaml_best"
    assert result.model_label.startswith("FLAML(")
    assert 1 <= result.k <= 20
    assert result.rows > 0
    assert len(result.feature_importances) == len(ml_selection.FEATURE_NAMES)
    assert sum(f.importance for f in result.feature_importances) == pytest.approx(1.0, abs=1e-6)
