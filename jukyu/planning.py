"""翌日の需給計画（調達計画・JEPX入札量）とインバランス評価。

単位の約束:
    *_kw   30分平均の電力（kW）。JEPXの入札量 kWh/h と同じ数値。
    *_kwh  30分コマの電力量（kWh）= kW × 0.5
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import SETTINGS, Settings

HALF_HOUR = 0.5
BID_ROUND_KW = 100.0   # 入札量の丸め単位（JEPXの最小単位は取引規程で確認してください）


def quantile_series(fc: pd.DataFrame, q: float) -> pd.Series:
    """P10/P50/P90 の間を線形補間して任意の分位点の需要を作る。"""
    levels = np.array([int(c[1:]) / 100 for c in fc.columns])
    values = fc.to_numpy()
    out = np.array([np.interp(q, levels, row) for row in values])
    return pd.Series(out, index=fc.index)


def solar_forecast_kw(radiation_wm2: pd.Series, settings: Settings = SETTINGS) -> pd.Series:
    s = settings.solar
    return (s.capacity_kw * s.performance_ratio * radiation_wm2 / 1000.0).clip(lower=0)


def contract_kw(index: pd.DatetimeIndex, settings: Settings = SETTINGS) -> pd.DataFrame:
    hour = index.hour + index.minute / 60
    daytime = (hour >= 8) & (hour < 22)
    return pd.DataFrame(
        {c.name: np.where(daytime, c.kw_day, c.kw_night) for c in settings.contracts},
        index=index,
    )


def build_plan(
    fc: pd.DataFrame,
    weather: pd.DataFrame,
    spot_price: pd.Series | None = None,
    settings: Settings = SETTINGS,
) -> pd.DataFrame:
    """48コマの需給計画表を作る。

    Args:
        fc: 需要予測（列 p10/p50/p90、kW）。
        weather: 対象日の気象予報（radiation 列を使用）。
        spot_price: JEPXスポット約定価格（円/kWh）。入札前は前日実績などの想定値を渡す。
    """
    p = settings.planning
    plan = pd.DataFrame(index=fc.index)
    plan["コマ"] = np.arange(1, len(fc) + 1)
    plan["時刻帯"] = [f"{t:%H:%M}-{(t + pd.Timedelta(minutes=30)):%H:%M}".replace("-00:00", "-24:00") for t in fc.index]
    plan["需要予測P10_kW"] = fc["p10"]
    plan["需要予測P50_kW"] = fc["p50"]
    plan["需要予測P90_kW"] = fc["p90"]
    plan["需要計画_kW"] = quantile_series(fc, p.bid_quantile).round(0)

    contracts = contract_kw(fc.index, settings)
    for name in contracts.columns:
        plan[f"{name}_kW"] = contracts[name]
    plan[f"{settings.solar.name}_kW"] = solar_forecast_kw(weather["radiation"].reindex(fc.index), settings).round(0)

    fixed_supply = plan[[c for c in plan.columns if c.endswith("_kW") and not c.startswith("需要")]].sum(axis=1)
    net = plan["需要計画_kW"] - fixed_supply
    plan["JEPX買入札_kW"] = (np.ceil(net.clip(lower=0) / BID_ROUND_KW) * BID_ROUND_KW)
    plan["JEPX売入札_kW"] = (np.floor((-net).clip(lower=0) / BID_ROUND_KW) * BID_ROUND_KW)
    plan["買入札価格_円"] = np.where(plan["JEPX買入札_kW"] > 0, p.bid_price_cap_yen, np.nan)
    plan["売入札価格_円"] = np.where(plan["JEPX売入札_kW"] > 0, p.sell_floor_yen, np.nan)

    if spot_price is not None:
        price = spot_price.reindex(fc.index)
        plan["スポット価格_円"] = price
        filled_buy = np.where(price <= p.bid_price_cap_yen, plan["JEPX買入札_kW"], 0.0)
        filled_sell = np.where(price >= p.sell_floor_yen, plan["JEPX売入札_kW"], 0.0)
        plan["JEPX買約定_kW"] = filled_buy
        plan["JEPX売約定_kW"] = filled_sell
        plan["調達計画合計_kW"] = fixed_supply + filled_buy - filled_sell
        plan["スポット調達費_円"] = (filled_buy - filled_sell) * HALF_HOUR * price
    else:
        plan["調達計画合計_kW"] = fixed_supply + plan["JEPX買入札_kW"] - plan["JEPX売入札_kW"]

    plan["計画差_kW"] = plan["調達計画合計_kW"] - plan["需要計画_kW"]
    return plan


def evaluate_imbalance(plan: pd.DataFrame, actual_kw: pd.Series, settings: Settings = SETTINGS) -> pd.DataFrame:
    """当日（または事後）の実績と計画からインバランスを見積もる。

    インバランス量 = 調達計画 − 需要実績（＋は余剰、−は不足）。
    単価は「スポット価格×係数」の簡易モデルです。実際のインバランス料金は
    補正インバランス料金算定インデックス等に基づくため、精算値とは一致しません。
    """
    p = settings.planning
    ev = pd.DataFrame(index=plan.index)
    ev["コマ"] = plan["コマ"]
    ev["時刻帯"] = plan["時刻帯"]
    ev["需要計画_kWh"] = plan["需要計画_kW"] * HALF_HOUR
    ev["需要実績_kWh"] = actual_kw.reindex(plan.index) * HALF_HOUR
    ev["調達計画_kWh"] = plan["調達計画合計_kW"] * HALF_HOUR
    ev["インバランス_kWh"] = ev["調達計画_kWh"] - ev["需要実績_kWh"]

    price = plan.get("スポット価格_円")
    if price is not None:
        shortage = (-ev["インバランス_kWh"]).clip(lower=0)
        surplus = ev["インバランス_kWh"].clip(lower=0)
        ev["インバランス単価_円"] = np.where(ev["インバランス_kWh"] < 0, price * p.shortage_price_factor, price * p.surplus_price_factor)
        ev["インバランス精算_円"] = surplus * price * p.surplus_price_factor - shortage * price * p.shortage_price_factor
        # 予測が完全に当たっていた場合（スポット価格で過不足を調整できた場合）との差＝予測誤差のコスト
        ev["インバランス損失_円"] = surplus * price * (1 - p.surplus_price_factor) + shortage * price * (p.shortage_price_factor - 1)
    ev["予測誤差率_%"] = ((plan["需要計画_kW"] * HALF_HOUR - ev["需要実績_kWh"]) / ev["需要実績_kWh"] * 100).round(2)
    return ev


def summarize(plan: pd.DataFrame, ev: pd.DataFrame | None = None) -> dict:
    s = {
        "需要計画_MWh": round(plan["需要計画_kW"].sum() * HALF_HOUR / 1000, 1),
        "最大需要計画_kW": float(plan["需要計画_kW"].max()),
        "JEPX買入札_MWh": round(plan["JEPX買入札_kW"].sum() * HALF_HOUR / 1000, 1),
        "JEPX売入札_MWh": round(plan["JEPX売入札_kW"].sum() * HALF_HOUR / 1000, 1),
        "計画差の最大_kW": float(plan["計画差_kW"].abs().max()),
    }
    if "スポット調達費_円" in plan:
        s["スポット調達費_万円"] = round(plan["スポット調達費_円"].sum() / 10_000, 1)
    if ev is not None:
        act = ev["需要実績_kWh"]
        s["需要実績_MWh"] = round(act.sum() / 1000, 1)
        s["MAPE_%"] = round(float((ev["需要計画_kWh"] - act).abs().div(act).mean() * 100), 2)
        s["不足インバランス_MWh"] = round(float((-ev["インバランス_kWh"]).clip(lower=0).sum() / 1000), 2)
        s["余剰インバランス_MWh"] = round(float(ev["インバランス_kWh"].clip(lower=0).sum() / 1000), 2)
        if "インバランス損失_円" in ev:
            s["インバランス精算_万円"] = round(float(ev["インバランス精算_円"].sum() / 10_000), 1)
            s["インバランス損失_万円"] = round(float(ev["インバランス損失_円"].sum() / 10_000), 1)
    return s
