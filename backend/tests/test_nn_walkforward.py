"""방식 B(Walk-Forward) — DB 없이 작은 합성 유니버스로만 돕니다.

보는 것은 세 가지입니다. ① 폴드 경계가 「학습 < Val < 시험」으로 겹치지 않는지,
② 학습·Val 텐서가 그 경계 밖의 날짜를 섞지 않는지(미래참조 없음), ③ 폴드를 이어 붙인
통합 지표 계산이 맞는지. 학습 품질은 검증하지 않습니다(리포트 쪽 책임).
"""

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import nn_search, nn_selection
from app.nn_walkforward import (
    SERIES_NAMES,
    Fold,
    _fold_tensors,
    _prepare_family,
    make_folds,
    run_walkforward,
    stitch,
)
from app.schemas import Candle

START = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _universe(n_tickers: int = 6, n_days: int = 1100) -> dict[str, list[Candle]]:
    """2020-01-01 부터 달력일로 이어지는 합성 일봉(2022 년 말까지 덮습니다)."""
    out: dict[str, list[Candle]] = {}
    for t_idx in range(n_tickers):
        price = 100.0 + 7 * t_idx
        candles: list[Candle] = []
        for day in range(n_days):
            drift = 0.002 * (1 if (day + t_idx) % 3 != 0 else -1)
            open_p = price * (1 + 0.001 * math.sin(day / 3 + t_idx))
            close_p = price * (1 + drift + 0.002 * math.sin(day / 5 + t_idx))
            candles.append(
                Candle(
                    time=START + timedelta(days=day),
                    open=open_p,
                    high=max(open_p, close_p) * 1.01,
                    low=min(open_p, close_p) * 0.99,
                    close=close_p,
                    volume=1_000_000 + 10_000 * ((day * (t_idx + 1)) % 7),
                )
            )
            price = close_p
        out[f"T{t_idx}"] = candles
    return out


# ── ① 폴드 경계 ──────────────────────────────────────────────


def test_make_folds_boundaries_do_not_overlap() -> None:
    folds = make_folds((2022, 2023, 2026))
    assert [f.test_year for f in folds] == [2022, 2023, 2026]
    for f in folds:
        assert f.train_end < f.val_end < f.test_end
        assert f.val_end.year == f.test_year - 1  # Val 은 시험 바로 전 해
        assert f.train_end.year == f.test_year - 2


def test_make_folds_matches_week10_convention_for_2025() -> None:
    """2025 폴드는 방식 A 의 경계(Train ~2023 · Val 2024 · OOS 2025)와 똑같아야 합니다."""
    (fold,) = make_folds((2025,))
    assert fold.train_end == nn_selection.TRAIN_END
    assert fold.val_end == nn_selection.VAL_END


# ── ② 학습 텐서가 경계를 넘지 않는다 ─────────────────────────


def test_fold_tensors_use_only_dates_before_boundaries() -> None:
    universe = _universe()
    walk = nn_selection._walk_all(universe, "extended")
    fam = _prepare_family(walk, "A", nn_selection.feature_dim("extended"))
    fold = make_folds((2022,))[0]

    x_train, y_train, x_val, y_val = _fold_tensors(fam, fold)

    expected_train = sum(
        len(item[0]) for d, item in zip(fam.dates, fam.scoring.day_batches) if item and d <= fold.train_end
    )
    expected_val = sum(
        len(item[0])
        for d, item in zip(fam.dates, fam.scoring.day_batches)
        if item and fold.train_end < d <= fold.val_end
    )
    assert x_train.shape[0] == y_train.shape[0] == expected_train > 0
    assert x_val.shape[0] == y_val.shape[0] == expected_val > 0
    # 시험 해(2022)의 표본은 학습에도 Val 에도 들어가지 않습니다
    in_test = sum(
        len(item[0]) for d, item in zip(fam.dates, fam.scoring.day_batches) if item and d > fold.val_end
    )
    assert in_test > 0
    assert x_train.shape[0] + x_val.shape[0] + in_test == sum(len(i[0]) for i in fam.scoring.day_batches if i)


def test_fold_tensors_empty_train_raises() -> None:
    universe = _universe(n_days=200)  # 2020 년 중반까지만 — 2019 이전 학습 구간이 없는 폴드
    walk = nn_selection._walk_all(universe, "extended")
    fam = _prepare_family(walk, "A", nn_selection.feature_dim("extended"))
    early = Fold(
        test_year=2020,
        train_end=datetime(2018, 12, 31, tzinfo=timezone.utc),
        val_end=datetime(2019, 12, 31, tzinfo=timezone.utc),
        test_end=datetime(2020, 12, 31, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError):
        _fold_tensors(fam, early)


# ── ③ 이어 붙이기 ────────────────────────────────────────────


def _fake_fold(year: int, returns: list[float]) -> dict:
    dates = [f"{year}-01-{i + 2:02d}" for i in range(len(returns))]
    entry = {"dates": dates, "returns": returns}
    return {"test_year": year, "series": {name: entry for name in SERIES_NAMES}}


def test_stitch_compounds_across_folds_without_reset() -> None:
    folds = [_fake_fold(2022, [0.10, 0.10]), _fake_fold(2023, [-0.10, 0.05])]
    out = stitch(folds)
    s = out["winner_config"]
    expected = (1.1 * 1.1 * 0.9 * 1.05 - 1) * 100
    assert s["cumulative_return_pct"] == pytest.approx(expected)
    assert s["nights"] == 4
    assert s["cum_pct"][-1] == pytest.approx(expected)
    assert [f["test_year"] for f in s["per_fold"]] == [2022, 2023]
    # 폴드별 누적은 각자 0 에서 다시 시작합니다
    assert s["per_fold"][0]["cumulative_return_pct"] == pytest.approx(21.0)
    assert s["per_fold"][1]["cumulative_return_pct"] == pytest.approx((0.9 * 1.05 - 1) * 100)


def test_stitch_counts_folds_beating_benchmark() -> None:
    folds = [_fake_fold(2022, [0.01, 0.02, 0.01]), _fake_fold(2023, [0.01, -0.02, 0.01])]
    for f in folds:
        f["series"]["benchmark"] = {"dates": f["series"]["rule_fixed"]["dates"], "returns": [0.0, 0.0, 0.0]}
    # 2022: 벤치마크 변동 없음 → 샤프 0, 수익이 있으니 이김. 2023: 평균이 거의 0 이라 지거나 비슷.
    out = stitch(folds)
    assert out["rule_fixed"]["folds_beating_benchmark"] in (1, 2)
    assert out["benchmark"]["folds_beating_benchmark"] is None


# ── ④ 작은 합성 유니버스에서 끝까지 한 번 ────────────────────


def test_run_walkforward_smoke_and_resume(tmp_path: Path) -> None:
    universe = _universe()
    kwargs = dict(
        test_years=(2022,),
        cache_dir=tmp_path,
        ml_keys=("ridge",),
        nn_archs=("mlp", "gru"),
        winner_seeds=(1, 2),
        max_epochs=2,
    )
    report = run_walkforward(universe, **kwargs)  # type: ignore[arg-type]

    (fold,) = report["folds"]
    assert fold["test_year"] == 2022
    assert fold["train_rows"] > 0 and fold["val_rows"] > 0
    assert fold["traditional_ml"]["pick"] == "ridge"
    assert fold["neural_net"]["pick"] in ("mlp", "gru")
    assert len(fold["rule_based"]["candidates"]) == 8
    # 선택은 Val 샤프로만 — 고른 후보의 Val 샤프가 후보 중 최대
    cands = fold["neural_net"]["candidates"]
    assert cands[fold["neural_net"]["pick"]]["val"]["sharpe"] == max(c["val"]["sharpe"] for c in cands.values())
    assert set(report["stitched"]) == set(SERIES_NAMES)
    assert report["stitched"]["winner_config"]["nights"] > 100  # 2022 년 거래일

    # 폴드 결과가 파일로 남고, 다시 돌리면 학습 없이 그 파일을 씁니다
    cached = tmp_path / "walkforward_fold_2022.json"
    assert cached.exists()
    payload = json.loads(cached.read_text(encoding="utf-8"))
    payload["seconds"] = 12345  # 캐시에서 읽었는지 알아보는 표식
    cached.write_text(json.dumps(payload), encoding="utf-8")
    again = run_walkforward(universe, **kwargs)  # type: ignore[arg-type]
    assert again["folds"][0]["seconds"] == 12345

    # --fresh 면 캐시를 무시하고 다시 학습합니다
    fresh = run_walkforward(universe, fresh=True, **kwargs)  # type: ignore[arg-type]
    assert fresh["folds"][0]["seconds"] != 12345


def test_run_walkforward_skips_fold_without_test_data(tmp_path: Path) -> None:
    universe = _universe(n_days=800)  # 2022 년 초중반까지 — 2023 시험 구간이 없습니다
    report = run_walkforward(
        universe,
        test_years=(2022, 2023),
        cache_dir=tmp_path,
        ml_keys=("ridge",),
        nn_archs=("mlp",),
        winner_seeds=(1,),
        max_epochs=1,
    )
    assert [f["test_year"] for f in report["folds"]] == [2022]


def test_winner_config_matches_search_winner() -> None:
    """고정해 둔 승자 설정이 방식 A 의 탐색 결과(GRU · 64 · 0.4)와 같아야 합니다."""
    from app import nn_walkforward as wf

    assert (wf.WINNER_ARCH, wf.WINNER_HIDDEN, wf.WINNER_DROPOUT) == ("gru", 64, 0.4)
    assert wf.DEFAULT_HIDDEN == 32 and wf.DEFAULT_DROPOUT == 0.3
    assert nn_search.ENSEMBLE_SEEDS == (42, 1, 2, 3, 4)
