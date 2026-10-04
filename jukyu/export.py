"""計画データのCSV出力（Excelで開けるよう UTF-8 BOM 付き）。

注意: ここで出力するのは社内確認用の「簡易様式」です。
広域機関（OCCTO）への需要調達計画の提出は、広域機関システムの所定様式で行う必要があります。
実運用ではこの表を元に、所定様式への変換処理を別途実装してください。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .planning import HALF_HOUR


def export_plan_csv(plan: pd.DataFrame, path: Path, bg_name: str) -> Path:
    day = plan.index[0].strftime("%Y-%m-%d")
    kwh_cols = [c for c in plan.columns if c.endswith("_kW") and not c.startswith(("需要予測", "計画差", "JEPX買入札", "JEPX売入札"))]
    out = pd.DataFrame({"BG名": bg_name, "対象日": day, "コマ": plan["コマ"], "時刻帯": plan["時刻帯"]})
    for c in kwh_cols:
        out[c.replace("_kW", "_kWh")] = (plan[c] * HALF_HOUR).round(0)
    out["計画差_kWh"] = (plan["計画差_kW"] * HALF_HOUR).round(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def export_bid_csv(plan: pd.DataFrame, path: Path, area_name: str) -> Path:
    """JEPXスポット入札の確認用一覧（入札量は kWh/h = 30分平均kW）。

    入札はエリアごとに行うため、どのエリアの入札かを「エリア」列に入れる。
    """
    day = plan.index[0].strftime("%Y-%m-%d")
    rows = []
    for _, r in plan.iterrows():
        if r["JEPX買入札_kW"] > 0:
            rows.append({"エリア": area_name, "受渡日": day, "コマ": r["コマ"], "時刻帯": r["時刻帯"], "売買": "買", "価格_円per_kWh": r["買入札価格_円"], "入札量_kWh_per_h": r["JEPX買入札_kW"]})
        if r["JEPX売入札_kW"] > 0:
            rows.append({"エリア": area_name, "受渡日": day, "コマ": r["コマ"], "時刻帯": r["時刻帯"], "売買": "売", "価格_円per_kWh": r["売入札価格_円"], "入札量_kWh_per_h": r["JEPX売入札_kW"]})
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path
