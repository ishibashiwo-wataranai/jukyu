"""日次の需給計画を作成する。

使い方:
    # 翌日分の計画を作る（データ末尾の日付まで実績がある前提）
    python run_daily.py --date 2026-10-05

    # 過去14日分をバックテストして精度とインバランスを確認する
    python run_daily.py --date 2026-10-05 --backtest-days 14

出力（output/ 以下）:
    YYYY-MM-DD/需給計画_YYYY-MM-DD.csv      48コマの需要・調達計画（簡易様式）
    YYYY-MM-DD/JEPX入札_YYYY-MM-DD.csv      スポット入札一覧
    YYYY-MM-DD/インバランス評価_YYYY-MM-DD.csv  実績がある日のみ
    results.json                             ダッシュボード用データ
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from jukyu.config import SETTINGS, TZ
from jukyu.export import export_bid_csv, export_plan_csv
from jukyu.forecast import forecast_next_day
from jukyu.planning import build_plan, evaluate_imbalance, summarize


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(TZ)
    return df.sort_index()


def run_one(df: pd.DataFrame, day: str, out_dir: Path, model: str, has_actual: bool) -> dict:
    fc, metrics = forecast_next_day(df, day, model=model)
    day_df = df.loc[day]
    plan = build_plan(fc, day_df, spot_price=day_df.get("jepx_spot"))
    d = out_dir / day
    export_plan_csv(plan, d / f"需給計画_{day}.csv", SETTINGS.bg_name)
    export_bid_csv(plan, d / f"JEPX入札_{day}.csv")

    ev = None
    if has_actual:
        ev = evaluate_imbalance(plan, day_df["load"])
        ev.to_csv(d / f"インバランス評価_{day}.csv", index=False, encoding="utf-8-sig")

    def col(frame, name):
        return [None if pd.isna(v) else round(float(v), 1) for v in frame[name]]

    rec = {
        "date": day,
        "weekday": "月火水木金土日"[pd.Timestamp(day).dayofweek],
        "has_actual": has_actual,
        "summary": summarize(plan, ev),
        "slots": plan["時刻帯"].tolist(),
        "p10": col(plan, "需要予測P10_kW"),
        "p50": col(plan, "需要予測P50_kW"),
        "p90": col(plan, "需要予測P90_kW"),
        "demand_plan": col(plan, "需要計画_kW"),
        "supply": {c.removesuffix("_kW"): col(plan, c) for c in plan.columns
                   if c.endswith("_kW") and c.startswith(tuple(x.name for x in SETTINGS.contracts) + (SETTINGS.solar.name,))},
        "jepx_buy": col(plan, "JEPX買約定_kW") if "JEPX買約定_kW" in plan else col(plan, "JEPX買入札_kW"),
        "jepx_sell": col(plan, "JEPX売約定_kW") if "JEPX売約定_kW" in plan else col(plan, "JEPX売入札_kW"),
        "spot": col(plan, "スポット価格_円") if "スポット価格_円" in plan else None,
        "actual": col(day_df.reindex(plan.index), "load") if has_actual else None,
        "imbalance_kwh": col(ev, "インバランス_kWh") if ev is not None else None,
        "imbalance_loss_yen": col(ev, "インバランス損失_円") if ev is not None and "インバランス損失_円" in ev else None,
    }
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description="新電力向け 翌日需給計画（OpenSTEF）")
    ap.add_argument("--data", default="data/sample_30min.csv")
    ap.add_argument("--date", required=True, help="計画対象日（翌日）YYYY-MM-DD")
    ap.add_argument("--backtest-days", type=int, default=0, help="対象日より前の何日分を検証するか")
    ap.add_argument("--model", default="xgboost", help="gblinear / xgboost など")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()

    df = load_data(args.data)
    out_dir = Path(args.out)
    target = pd.Timestamp(args.date)
    days = [(target - pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(args.backtest_days, 0, -1)]

    records = []
    for day in days:
        rec = run_one(df, day, out_dir, args.model, has_actual=True)
        s = rec["summary"]
        print(f"[検証] {day}({rec['weekday']}) MAPE {s['MAPE_%']:5.2f}%  不足 {s['不足インバランス_MWh']:6.2f}MWh  余剰 {s['余剰インバランス_MWh']:6.2f}MWh  損失 {s['インバランス損失_万円']:5.1f}万円")
        records.append(rec)

    rec = run_one(df, args.date, out_dir, args.model, has_actual=False)
    s = rec["summary"]
    print(f"[計画] {args.date}({rec['weekday']}) 需要 {s['需要計画_MWh']}MWh  最大 {s['最大需要計画_kW']:,.0f}kW  JEPX買 {s['JEPX買入札_MWh']}MWh  売 {s['JEPX売入札_MWh']}MWh")
    records.append(rec)

    payload = {
        "bg_name": SETTINGS.bg_name,
        "area": SETTINGS.area.name,
        "model": args.model,
        "bid_quantile": SETTINGS.planning.bid_quantile,
        "supply_names": [c.name for c in SETTINGS.contracts] + [SETTINGS.solar.name],
        "days": records,
    }
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"→ {out_dir}/results.json")


if __name__ == "__main__":
    main()
