"""여러 종류의 머신러닝 모델로 다음날 밤수익률(Open_t+1/Close_t - 1)을 예측해
Top-K 종목을 고르는 백테스트 — Week 8 미션의 "머신러닝" 축입니다.

8대 비머신러닝 전략(selection.py)과 다른 점은 "점수식을 사람이 정하느냐, 과거
데이터로 학습하느냐" 뿐입니다. 피처는 전부 factors.py 의 과거참조-안전 지표를
그대로 쓰고, 선택·집계·비용차감·샤프 계산은 selection.best_k_from_scores 를
그대로 재사용합니다 — 두 축을 같은 잣대로 비교하기 위함입니다.

모델 종류가 여럿이라(MODEL_REGISTRY), "어떤 모델이 샤프지수가 가장 좋은가"도
8대 전략 리더보드와 같은 방식으로 비교합니다 — train_all_and_save ·
load_all_and_score 가 그 역할입니다. scikit-learn 안에 이미 있는 모델만
씁니다(새 패키지 없음) — 트리 앙상블 3종(RandomForest·ExtraTrees·
GradientBoosting)은 feature_importances_ 로, 선형 베이스라인(Ridge)은
표준화 후 계수 크기로 "무엇을 보고 골랐는지"를 설명합니다.

규칙 2(구간 분할 없음)에 따라 train_and_save 는 받은 기간 전체를 학습에 씁니다.
비머신러닝 전략처럼 "미래 캔들이 과거 선택을 바꾸지 않는다"는 보장은 피처
추출(_extract_features) 단계에서만 성립합니다 — 모델 자체는 전 구간의 통계를
학습하도록 설계가 요구하므로, 재학습 없이 저장된 모델을 다른 기간에 그대로
적용하는 load_and_score 를 따로 둡니다(다음 주 다른 기간 적용 대비).
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import factors, selection
from .schemas import Candle

# 컨테이너 안에만 남깁니다 — 볼륨도 git 도 아닙니다(kis-tokens 와 같은 패턴).
# 재학습이 몇 초면 끝나, 이미지 재빌드로 사라져도 비용이 크지 않습니다.
MODEL_DIR = Path("/tmp/ml-models")


def _model_path(model_key: str) -> Path:
    return MODEL_DIR / f"{model_key}.joblib"


def _tree_importances(model: object) -> list[float]:
    return [float(v) for v in model.feature_importances_]  # type: ignore[attr-defined]


def _linear_importances(pipeline: object) -> list[float]:
    """계수를 절댓값으로 모아 합이 1이 되게 정규화합니다 — 트리 모델의
    feature_importances_ 와 같은 저울로 화면에 나란히 띄우기 위함입니다.
    StandardScaler 를 먼저 거쳐서 계수 크기가 피처 단위에 휘둘리지 않습니다.
    """
    coefs = pipeline.named_steps["ridge"].coef_  # type: ignore[attr-defined]
    abs_coefs = [abs(float(c)) for c in coefs]
    total = sum(abs_coefs) or 1.0
    return [c / total for c in abs_coefs]


@dataclass(frozen=True)
class ModelSpec:
    label: str
    build: Callable[[], object]
    importances: Callable[[object], list[float]]


# 전부 scikit-learn 기본 제공 — 새 패키지 없이 "점수식을 사람이 정하느냐, 학습하느냐"
# 축 안에서 모델 종류만 비교합니다. 하이퍼파라미터는 고정값입니다 — 종목 수가
# 8 → 140 으로 늘어 RandomizedSearchCV 같은 자동 탐색도 가능해졌지만, 수업
# 시간 안에 모델 4개를 전부 재학습해야 해서 탐색은 다음 기회로 미룹니다.
MODEL_REGISTRY: dict[str, ModelSpec] = {
    "random_forest": ModelSpec(
        label="RandomForest",
        build=lambda: RandomForestRegressor(
            n_estimators=500, max_depth=10, min_samples_leaf=50, random_state=42, n_jobs=-1
        ),
        importances=_tree_importances,
    ),
    "extra_trees": ModelSpec(
        label="ExtraTrees",
        build=lambda: ExtraTreesRegressor(
            n_estimators=500, max_depth=10, min_samples_leaf=50, random_state=42, n_jobs=-1
        ),
        importances=_tree_importances,
    ),
    "gradient_boosting": ModelSpec(
        label="GradientBoosting",
        build=lambda: GradientBoostingRegressor(
            n_estimators=300, max_depth=3, min_samples_leaf=50, learning_rate=0.05, random_state=42
        ),
        importances=_tree_importances,
    ),
    "ridge": ModelSpec(
        label="Ridge(선형)",
        build=lambda: Pipeline([("scaler", StandardScaler()), ("ridge", Ridge(alpha=1.0))]),
        importances=_linear_importances,
    ),
}

_WINDOW = 20  # selection.py 의 20일 창과 동일 — 지표가 전부 20일 역사를 요구합니다

FEATURE_NAMES: tuple[str, ...] = (
    "close_pos",
    "upper_shadow_ratio",
    "turnover",
    "volume_ratio",
    "ma_gap",
    "return_5d",
    "night_win_rate",
    "night_return_std",
)


def _extract_features(
    c: Candle,
    prior_closes: list[float],
    prior_volumes: list[float],
    prior_night_returns: list[float],
) -> list[float] | None:
    """factors.py 의 과거참조-안전 지표 8개를 한 벡터로 묶습니다.

    하나라도 역사가 모자라 None 이면 그날 그 종목은 학습·채점 양쪽에서
    통째로 빠집니다 — 8대 전략이 조건 미달 종목을 후보에서 빼는 것과 같습니다.
    """
    vol_ratio = factors.volume_ratio(c, prior_volumes)
    gap = factors.ma_gap(c, prior_closes)
    ret5 = factors.return_5d(c, prior_closes)
    win_rate = factors.night_win_rate(prior_night_returns)
    ret_std = factors.night_return_std(prior_night_returns)
    if None in (vol_ratio, gap, ret5, win_rate, ret_std):
        return None
    return [
        factors.close_pos(c),
        factors.upper_shadow_ratio(c),
        factors.turnover(c),
        vol_ratio,
        gap,
        ret5,
        win_rate,
        ret_std,
    ]


def _walk(
    universe: dict[str, list[Candle]],
) -> tuple[list[datetime], list[dict[str, list[float]]], list[dict[str, float]]]:
    """selection._score_all_days 와 같은 구조로 날짜를 한 번 걷습니다.

    각 날짜 전환마다 종목별 피처 벡터(그날 15:20까지 알 수 있는 정보만)와
    실현된 밤수익률(y = Open_t+1/Close_t - 1)을 모읍니다. 롤링 지표(야간승률 ·
    야간변동성)의 창은 selection.py 와 똑같이 "오늘 채점이 끝난 뒤" 갱신해
    오늘의 타깃을 미리 들여다보지 않습니다.
    """
    by_ticker = {t: sorted(cs, key=lambda c: c.time) for t, cs in universe.items() if cs}
    index_of = {t: {c.time: i for i, c in enumerate(cs)} for t, cs in by_ticker.items()}
    all_dates = sorted({c.time for cs in by_ticker.values() for c in cs})

    if len(all_dates) < 2:
        return [], [], []

    night_returns: dict[str, list[float]] = {t: [] for t in by_ticker}
    features_per_day: list[dict[str, list[float]]] = []
    realized_per_day: list[dict[str, float]] = []

    for i, t in enumerate(all_dates[:-1]):
        t_next = all_dates[i + 1]
        features: dict[str, list[float]] = {}
        realized: dict[str, float] = {}

        for ticker, candles in by_ticker.items():
            idx = index_of[ticker].get(t)
            if idx is None:
                continue
            next_idx = index_of[ticker].get(t_next)
            if next_idx is None or next_idx != idx + 1:
                continue

            c, c_next = candles[idx], candles[next_idx]
            realized[ticker] = c_next.open / c.close - 1

            window_start = max(0, idx - _WINDOW)
            recent = candles[window_start:idx]
            fv = _extract_features(
                c,
                [x.close for x in recent],
                [x.volume for x in recent],
                night_returns[ticker],
            )
            if fv is not None:
                features[ticker] = fv

        for ticker, r in realized.items():
            night_returns[ticker].append(r)

        features_per_day.append(features)
        realized_per_day.append(realized)

    return all_dates, features_per_day, realized_per_day


def build_training_set(universe: dict[str, list[Candle]]) -> tuple[list[list[float]], list[float]]:
    """전 구간의 (피처, 실현 밤수익률) 쌍을 전부 모읍니다 — 분할 없이 전 구간 학습용."""
    _, features_per_day, realized_per_day = _walk(universe)
    X: list[list[float]] = []
    y: list[float] = []
    for features, realized in zip(features_per_day, realized_per_day):
        for ticker, fv in features.items():
            if ticker in realized:
                X.append(fv)
                y.append(realized[ticker])
    return X, y


def _score_with_model(
    universe: dict[str, list[Candle]],
    model: object,
) -> tuple[list[datetime], list[list[tuple[str, float]]], list[dict[str, float]], list[float]]:
    """학습된 모델의 예측값을 점수 삼아 selection._score_all_days 와 같은 모양으로 돌려줍니다."""
    all_dates, features_per_day, realized_per_day = _walk(universe)
    if not all_dates:
        return [], [], [], []

    sorted_candidates: list[list[tuple[str, float]]] = []
    universe_returns: list[float] = []
    for features, realized in zip(features_per_day, realized_per_day):
        if features:
            tickers = list(features.keys())
            preds = model.predict([features[t] for t in tickers])
            candidates = sorted(
                ((t, float(p)) for t, p in zip(tickers, preds)), key=lambda x: x[1], reverse=True
            )
        else:
            candidates = []
        sorted_candidates.append(candidates)
        universe_returns.append(sum(realized.values()) / len(realized) if realized else 0.0)

    return all_dates, sorted_candidates, realized_per_day, universe_returns


@dataclass(frozen=True)
class FeatureImportance:
    name: str
    importance: float


@dataclass(frozen=True)
class MlResult:
    model_key: str
    model_label: str
    k: int
    result: selection.Top5Result
    feature_importances: list[FeatureImportance]
    trained_at: datetime  # 학습을 실행한 시각(벽시계) — 아래 둘과 다른 개념입니다
    trained_from: datetime  # 학습에 쓴 데이터의 첫 날짜
    trained_to: datetime  # 학습에 쓴 데이터의 마지막 날짜
    rows: int  # 학습에 쓰인 (종목, 날짜) 표본 수


def _importances(names: tuple[str, ...], values: "list[float]") -> list[FeatureImportance]:
    return [
        FeatureImportance(name=n, importance=float(v))
        for n, v in sorted(zip(names, values), key=lambda x: x[1], reverse=True)
    ]


def train_and_save(
    universe: dict[str, list[Candle]],
    cost: float = 0.0,
    k_candidates: tuple[int, ...] = selection.K_CANDIDATES,
    model_key: str = "random_forest",
    model_path: Path | None = None,
) -> MlResult:
    """받은 기간 전체로(분할 없이) model_key 모델을 학습하고 저장합니다."""
    if model_key not in MODEL_REGISTRY:
        raise ValueError(f"알 수 없는 머신러닝 모델입니다: {model_key}")
    spec = MODEL_REGISTRY[model_key]

    X, y = build_training_set(universe)
    if len(X) < 30:
        raise ValueError("학습할 데이터가 부족합니다 — 20일 이상 역사가 쌓인 날이 더 필요합니다")

    model = spec.build()
    model.fit(X, y)  # 규칙 2: 검증 구간을 떼지 않고 전 구간으로 학습

    all_dates, sorted_candidates, realized_per_day, universe_returns = _score_with_model(universe, model)
    best_k, result = selection.best_k_from_scores(
        spec.label, all_dates, sorted_candidates, realized_per_day, universe_returns, cost, k_candidates
    )

    trained_at = datetime.now(timezone.utc)
    trained_from, trained_to = all_dates[0], all_dates[-1]
    path = model_path if model_path is not None else _model_path(model_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "model_key": model_key,
            "feature_names": FEATURE_NAMES,
            "trained_at": trained_at.isoformat(),
            "trained_from": trained_from.isoformat(),
            "trained_to": trained_to.isoformat(),
            "rows": len(X),
        },
        path,
    )
    os.chmod(path, 0o600)

    return MlResult(
        model_key=model_key,
        model_label=spec.label,
        k=best_k,
        result=result,
        feature_importances=_importances(FEATURE_NAMES, spec.importances(model)),
        trained_at=trained_at,
        trained_from=trained_from,
        trained_to=trained_to,
        rows=len(X),
    )


def train_all_and_save(
    universe: dict[str, list[Candle]],
    cost: float = 0.0,
    k_candidates: tuple[int, ...] = selection.K_CANDIDATES,
) -> list[MlResult]:
    """MODEL_REGISTRY 의 모델을 전부 순서대로 재학습·저장합니다 — 8대 전략 리더보드와
    같은 비교를 머신러닝 쪽에서도 하기 위함입니다.
    """
    return [train_and_save(universe, cost=cost, k_candidates=k_candidates, model_key=key) for key in MODEL_REGISTRY]


def load_and_score(
    universe: dict[str, list[Candle]],
    cost: float = 0.0,
    k_candidates: tuple[int, ...] = selection.K_CANDIDATES,
    model_key: str = "random_forest",
    model_path: Path | None = None,
) -> MlResult:
    """저장된 모델을 재학습 없이 불러와, 주어진 universe 에 대해서만 다시 점수를 매깁니다.

    다음 주 다른 기간에 같은 모델을 그대로 적용해 보는 길이 이 함수입니다.
    """
    if model_key not in MODEL_REGISTRY:
        raise ValueError(f"알 수 없는 머신러닝 모델입니다: {model_key}")
    spec = MODEL_REGISTRY[model_key]

    path = model_path if model_path is not None else _model_path(model_key)
    if not path.exists():
        raise FileNotFoundError(f"[{spec.label}] 아직 학습된 머신러닝 모델이 없습니다 — 먼저 학습을 실행해 주세요")
    saved = joblib.load(path)
    if "trained_from" not in saved or "trained_to" not in saved:
        # 학습 데이터 구간을 안 남기던 예전 형식 — 어느 기간으로 학습됐는지 알 길이
        # 없어 "학습 안 됨"과 똑같이 취급합니다. 다시 학습하면 바로 해결됩니다.
        raise FileNotFoundError(f"[{spec.label}] 예전 형식으로 저장된 모델입니다 — 다시 학습해 주세요")
    model = saved["model"]

    all_dates, sorted_candidates, realized_per_day, universe_returns = _score_with_model(universe, model)
    best_k, result = selection.best_k_from_scores(
        spec.label, all_dates, sorted_candidates, realized_per_day, universe_returns, cost, k_candidates
    )

    return MlResult(
        model_key=model_key,
        model_label=spec.label,
        k=best_k,
        result=result,
        feature_importances=_importances(saved["feature_names"], spec.importances(model)),
        trained_at=datetime.fromisoformat(saved["trained_at"]),
        trained_from=datetime.fromisoformat(saved["trained_from"]),
        trained_to=datetime.fromisoformat(saved["trained_to"]),
        rows=saved.get("rows", 0),
    )


def load_all_and_score(
    universe: dict[str, list[Candle]],
    cost: float = 0.0,
    k_candidates: tuple[int, ...] = selection.K_CANDIDATES,
) -> list[MlResult]:
    """이미 학습·저장된 모델들만 모아 재학습 없이 다시 채점합니다.

    아직 한 번도 학습하지 않은 모델은 조용히 건너뜁니다 — discord.py 가 웹훅 미설정을
    건너뛰는 것과 같은 결로, 리더보드 일부가 비어 있다고 전체 조회를 실패시키지 않습니다.
    """
    results = []
    for key in MODEL_REGISTRY:
        try:
            results.append(load_and_score(universe, cost=cost, k_candidates=k_candidates, model_key=key))
        except FileNotFoundError:
            continue
    return results
