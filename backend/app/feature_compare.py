"""입력 피처 세트(기본 8개 · 확장 19개)만 바꿔 가며 전통ML 4종과 신경망 5종을 같은 조건으로
비교하는 1회성 도구. `docker compose exec backend python -m app.feature_compare`

묻는 것은 하나입니다 — "신경망이 전통ML보다 나은 것이 구조 덕인가, 피처를 더 줘서인가?"
그래서 모델 종류만 다르고 나머지는 전부 같게 맞춥니다.

  - 입력: 같은 피처 행렬(날짜별 종목 간 순위 -0.5~+0.5), 같은 타깃(시장중립 초과수익률)
  - 구간: Train 으로만 학습 · Val 은 신경망 얼리스토핑에만 · OOS 는 학습·선택에 관여하지 않음
  - 매매: Top-5 · 왕복비용 0.10% · 같은 채점 함수(nn_search.evaluate)
  - 신경망은 기본 설정(hidden 32 · dropout 0.3 · 시드 42) 그대로 — 탐색하지 않습니다

여기서는 아무것도 "고르지" 않고 전부 나열합니다(OOS 를 보고 고르는 일이 없습니다).
운영 화면의 전통ML(ml_selection: 원시 피처 · 원시 밤수익률 타깃)과는 입력·타깃이 달라
숫자가 다릅니다 — 이 도구는 통제 실험용이고 DB 에는 쓰지 않습니다.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from . import ml_selection, nn_search, nn_selection
from .schemas import Candle

RESULT_PATH = Path("/tmp/nn-search/feature_compare.json")
FEATURE_SETS = ("base", "extended")


def _row(generation: str, model: str, feature_set: str, ev: nn_search.Evaluated) -> dict:
    return {
        "generation": generation,
        "model": model,
        "feature_set": feature_set,
        "n_features": nn_selection.feature_dim(feature_set),
        "is": nn_search._summary_dict(ev.is_summary),
        "val": nn_search._summary_dict(ev.val_summary),
        "oos": nn_search._summary_dict(ev.reveal_oos()),
    }


def _ml_scores(model: object, ds: nn_search.Dataset) -> nn_search.DayScores:
    out: nn_search.DayScores = []
    for item in ds.day_batches:
        if item is None:
            out.append({})
            continue
        tickers, batch = item
        out.append({t: float(p) for t, p in zip(tickers, model.predict(batch.numpy()))})  # type: ignore[attr-defined]
    return out


def run_compare(
    universe: dict[str, list[Candle]],
    cost: float = nn_selection.DEFAULT_COST,
    feature_sets: tuple[str, ...] = FEATURE_SETS,
    ml_keys: tuple[str, ...] | None = None,
    nn_archs: tuple[str, ...] | None = None,
) -> list[dict]:
    """피처 세트 × (전통ML · 신경망) 전부를 학습·채점해 한 줄씩 돌려줍니다."""
    ml_keys = ml_keys if ml_keys is not None else tuple(ml_selection.MODEL_REGISTRY)
    nn_archs = nn_archs if nn_archs is not None else tuple(nn_selection.NN_MODEL_REGISTRY)
    rows: list[dict] = []

    for feature_set in feature_sets:
        walk = nn_selection._walk_all(universe, feature_set)
        dim = nn_selection.feature_dim(feature_set)
        flat = nn_search.build_dataset(walk, "A", dim)  # (N, F) — 전통ML 과 MLP 의 입력
        seq = nn_search.build_dataset(walk, "B", dim) if any(
            nn_selection.NN_MODEL_REGISTRY[a].family == "B" for a in nn_archs
        ) else None

        for key in ml_keys:
            started = time.time()
            spec = ml_selection.MODEL_REGISTRY[key]
            model = spec.build()
            model.fit(flat.x_train.numpy(), flat.y_train.numpy())  # Train 구간만
            ev = nn_search.evaluate(spec.label, walk, _ml_scores(model, flat), cost)
            rows.append(_row("traditional_ml", spec.label, feature_set, ev))
            nn_search._log(
                event="ml",
                model=spec.label,
                feature_set=feature_set,
                val_sharpe=round(ev.val_summary.sharpe, 3),
                seconds=round(time.time() - started),
            )

        for arch in nn_archs:
            family = nn_selection.NN_MODEL_REGISTRY[arch].family
            ds = flat if family == "A" else seq
            assert ds is not None
            ev = nn_search.run_config(ds, feature_set, arch, 32, 0.3, 1e-3, 1e-3, 42, cost)
            rows.append(_row("neural_net", nn_selection.NN_MODEL_REGISTRY[arch].label, feature_set, ev))

    return rows


async def _main() -> None:
    from . import db
    from .main import _fetch_universe_candles

    await db.connect()
    try:
        async with db.acquire() as conn:
            universe = await _fetch_universe_candles(conn, days=3650, start=None, end=None)
    finally:
        await db.disconnect()
    nn_search._log(event="universe", tickers=len(universe))

    rows = await asyncio.to_thread(run_compare, universe)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    nn_search._log(event="done", rows=len(rows), path=str(RESULT_PATH))


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
