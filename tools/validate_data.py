"""入力CSV（30分間隔の統合データ）を検証する。

使い方:
    python tools/validate_data.py data/sample_30min.csv

終了コード: 0 = エラーなし（警告はあってもよい）、1 = エラーあり
"""

from __future__ import annotations

import sys

import pandas as pd

REQUIRED = {
    "load": (0, 5_000_000),             # kW
    "temperature": (-30, 45),           # ℃
    "relative_humidity": (0, 100),      # %
    "windspeed": (0, 60),               # m/s
    "pressure": (850, 1090),            # hPa
    "radiation": (0, 1400),             # W/m2
}
OPTIONAL = {"jepx_spot": (0, 300)}      # 円/kWh


def validate(path: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    df = pd.read_csv(path)
    if "timestamp" not in df.columns:
        errors.append("timestamp 列がありません。")
        return errors, warnings
    try:
        ts = pd.to_datetime(df["timestamp"], utc=True)
    except (ValueError, TypeError) as e:
        errors.append(f"timestamp を日時として読めません: {e}")
        return errors, warnings
    if not df["timestamp"].astype(str).str.contains(r"[+-]\d{2}:?\d{2}$|Z$", regex=True).all():
        errors.append("timestamp にタイムゾーンがない行があります（例: 2026-10-01 00:00:00+09:00）。")

    df.index = ts.dt.tz_convert("Asia/Tokyo")
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        errors.append(f"必須列がありません: {', '.join(missing)}")
    if "jepx_spot" not in df.columns:
        warnings.append("jepx_spot がないため、スポット調達費とインバランス金額は計算されません。")

    if df.index.duplicated().any():
        errors.append(f"重複した時刻が {int(df.index.duplicated().sum())} 件あります。")
    if not df.index.is_monotonic_increasing:
        warnings.append("時刻が昇順に並んでいません（読み込み時に並べ替えます）。")

    idx = df.index.sort_values()
    off_grid = (idx.minute % 30 != 0) | (idx.second != 0)
    if off_grid.any():
        errors.append(f"30分の区切りに乗っていない時刻が {int(off_grid.sum())} 件あります（コマ開始時刻に揃えてください）。")
    full = pd.date_range(idx.min(), idx.max(), freq="30min")
    gaps = full.difference(idx)
    if len(gaps):
        warnings.append(f"欠けているコマが {len(gaps)} 件あります（最初: {gaps[0]}）。")

    span_days = (idx.max() - idx.min()).days
    if span_days < 60:
        errors.append(f"期間が {span_days} 日しかありません。学習には最低60日、季節変動のためには1年以上を推奨します。")
    elif span_days < 365:
        warnings.append(f"期間が {span_days} 日です。季節変動を学習するには1年以上を推奨します。")

    for col, (lo, hi) in {**REQUIRED, **OPTIONAL}.items():
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        n_nan = int(s.isna().sum())
        if n_nan:
            (errors if col == "load" and n_nan > len(s) * 0.05 else warnings).append(f"{col}: 欠損 {n_nan} 件")
        out = s[(s < lo) | (s > hi)]
        if len(out):
            warnings.append(f"{col}: 想定範囲 {lo}〜{hi} の外が {len(out)} 件（例: {out.iloc[0]}）")

    if "load" in df.columns:
        load = pd.to_numeric(df["load"], errors="coerce")
        if load.median() < 1_000 and load.max() < 10_000:
            warnings.append("load の値が小さめです。kWh/コマ のままなら ×2 で kW に換算してください。")
        flat = load.diff().eq(0).rolling(12).sum().eq(12).sum()
        if flat:
            warnings.append(f"load が6時間以上同じ値のまま続く箇所があります（{int(flat)} コマ）。メーター欠測の可能性。")

    return errors, warnings


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    errors, warnings = validate(sys.argv[1])
    for e in errors:
        print(f"[エラー] {e}")
    for w in warnings:
        print(f"[警告] {w}")
    if not errors and not warnings:
        print("問題は見つかりませんでした。")
    elif not errors:
        print(f"エラーなし（警告 {len(warnings)} 件）")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
