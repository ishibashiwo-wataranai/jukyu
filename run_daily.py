"""日次の需給計画を作成する。

使い方:
    # 全エリアの翌日分の計画を作る（データ末尾の日付まで実績がある前提）
    python run_daily.py --date 2026-10-05

    # 過去14日分をバックテストして精度とインバランスを確認する
    python run_daily.py --date 2026-10-05 --backtest-days 14

    # 1エリアだけ（エリアは jukyu/config.py の AREAS のキー）
    python run_daily.py --date 2026-10-05 --area tohoku

計画はエリア（BG）ごとに別々に立てる。エリアをまたいで合算した計画は作らない。

出力（output/ 以下、<エリア> は tokyo / tohoku など）:
    <エリア>/YYYY-MM-DD/需給計画_YYYY-MM-DD.csv      48コマの需要・調達計画（簡易様式）
    <エリア>/YYYY-MM-DD/JEPX入札_YYYY-MM-DD.csv      スポット入札一覧
    <エリア>/YYYY-MM-DD/インバランス評価_YYYY-MM-DD.csv  実績がある日のみ
    results.json                                     ダッシュボード用データ（実行した全エリア）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from jukyu.config import AREAS, TZ, Settings
from jukyu.export import export_bid_csv, export_plan_csv
from jukyu.forecast import forecast_next_day
from jukyu.planning import build_plan, evaluate_imbalance, summarize


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(TZ)
    return df.sort_index()


def run_one(df: pd.DataFrame, day: str, out_dir: Path, model: str, has_actual: bool, settings: Settings) -> dict:
    fc, metrics = forecast_next_day(df, day, settings=settings, model=model)
    day_df = df.loc[day]
    plan = build_plan(fc, day_df, spot_price=day_df.get("jepx_spot"), settings=settings)
    d = out_dir / day
    export_plan_csv(plan, d / f"需給計画_{day}.csv", settings.bg_name)
    export_bid_csv(plan, d / f"JEPX入札_{day}.csv", settings.area.name)

    ev = None
    if has_actual:
        ev = evaluate_imbalance(plan, day_df["load"], settings)
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
                   if c.endswith("_kW") and c.startswith(tuple(x.name for x in settings.contracts) + (settings.solar.name,))},
        "jepx_buy": col(plan, "JEPX買約定_kW") if "JEPX買約定_kW" in plan else col(plan, "JEPX買入札_kW"),
        "jepx_sell": col(plan, "JEPX売約定_kW") if "JEPX売約定_kW" in plan else col(plan, "JEPX売入札_kW"),
        "spot": col(plan, "スポット価格_円") if "スポット価格_円" in plan else None,
        "actual": col(day_df.reindex(plan.index), "load") if has_actual else None,
        "imbalance_kwh": col(ev, "インバランス_kWh") if ev is not None else None,
        "imbalance_loss_yen": col(ev, "インバランス損失_円") if ev is not None and "インバランス損失_円" in ev else None,
    }
    return rec


def run_area(settings: Settings, data_path: str, args: argparse.Namespace) -> dict:
    """1エリア分の検証と翌日計画を行い、results.json の1エリア分を返す。"""
    df = load_data(data_path)
    key = settings.area.key
    out_dir = Path(args.out) / key
    target = pd.Timestamp(args.date)
    days = [(target - pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(args.backtest_days, 0, -1)]

    records = []
    for day in days:
        rec = run_one(df, day, out_dir, args.model, has_actual=True, settings=settings)
        s = rec["summary"]
        print(f"[{key}][検証] {day}({rec['weekday']}) MAPE {s['MAPE_%']:5.2f}%  不足 {s['不足インバランス_MWh']:6.2f}MWh  余剰 {s['余剰インバランス_MWh']:6.2f}MWh  損失 {s['インバランス損失_万円']:5.1f}万円")
        records.append(rec)

    rec = run_one(df, args.date, out_dir, args.model, has_actual=False, settings=settings)
    s = rec["summary"]
    print(f"[{key}][計画] {args.date}({rec['weekday']}) 需要 {s['需要計画_MWh']}MWh  最大 {s['最大需要計画_kW']:,.0f}kW  JEPX買 {s['JEPX買入札_MWh']}MWh  売 {s['JEPX売入札_MWh']}MWh")
    records.append(rec)

    return {
        "key": key,
        "bg_name": settings.bg_name,
        "area": settings.area.name,
        "bid_quantile": settings.planning.bid_quantile,
        "supply_names": [c.name for c in settings.contracts] + [settings.solar.name],
        "days": records,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="新電力向け 翌日需給計画（OpenSTEF）")
    ap.add_argument("--area", default="all", choices=[*AREAS, "all"], help="計画するエリア（all は全エリア）")
    ap.add_argument("--data", default=None, help="入力CSV。省略時はエリアごとの config の data_path（--area で1エリアを指定したときだけ使える）")
    ap.add_argument("--date", required=True, help="計画対象日（翌日）YYYY-MM-DD")
    ap.add_argument("--backtest-days", type=int, default=0, help="対象日より前の何日分を検証するか")
    ap.add_argument("--model", default="xgboost", help="gblinear / xgboost など")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()

    keys = list(AREAS) if args.area == "all" else [args.area]
    if args.data and len(keys) > 1:
        ap.error("--data は --area で1エリアを指定したときだけ使えます")

    areas = [run_area(AREAS[k], args.data or AREAS[k].area.data_path, args) for k in keys]

    payload = {"model": args.model, "areas": areas}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"→ {out_dir}/results.json")


if __name__ == "__main__":
    main()
