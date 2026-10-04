"""計画・インバランス・CSV・データ検証の単体テスト（OpenSTEFの学習は行わない）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jukyu.config import AREAS, SETTINGS, TZ
from jukyu.export import export_bid_csv, export_plan_csv
from jukyu.planning import BID_ROUND_KW, build_plan, evaluate_imbalance, quantile_series, summarize
from jukyu.sample_data import generate
from tools.validate_data import validate


@pytest.fixture(scope="module")
def day_data() -> pd.DataFrame:
    return generate(start="2026-07-01", end="2026-07-03").loc["2026-07-02"]


@pytest.fixture(scope="module")
def forecast(day_data) -> pd.DataFrame:
    p50 = day_data["load"] * 1.02
    return pd.DataFrame({"p10": p50 * 0.9, "p50": p50, "p90": p50 * 1.1}, index=day_data.index)


@pytest.fixture(scope="module")
def plan(forecast, day_data) -> pd.DataFrame:
    return build_plan(forecast, day_data, spot_price=day_data["jepx_spot"])


def test_sample_day_has_48_slots(day_data):
    assert len(day_data) == 48
    assert str(day_data.index.tz) == TZ
    assert day_data.index[0].strftime("%H:%M") == "00:00"


def test_quantile_interpolation(forecast):
    q50 = quantile_series(forecast, 0.5)
    q30 = quantile_series(forecast, 0.3)
    assert np.allclose(q50, forecast["p50"])
    assert ((q30 > forecast["p10"]) & (q30 < forecast["p50"])).all()


def test_plan_slots_and_labels(plan):
    assert plan["コマ"].tolist() == list(range(1, 49))
    assert plan["時刻帯"].iloc[0] == "00:00-00:30"
    assert plan["時刻帯"].iloc[-1] == "23:30-24:00"


def test_plan_balances_within_rounding(plan):
    assert plan["計画差_kW"].abs().max() < BID_ROUND_KW
    assert (plan["JEPX買入札_kW"] >= 0).all() and (plan["JEPX売入札_kW"] >= 0).all()
    assert not ((plan["JEPX買入札_kW"] > 0) & (plan["JEPX売入札_kW"] > 0)).any()
    assert (plan["JEPX買入札_kW"] % BID_ROUND_KW == 0).all()


def test_contracts_follow_day_night(plan):
    night = plan.loc[plan.index.hour < 8]
    day = plan.loc[(plan.index.hour >= 8) & (plan.index.hour < 22)]
    for c in SETTINGS.contracts:
        assert (night[f"{c.name}_kW"] == c.kw_night).all()
        assert (day[f"{c.name}_kW"] == c.kw_day).all()


def test_solar_is_zero_at_night(plan):
    col = f"{SETTINGS.solar.name}_kW"
    assert (plan.loc[plan.index.hour < 4, col] == 0).all()
    assert plan[col].max() <= SETTINGS.solar.capacity_kw


def test_imbalance_sign_and_units(plan, day_data):
    ev = evaluate_imbalance(plan, day_data["load"])
    expected = plan["調達計画合計_kW"] * 0.5 - day_data["load"] * 0.5
    assert np.allclose(ev["インバランス_kWh"], expected)
    assert (ev["インバランス損失_円"] >= 0).all()


def test_perfect_forecast_has_small_imbalance(day_data):
    exact = pd.DataFrame({"p10": day_data["load"], "p50": day_data["load"], "p90": day_data["load"]})
    plan = build_plan(exact, day_data, spot_price=day_data["jepx_spot"])
    s = summarize(plan, evaluate_imbalance(plan, day_data["load"]))
    assert s["MAPE_%"] < 0.5
    assert s["不足インバランス_MWh"] == 0


def test_csv_exports(plan, tmp_path):
    p = export_plan_csv(plan, tmp_path / "plan.csv", "テストBG")
    out = pd.read_csv(p, encoding="utf-8-sig")
    assert len(out) == 48
    assert {"BG名", "対象日", "コマ", "時刻帯", "需要計画_kWh", "計画差_kWh"} <= set(out.columns)
    assert np.isclose(out["需要計画_kWh"].sum(), (plan["需要計画_kW"] * 0.5).sum(), atol=48)

    b = pd.read_csv(export_bid_csv(plan, tmp_path / "bid.csv", "東京エリア"), encoding="utf-8-sig")
    assert set(b["売買"]) <= {"買", "売"}
    assert set(b["エリア"]) == {"東京エリア"}


def test_validate_sample_ok(tmp_path):
    p = tmp_path / "s.csv"
    generate(start="2026-01-01", end="2026-04-01").to_csv(p)
    errors, _ = validate(str(p))
    assert errors == []


def test_validate_detects_problems(tmp_path):
    p = tmp_path / "bad.csv"
    pd.DataFrame({"timestamp": ["2026-01-01 00:00:00", "2026-01-01 00:15:00"], "load": [1, 2]}).to_csv(p, index=False)
    errors, _ = validate(str(p))
    joined = " ".join(errors)
    assert "タイムゾーン" in joined and "必須列" in joined and "30分" in joined


# --- エリア ----------------------------------------------------------------

def test_areas_are_distinct():
    assert set(AREAS) >= {"tokyo", "tohoku"}
    for key, st in AREAS.items():
        assert st.area.key == key
        assert st.area.country_code == "JP"
    assert len({st.bg_name for st in AREAS.values()}) == len(AREAS)
    assert len({st.area.data_path for st in AREAS.values()}) == len(AREAS)
    # 東北は陸前高田付近
    th = AREAS["tohoku"].area
    assert th.name == "東北エリア"
    assert abs(th.latitude - 39.02) < 0.1 and abs(th.longitude - 141.63) < 0.1


@pytest.fixture(scope="module")
def tohoku_day() -> pd.DataFrame:
    return generate(start="2026-01-14", end="2026-01-16", area="tohoku").loc["2026-01-15"]


def test_tohoku_sample_has_48_slots_and_differs(tohoku_day, day_data):
    assert len(tohoku_day) == 48
    assert str(tohoku_day.index.tz) == TZ
    tokyo_jan = generate(start="2026-01-14", end="2026-01-16").loc["2026-01-15"]
    assert not np.allclose(tohoku_day["load"], tokyo_jan["load"])
    assert tohoku_day["temperature"].mean() < tokyo_jan["temperature"].mean()


def test_tohoku_plan_uses_tohoku_supply(tohoku_day, tmp_path):
    st = AREAS["tohoku"]
    fc = pd.DataFrame({"p10": tohoku_day["load"] * 0.95, "p50": tohoku_day["load"], "p90": tohoku_day["load"] * 1.05})
    plan = build_plan(fc, tohoku_day, spot_price=tohoku_day["jepx_spot"], settings=st)
    for c in st.contracts:
        day = plan.loc[(plan.index.hour >= 8) & (plan.index.hour < 22), f"{c.name}_kW"]
        assert (day == c.kw_day).all()
    assert plan[f"{st.solar.name}_kW"].max() <= st.solar.capacity_kw
    assert plan["計画差_kW"].abs().max() < BID_ROUND_KW

    ev = evaluate_imbalance(plan, tohoku_day["load"], st)
    assert len(ev) == 48
    out = pd.read_csv(export_plan_csv(plan, tmp_path / "plan.csv", st.bg_name), encoding="utf-8-sig")
    assert set(out["BG名"]) == {st.bg_name}
    b = pd.read_csv(export_bid_csv(plan, tmp_path / "bid.csv", st.area.name), encoding="utf-8-sig")
    assert set(b["エリア"]) == {"東北エリア"}
