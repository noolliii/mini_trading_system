"""FLAML AutoML 로 RandomForest·ExtraTrees·LightGBM·XGBoost 하이퍼파라미터를 자동
탐색해 ml_selection.MODEL_REGISTRY 의 손튜닝 결과와 나란히 비교하는 도구.

ml_selection.py 의 MODEL_REGISTRY 설명에 있는 "탐색은 다음 기회로 미룹니다"가
여기입니다 — 종목이 8→140개로 늘면서 자동 탐색이 수업 시간 안에(수~십여 분)
끝날 만해졌습니다. flaml·lightgbm·xgboost 는 scikit-learn 에 없는 새 패키지라
API 로 노출하지 않고, 이 모듈을 필요할 때만 직접 돌리는 1회성 비교 도구로
둡니다 — `python -m app.ml_automl` (평소 수집·백테스트 흐름엔 영향이 없습니다).

FLAML 은 예측오차(mse)를 기준으로 "최적"을 찾지만, 우리가 보려는 건 매매전략
으로 바꿨을 때의 샤프지수입니다 — 그래서 탐색이 끝난 뒤 ml_selection.py 와
똑같은 채점 파이프라인(_score_with_model · selection.best_k_from_scores)으로
실제 백테스트 성과를 다시 계산합니다.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from flaml import AutoML

from . import ml_selection, selection
from .schemas import Candle

MODEL_KEY = "flaml_best"
MODEL_PATH = ml_selection.MODEL_DIR / f"{MODEL_KEY}.joblib"
DEFAULT_TIME_BUDGET_SECONDS = 600
DEFAULT_ESTIMATORS: tuple[str, ...] = ("lgbm", "xgboost", "rf", "extra_tree")


def run_automl_search(
    universe: dict[str, list[Candle]],
    cost: float = 0.0,
    time_budget: int = DEFAULT_TIME_BUDGET_SECONDS,
    estimator_list: tuple[str, ...] = DEFAULT_ESTIMATORS,
    k_candidates: tuple[int, ...] = selection.K_CANDIDATES,
    model_path: Path = MODEL_PATH,
) -> ml_selection.MlResult:
    """estimator_list 안에서 FLAML 이 찾은 최적 모델을 ml_selection.MlResult 형식으로
    돌려주고 저장합니다 — MODEL_REGISTRY 모델들과 바로 나란히 비교할 수 있습니다.
    """
    X_list, y_list = ml_selection.build_training_set(universe)
    if len(X_list) < 30:
        raise ValueError("학습할 데이터가 부족합니다 — 20일 이상 역사가 쌓인 날이 더 필요합니다")

    X = np.array(X_list)
    y = np.array(y_list)

    automl = AutoML()
    automl.fit(
        X_train=X,
        y_train=y,
        task="regression",
        time_budget=time_budget,
        estimator_list=list(estimator_list),
        metric="mse",
        verbose=0,
    )
    model = automl.model.estimator

    all_dates, sorted_candidates, realized_per_day, universe_returns = ml_selection._score_with_model(
        universe, model
    )
    if not all_dates:
        raise ValueError("채점할 날짜가 없습니다 — 20일 이상 역사가 쌓인 날이 더 필요합니다")

    label = f"FLAML({automl.best_estimator})"
    best_k, result = selection.best_k_from_scores(
        label, all_dates, sorted_candidates, realized_per_day, universe_returns, cost, k_candidates
    )

    trained_at = datetime.now(timezone.utc)
    trained_from, trained_to = all_dates[0], all_dates[-1]
    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "model_key": MODEL_KEY,
            "feature_names": ml_selection.FEATURE_NAMES,
            "trained_at": trained_at.isoformat(),
            "trained_from": trained_from.isoformat(),
            "trained_to": trained_to.isoformat(),
            "rows": len(X_list),
            "flaml_estimator": automl.best_estimator,
            "flaml_config": automl.best_config,
        },
        model_path,
    )
    os.chmod(model_path, 0o600)

    return ml_selection.MlResult(
        model_key=MODEL_KEY,
        model_label=label,
        k=best_k,
        result=result,
        feature_importances=ml_selection._importances(
            ml_selection.FEATURE_NAMES, ml_selection._tree_importances(model)
        ),
        trained_at=trained_at,
        trained_from=trained_from,
        trained_to=trained_to,
        rows=len(X_list),
    )


async def _main() -> None:
    """CLI 진입점 — `docker compose exec backend python -m app.ml_automl` 로 돌립니다.

    API 엔드포인트는 없습니다 — 모델 선택을 다시 비교하고 싶을 때만 수동으로 실행합니다.
    """
    from . import db
    from .main import _fetch_universe_candles

    await db.connect()
    try:
        async with db.acquire() as conn:
            universe = await _fetch_universe_candles(
                conn, days=3650, start=date(2020, 1, 1), end=date(2026, 11, 4)
            )
        result = run_automl_search(universe, cost=0.001)
        print(
            f"{result.model_label} 샤프 {result.result.summary.sharpe:.2f} · "
            f"CAGR {result.result.summary.cagr:.1f}% · MDD {result.result.summary.mdd:.1f}% · K={result.k}\n"
            f"표본 {result.rows}개 · 저장 위치: {MODEL_PATH}"
        )
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(_main())
