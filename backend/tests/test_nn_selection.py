"""Week 10 — 신경망 IS/Val/OOS 비교 모듈. DB 없이, 작은 합성 유니버스로만 돕니다.

가장 중요하게 보는 것은 selection.py 의 8대 전략과 똑같은 원칙 — 횡단면
정규화·타깃이 그날 하루치 정보만 쓰는지(미래 비참조)이고, 그 다음이 시퀀스
윈도우가 "역사가 모자란 종목은 제외"를 지키는지입니다. 실제 학습 수렴 품질은
검증하지 않습니다(그건 과제 산출물 — WEEK10_NN_REPORT.md — 쪽 책임입니다).
"""

import math
from datetime import datetime, timedelta, timezone

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


# ──────────────────────────────────────────────────────────────
# 확장 피처 세트(룰베이스 점수 + 원시 캔들 변화) · 순위 평균 앙상블
# ──────────────────────────────────────────────────────────────


def test_extended_features_never_look_at_future_candles() -> None:
    from app.nn_selection import EXTENDED_FEATURE_NAMES

    base = _make_synthetic_universe()
    mutated = {t: list(cs) for t, cs in base.items()}
    for t in mutated:
        last = mutated[t][-1]
        mutated[t][-1] = Candle(time=last.time, open=9999, high=20000, low=1, close=15000, volume=999)

    walk_base = _walk_all(base, "extended")
    walk_mutated = _walk_all(mutated, "extended")

    assert walk_base.norm_features[:-1] == walk_mutated.norm_features[:-1]
    assert walk_base.seq_features[:-1] == walk_mutated.seq_features[:-1]

    first = next(f for f in walk_base.norm_features if f)
    for values in first.values():
        assert len(values) == len(EXTENDED_FEATURE_NAMES)
        assert all(-0.5 <= v <= 0.5 for v in values)


def test_walk_all_rejects_unknown_feature_set() -> None:
    with pytest.raises(ValueError):
        _walk_all(_make_synthetic_universe(), "전부")


def test_rank_average_treats_missing_ticker_as_lowest() -> None:
    from app.nn_search import rank_average

    a = [{"X": 3.0, "Y": 2.0, "Z": 1.0}]
    b = [{"X": 0.1, "Y": 0.9}]  # Z 는 점수가 없어 이 구성원 안에서 최하위
    merged = rank_average([a, b])[0]

    assert merged["Z"] == pytest.approx(-0.5)
    assert merged["X"] == pytest.approx(merged["Y"])  # 한쪽 1등 · 다른 쪽 2등으로 서로 엇갈림
    assert merged["X"] > merged["Z"]


def test_rank_average_orders_tickers_deterministically() -> None:
    """동점 종목의 순서가 실행마다 달라지면 Top-K 가 바뀝니다 — 항상 종목코드 순이어야 합니다."""
    from app.nn_search import rank_average

    a = [{"C": 1.0, "A": 1.0, "B": 1.0, "D": 1.0}]
    b = [{"D": 2.0, "B": 2.0, "A": 2.0, "C": 2.0}]

    assert list(rank_average([a, b])[0]) == ["A", "B", "C", "D"]


def test_winner_rows_are_three_periods_under_one_fixed_key() -> None:
    """탐색 승자는 항상 같은 키로 is · val · oos 세 줄 — 다시 돌려도 줄이 늘지 않습니다."""
    import json

    from app.nn_search import evaluate, winner_rows
    from app.nn_selection import SEARCH_WINNER_KEY

    # 구간 경계(TRAIN_END · VAL_END)가 UTC 라 여기서도 UTC 로, 세 구간에 다 걸치게 만듭니다
    start = datetime(2023, 10, 1, tzinfo=timezone.utc)
    walk = _walk_all(_make_synthetic_universe(n_days=500, start=start))
    scores = [{t: float(i) for i, t in enumerate(sorted(day))} for day in walk.norm_features]
    winner = evaluate("NN 시드앙상블", walk, scores, cost=0.001, members=("a|s1", "a|s2"))

    rows = winner_rows(winner, cost=0.001, configs_tried=87, candidates_compared=12)

    assert [(r[0], r[1], r[3]) for r in rows] == [
        ("neural_net", SEARCH_WINNER_KEY, "is"),
        ("neural_net", SEARCH_WINNER_KEY, "val"),
        ("neural_net", SEARCH_WINNER_KEY, "oos"),
    ]
    assert all(len(r) == 22 for r in rows)  # upsert_experiment_rows 의 자리표시자 개수
    meta = json.loads(rows[0][18])
    assert meta["members"] == ["a|s1", "a|s2"]
    assert meta["configs_tried"] == 87
    assert meta["candidates_compared"] == 12


# ──────────────────────────────────────────────────────────────
# 탐색 승자 저장 · 복원 — 수치만 남기고 모델을 버리면 다시 쓸 수 없습니다
# ──────────────────────────────────────────────────────────────


def test_config_name_round_trips_and_rejects_non_nn_names() -> None:
    from app.nn_search import config_name, parse_config_name

    config = ("gru", "extended", 64, 0.4, 0.001, 0.0003, 42)
    assert parse_config_name(config_name(*config)) == config
    for name in ("random_forest", "vol_surge_edge", "gru|전부|h64|d0.4|wd0.001|lr0.001|s42"):
        with pytest.raises(ValueError):
            parse_config_name(name)


def test_saved_winner_scores_the_same_after_reload(tmp_path) -> None:
    """저장한 승자를 다시 불러와 채점하면 학습 직후와 똑같은 점수가 나와야 합니다."""
    import json
    import stat

    from app import nn_search
    from app.nn_selection import feature_dim

    start = datetime(2023, 10, 1, tzinfo=timezone.utc)
    universe = _make_synthetic_universe(n_days=500, start=start)
    walk = _walk_all(universe, "extended")
    ds = nn_search.build_dataset(walk, "B", feature_dim("extended"))
    members = [nn_search.run_config(ds, "extended", "gru", 8, 0.2, 1e-3, 1e-3, seed, 0.001) for seed in (1, 2)]
    winner = nn_search.evaluate(
        "NN 시드앙상블",
        walk,
        nn_search.rank_average([m.scores for m in members]),
        0.001,
        tuple(m.name for m in members),
        tuple(t for m in members for t in m.trained),
    )

    model_dir = nn_search.save_winner_models(winner, tmp_path / "winner")

    files = sorted(p.name for p in model_dir.iterdir())
    assert files == ["manifest.json", "member_0.pt", "member_1.pt"]
    for path in model_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    manifest = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [m["seed"] for m in manifest["nn_members"]] == [1, 2]
    assert manifest["other_components"] == []

    _, reloaded = nn_search.score_with_winner(universe, model_dir)
    assert len(reloaded) == len(winner.scores)
    for got, want in zip(reloaded, winner.scores):
        assert got == pytest.approx(want)

    # DB 줄에도 파라미터 수와 구성원별 학습 기록이 실립니다
    rows = nn_search.winner_rows(winner, 0.001, configs_tried=2, candidates_compared=1)
    assert rows[0][19] == sum(t.param_count for t in winner.trained) > 0
    details = json.loads(rows[0][18])["member_details"]
    assert [d["seed"] for d in details] == [1, 2]
    assert all(d["epochs"] == len(d["loss_curve"]) >= d["best_epoch"] >= 1 for d in details)


def test_load_winner_models_without_saved_files_raises(tmp_path) -> None:
    from app.nn_search import load_winner_models

    with pytest.raises(FileNotFoundError):
        load_winner_models(tmp_path / "없음")


# ──────────────────────────────────────────────────────────────
# 점검에서 찾은 세 가지의 수정 — 중복 피처 · 거래일 공백 시퀀스 · Huber delta
# ──────────────────────────────────────────────────────────────


def test_dedup_feature_sets_drop_only_the_redundant_column() -> None:
    from app.nn_selection import REDUNDANT_FEATURE, feature_dim, feature_names

    assert REDUNDANT_FEATURE not in feature_names("base_dedup")
    assert REDUNDANT_FEATURE not in feature_names("extended_dedup")
    assert (feature_dim("base"), feature_dim("base_dedup")) == (8, 7)
    assert (feature_dim("extended"), feature_dim("extended_dedup")) == (19, 18)

    universe = _make_synthetic_universe(n_tickers=3, n_days=90)
    for full, dedup in (("base", "base_dedup"), ("extended", "extended_dedup")):
        walk_full, walk_dedup = _walk_all(universe, full), _walk_all(universe, dedup)
        drop = list(feature_names(full)).index(REDUNDANT_FEATURE)
        compared = 0
        for day_full, day_dedup in zip(walk_full.norm_features, walk_dedup.norm_features):
            assert day_full.keys() == day_dedup.keys()
            for ticker, vec in day_full.items():
                # 열마다 따로 순위를 매기므로, 한 열을 빼도 나머지 열은 한 글자도 안 바뀝니다
                assert day_dedup[ticker] == vec[:drop] + vec[drop + 1 :]
                compared += 1
        assert compared > 0


def test_close_pos_and_upper_shadow_sum_to_one_only_for_bullish_candles() -> None:
    """예전 정의 오류의 흔적 — 양봉에서는 두 값의 합이 1 이지만, 음봉에서는 더 이상 아닙니다.

    (고가-종가)/범위 로 계산하던 시절에는 음봉에서도 합이 1 이라 같은 정보를 두 번 넣었습니다.
    """
    from app import factors

    bullish = Candle(time=START, open=100, high=110, low=90, close=104, volume=1)
    bearish = Candle(time=START, open=104, high=110, low=90, close=100, volume=1)

    assert factors.close_pos(bullish) + factors.upper_shadow_ratio(bullish) == pytest.approx(1.0)
    assert factors.close_pos(bearish) + factors.upper_shadow_ratio(bearish) != pytest.approx(1.0)


def test_sequence_window_restarts_after_a_calendar_gap() -> None:
    """종목이 며칠 빠지면(거래정지 등) 끊긴 날들을 한 창으로 잇지 않고 SEQ_LEN 일을 다시 쌓습니다."""
    universe = _make_synthetic_universe(n_tickers=2, n_days=120)
    universe["T0"] = [c for i, c in enumerate(universe["T0"]) if i not in (50, 51, 52)]

    walk = _walk_all(universe)

    assert "T0" in walk.seq_features[45]  # 공백 전에는 정상적으로 시퀀스가 있습니다
    assert "T0" in walk.norm_features[53]  # 공백이 끝난 첫날부터 피처는 다시 생기지만
    for day in range(53, 53 + SEQ_LEN - 1):
        assert "T0" not in walk.seq_features[day], day  # 새로 쌓는 동안은 시퀀스 후보에서 빠지고
        assert "T1" in walk.seq_features[day]  # 공백 없는 종목은 영향이 없습니다
    assert "T0" in walk.seq_features[53 + SEQ_LEN - 1]  # SEQ_LEN 일이 차면 돌아옵니다


def test_huber_delta_matches_the_target_scale() -> None:
    """delta 가 1.0 이면 이 타깃 크기(최대 0.30)에서는 MSE 와 같습니다 — 큰 오차를 실제로 완충해야 합니다."""
    from torch import nn as torch_nn

    from app.nn_selection import HUBER_DELTA

    import torch

    error = torch.tensor([0.30])
    zero = torch.tensor([0.0])
    assert HUBER_DELTA < 0.1
    assert torch_nn.HuberLoss(delta=HUBER_DELTA)(error, zero).item() < 0.1 * torch_nn.MSELoss()(error, zero).item()
    assert torch_nn.HuberLoss(delta=1.0)(error, zero).item() == pytest.approx(0.5 * 0.30**2)  # 예전 값은 사실상 MSE


def test_feature_compare_runs_every_model_on_the_same_inputs() -> None:
    """피처 세트만 바꿔 전통ML·신경망을 같은 입력으로 돌리는 통제 실험 도구의 스모크 테스트."""
    from app.feature_compare import run_compare

    start = datetime(2023, 10, 1, tzinfo=timezone.utc)
    universe = _make_synthetic_universe(n_tickers=3, n_days=500, start=start)

    rows = run_compare(universe, feature_sets=("base", "extended"), ml_keys=("ridge",), nn_archs=("mlp",))

    assert [(r["feature_set"], r["generation"]) for r in rows] == [
        ("base", "traditional_ml"),
        ("base", "neural_net"),
        ("extended", "traditional_ml"),
        ("extended", "neural_net"),
    ]
    assert [r["n_features"] for r in rows] == [8, 8, 19, 19]
    for r in rows:
        for period in ("is", "val", "oos"):
            assert r[period]["trading_nights"] >= 0


def test_registry_models_accept_the_feature_count_they_are_built_with() -> None:
    import torch

    for key, spec in NN_MODEL_REGISTRY.items():
        model = spec.build(19)
        x = torch.zeros(4, 19) if spec.family == "A" else torch.zeros(4, SEQ_LEN, 19)
        assert model(x).shape == (4,), key


def test_three_generation_comparison_defaults_to_the_extended_features() -> None:
    from app.nn_selection import DEFAULT_FEATURE_SET

    assert DEFAULT_FEATURE_SET == "extended"

    start = datetime(2023, 10, 1, tzinfo=timezone.utc)
    universe = _make_synthetic_universe(n_tickers=3, n_days=500, start=start)
    result = train_and_score(universe, "mlp", k=2, max_epochs=2, patience=1)
    assert result.hyperparams["feature_set"] == "extended"
    assert result.hyperparams["n_features"] == 19


def test_traditional_ml_is_trained_on_the_same_inputs_as_the_neural_nets(tmp_path) -> None:
    """전통ML 도 신경망과 같은 입력(순위 변환 피처)·같은 타깃으로 학습해야 두 세대의 차이가 구조뿐입니다."""
    from app.nn_selection import select_best_traditional_ml

    start = datetime(2023, 10, 1, tzinfo=timezone.utc)
    universe = _make_synthetic_universe(n_tickers=3, n_days=500, start=start)

    choice = select_best_traditional_ml(universe, k=2, model_dir=tmp_path)

    assert choice.generation == "traditional_ml"
    assert choice.hyperparams is not None
    assert choice.hyperparams["feature_set"] == "extended"
    assert choice.hyperparams["n_features"] == 19
    assert "초과수익률" in choice.hyperparams["target"]
    assert len(list(tmp_path.glob("*_extended.joblib"))) == 4  # 4종 모두 저장
