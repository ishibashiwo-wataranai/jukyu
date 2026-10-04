"""検証用のサンプルデータ（30分値）を生成する。

エリアごとに合成データを作ります（すべて架空の値で、実在の事業者・地点の実績ではありません）。

    tokyo   東京エリア。高圧の業務用需要家（オフィス・店舗・工場）中心、最大需要およそ5万kW
    tohoku  東北エリア（陸前高田付近の気候を想定）。業務用・工場中心で暖房需要が大きく、
            冬にピークが来る。最大需要およそ2万kW

実データに差し替える場合は、同じ列名・30分間隔のCSVを用意してください（README参照）。

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

from dataclasses import dataclass

from .config import AREAS, TZ


@dataclass(frozen=True)
class SampleProfile:
    """合成データの形を決めるパラメータ（気候・需要規模の目安。実測値ではない）。"""

    seed: int
    temp_mean: float            # 年平均気温（℃）
    temp_amplitude: float       # 季節変動の振幅（℃）
    temp_diurnal: float         # 日較差の半分（℃）
    wind_mean: float            # 平均風速（m/s）
    base_kw: float              # 夜間・休日も残る需要（kW）
    shape_kw: float             # 平日昼間に上乗せされる需要（kW）
    cooling_from: float         # 冷房が効き始める気温（℃）
    cooling_kw: float           # 冷房需要の係数
    heating_below: float        # 暖房が効き始める気温（℃）
    heating_kw: float           # 暖房需要の係数（kW/℃）
    noise_kw: float             # 需要のゆらぎ（kW）
    min_kw: float               # 需要の下限（kW）
    spot_offset: float          # スポット価格の水準差（円/kWh）


PROFILES: dict[str, SampleProfile] = {
    "tokyo": SampleProfile(
        seed=7, temp_mean=16.5, temp_amplitude=10.5, temp_diurnal=4.0, wind_mean=3.0,
        base_kw=14_000, shape_kw=22_000, cooling_from=22, cooling_kw=900, heating_below=14, heating_kw=750,
        noise_kw=450, min_kw=5_000, spot_offset=0.0,
    ),
    "tohoku": SampleProfile(
        seed=11, temp_mean=12.0, temp_amplitude=10.5, temp_diurnal=4.5, wind_mean=3.5,
        base_kw=5_000, shape_kw=8_000, cooling_from=23, cooling_kw=300, heating_below=15, heating_kw=450,
        noise_kw=200, min_kw=2_000, spot_offset=-0.5,
    ),
}


def _ar1(n: int, phi: float, sigma: float, rng: np.random.Generator) -> np.ndarray:
    x = np.zeros(n)
    eps = rng.normal(0, sigma, n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + eps[i]
    return x


def generate(start: str = "2025-04-01", end: str = "2026-10-06", seed: int | None = None, area: str = "tokyo") -> pd.DataFrame:
    prof = PROFILES[area]
    rng = np.random.default_rng(prof.seed if seed is None else seed)
    idx = pd.date_range(start, end, freq="30min", tz=TZ, inclusive="left")
    n = len(idx)
    doy = idx.dayofyear.to_numpy()
    hour = (idx.hour + idx.minute / 60).to_numpy()

    # --- 気象 ---------------------------------------------------------
    seasonal = prof.temp_mean - prof.temp_amplitude * np.cos(2 * np.pi * (doy - 20) / 365)          # 1月下旬が最低
    diurnal = prof.temp_diurnal * np.sin(2 * np.pi * (hour - 9) / 24)                     # 15時ごろ最高
    weather_dev = _ar1(n, 0.995, 0.25, rng)                                 # 数日単位の寒暖
    temperature = seasonal + diurnal + weather_dev

    cloud = np.clip(0.45 + _ar1(n, 0.98, 0.08, rng), 0, 1)                 # 雲量 0〜1
    decl = 23.44 * np.sin(2 * np.pi * (doy - 81) / 365)
    lat = np.deg2rad(AREAS[area].area.latitude)
    hour_angle = np.deg2rad(15 * (hour + 0.25 - 12))
    sin_elev = np.sin(lat) * np.sin(np.deg2rad(decl)) + np.cos(lat) * np.cos(np.deg2rad(decl)) * np.cos(hour_angle)
    radiation = np.clip(1000 * sin_elev, 0, None) * (1 - 0.75 * cloud)

    humidity = np.clip(65 + 15 * np.cos(2 * np.pi * (doy - 200) / 365) + 25 * cloud - 8 * np.sin(2 * np.pi * (hour - 9) / 24) + rng.normal(0, 4, n), 15, 100)
    windspeed = np.clip(prof.wind_mean + _ar1(n, 0.97, 0.35, rng), 0, None)
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
    base_kw = prof.base_kw + prof.shape_kw * shape * scale

    cooling = np.clip(temperature - prof.cooling_from, 0, None) ** 1.25 * prof.cooling_kw   # 冷房
    heating = np.clip(prof.heating_below - temperature, 0, None) * prof.heating_kw          # 暖房
    occupancy = np.where(day_type == 0, 1.0, 0.55) * (0.45 + 0.55 * shape)
    load = base_kw + (cooling + heating) * occupancy
    load *= 1 + 0.03 * (idx - idx[0]).days.to_numpy() / 365             # 顧客獲得による増加
    load += _ar1(n, 0.9, prof.noise_kw, rng)
    load = np.clip(load, prof.min_kw, None)

    # --- JEPXスポット価格 ---------------------------------------------
    solar_dip = -5.5 * np.clip(sin_elev, 0, None) * (1 - cloud) * (1 + 0.5 * np.sin(2 * np.pi * (doy - 80) / 365))
    evening = 4.0 * np.exp(-((hour - 18) ** 2) / 3)
    weather_term = 0.035 * (cooling + heating) / 100
    spot = 11 + prof.spot_offset + evening + solar_dip + weather_term + 2.5 * np.cos(4 * np.pi * (doy - 15) / 365) + _ar1(n, 0.97, 0.6, rng)
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

    # 使い方: python -m jukyu.sample_data [出力先] [エリア]
    #   エリアを省略すると tokyo。出力先を省略すると config の data_path
    area = sys.argv[2] if len(sys.argv) > 2 else "tokyo"
    out = sys.argv[1] if len(sys.argv) > 1 else AREAS[area].area.data_path
    d = generate(area=area)
    d.to_csv(out, lineterminator="\n")
    print(f"{out}: {len(d):,} 行, {d.index.min()} 〜 {d.index.max()}")
