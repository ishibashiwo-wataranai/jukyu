"""検証用のサンプルデータ（30分値）を生成する。

東京エリアで高圧の業務用需要家（オフィス・店舗・工場）を中心に供給する、
最大需要およそ5万kWの新電力を想定した合成データです。実データに差し替える場合は、
同じ列名・30分間隔のCSVを用意してください（README参照）。

列:
    load                 需要実績（kW、30分平均）
    temperature          気温（℃）
    relative_humidity    相対湿度（%）
    windspeed            風速（m/s）
    pressure             気圧（hPa）
    radiation            全天日射量（W/m2）
    jepx_spot            JEPXスポット価格（円/kWh、エリアプライス想定）
"""

from __future__ import annotations

import holidays
import numpy as np
import pandas as pd

from .config import TZ


def _ar1(n: int, phi: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    x = np.zeros(n)
    eps = rng.normal(0, sigma, n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + eps[i]
    return x


def generate(start: str = "2025-04-01", end: str = "2026-10-06", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="30min", tz=TZ, inclusive="left")
    n = len(idx)
    doy = idx.dayofyear.to_numpy()
    hour = (idx.hour + idx.minute / 60).to_numpy()

    # --- 気象 ---------------------------------------------------------
    seasonal = 16.5 - 10.5 * np.cos(2 * np.pi * (doy - 20) / 365)          # 1月下旬が最低
    diurnal = 4.0 * np.sin(2 * np.pi * (hour - 9) / 24)                     # 15時ごろ最高
    weather_dev = _ar1(n, 0.995, 0.25, rng)                                 # 数日単位の寒暖
    temperature = seasonal + diurnal + weather_dev

    cloud = np.clip(0.45 + _ar1(n, 0.98, 0.08, rng), 0, 1)                 # 雲量 0〜1
    decl = 23.44 * np.sin(2 * np.pi * (doy - 81) / 365)
    lat = np.deg2rad(35.68)
    hour_angle = np.deg2rad(15 * (hour + 0.25 - 12))
    sin_elev = np.sin(lat) * np.sin(np.deg2rad(decl)) + np.cos(lat) * np.cos(np.deg2rad(decl)) * np.cos(hour_angle)
    radiation = np.clip(1000 * sin_elev, 0, None) * (1 - 0.75 * cloud)

    humidity = np.clip(65 + 15 * np.cos(2 * np.pi * (doy - 200) / 365) + 25 * cloud - 8 * np.sin(2 * np.pi * (hour - 9) / 24) + rng.normal(0, 4, n), 15, 100)
    windspeed = np.clip(3 + _ar1(n, 0.97, 0.35, rng), 0, None)
    pressure = 1013 + _ar1(n, 0.995, 0.3, rng)

    # --- 需要 ---------------------------------------------------------
    jp_holidays = holidays.country_holidays("JP", years=range(idx.year.min(), idx.year.max() + 1))
    dates = idx.date
    is_holiday = np.array([d in jp_holidays for d in dates])
    md = idx.strftime("%m-%d")
    obon = (md >= "08-13") & (md <= "08-16")
    year_end = (md >= "12-29") | (md <= "01-03")
    dow = idx.dayofweek.to_numpy()
    day_type = np.where(is_holiday | obon | year_end | (dow == 6), 2, np.where(dow == 5, 1, 0))  # 0平日 1土曜 2休日

    # 業務用の日内パターン（平日は8〜19時が高い）
    office = 0.35 + 0.65 / (1 + np.exp(-(hour - 8.0) * 2.2)) * (1 / (1 + np.exp((hour - 19.5) * 1.6)))
    lunch_dip = -0.05 * np.exp(-((hour - 12.25) ** 2) / 0.15)
    shape = office + lunch_dip
    scale = np.choose(day_type, [1.0, 0.62, 0.48])
    base_kw = 14_000 + 22_000 * shape * scale

    cooling = np.clip(temperature - 22, 0, None) ** 1.25 * 900          # 冷房
    heating = np.clip(14 - temperature, 0, None) * 750                  # 暖房
    occupancy = np.where(day_type == 0, 1.0, 0.55) * (0.45 + 0.55 * shape)
    load = base_kw + (cooling + heating) * occupancy
    load *= 1 + 0.03 * (idx - idx[0]).days.to_numpy() / 365             # 顧客獲得による増加
    load += _ar1(n, 0.9, 450, rng)
    load = np.clip(load, 5_000, None)

    # --- JEPXスポット価格 ---------------------------------------------
    solar_dip = -5.5 * np.clip(sin_elev, 0, None) * (1 - cloud) * (1 + 0.5 * np.sin(2 * np.pi * (doy - 80) / 365))
    evening = 4.0 * np.exp(-((hour - 18) ** 2) / 3)
    weather_term = 0.035 * (cooling + heating) / 100
    spot = 11 + evening + solar_dip + weather_term + 2.5 * np.cos(4 * np.pi * (doy - 15) / 365) + _ar1(n, 0.97, 0.6, rng)
    spot = np.clip(spot, 0.01, None)

    df = pd.DataFrame(
        {
            "load": load.round(1),
            "temperature": temperature.round(2),
            "relative_humidity": humidity.round(1),
            "windspeed": windspeed.round(2),
            "pressure": pressure.round(1),
            "radiation": radiation.round(1),
            "jepx_spot": spot.round(2),
        },
        index=idx,
    )
    df.index.name = "timestamp"
    return df


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "data/sample_30min.csv"
    d = generate()
    d.to_csv(out)
    print(f"{out}: {len(d):,} 行, {d.index.min()} 〜 {d.index.max()}")
