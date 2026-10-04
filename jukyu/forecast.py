"""OpenSTEF v4 を使った翌日48コマの需要予測。

運用イメージ:
    前日 9:00（JEPXスポット入札締切 10:00 の前）に予測を実行し、
    翌日 0:00〜24:00 の30分コマごとの需要を分位点付き（P10/P50/P90）で出す。
"""

from __future__ import annotations

import logging
import os
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
warnings.filterwarnings("ignore")
logging.getLogger("openstef_core").setLevel(logging.WARNING)
logging.getLogger("openstef_models").setLevel(logging.WARNING)

from openstef_core.datasets import TimeSeriesDataset  # noqa: E402
from openstef_core.types import LeadTime, Q  # noqa: E402
from openstef_models.presets import ForecastingWorkflowConfig, create_forecasting_workflow  # noqa: E402
from openstef_models.presets.forecasting_workflow import LocationConfig  # noqa: E402

from .config import SETTINGS, SLOT_MINUTES, TZ, Settings  # noqa: E402

WEATHER_COLUMNS = ["temperature", "relative_humidity", "windspeed", "pressure", "radiation"]
FORECAST_HOUR = 9          # 前日 9:00 に予測を作る
TRAIN_DAYS = 365           # 学習に使う期間
HISTORY_DAYS = 14          # ラグ特徴量のために予測時に渡す過去データ


def _to_dataset(df: pd.DataFrame) -> TimeSeriesDataset:
    data = df[["load", *WEATHER_COLUMNS]].copy()
    data.index = data.index.tz_convert("UTC")
    data.index.name = "timestamp"
    return TimeSeriesDataset(data, sample_interval=timedelta(minutes=SLOT_MINUTES))


def forecast_next_day(
    df: pd.DataFrame,
    target_date: str,
    settings: Settings = SETTINGS,
    model: str = "xgboost",
) -> tuple[pd.DataFrame, dict]:
    """target_date（JST、YYYY-MM-DD）の48コマ需要予測を返す。

    Args:
        df: 30分値の実績・気象データ（index は tz付き日時）。
        target_date: 計画対象日。
        model: OpenSTEFのモデル名（"gblinear" / "xgboost" / "lgbm" など）。

    Returns:
        (予測DataFrame[JST, 列 p10/p50/p90 kW], 学習メトリクス)
    """
    day = pd.Timestamp(target_date, tz=TZ)
    issue_time = day - pd.Timedelta(days=1) + pd.Timedelta(hours=FORECAST_HOUR)
    day_end = day + pd.Timedelta(days=1)
    horizon_hours = int((day_end - issue_time) / pd.Timedelta(hours=1)) + 1

    # 予測時点までの実績で学習（気象は翌日分まで「予報」として使う）
    known = df.copy()
    known.loc[known.index >= issue_time, "load"] = float("nan")
    train = known.loc[(known.index >= issue_time - pd.Timedelta(days=TRAIN_DAYS)) & (known.index < issue_time)]
    predict = known.loc[(known.index >= issue_time - pd.Timedelta(days=HISTORY_DAYS)) & (known.index < day_end)]

    area = settings.area
    config = ForecastingWorkflowConfig(
        model_id=f"jukyu_{model}",
        model=model,
        sample_interval=timedelta(minutes=SLOT_MINUTES),
        horizons=[LeadTime.from_string(f"PT{horizon_hours}H")],
        quantiles=[Q(0.5), *[Q(q) for q in settings.quantiles if q != 0.5]],
        target_column="load",
        temperature_column="temperature",
        relative_humidity_column="relative_humidity",
        wind_speed_column="windspeed",
        pressure_column="pressure",
        radiation_column="radiation",
        location=LocationConfig(
            name=area.name,
            country_code=area.country_code,
            coordinate={"latitude": area.latitude, "longitude": area.longitude},
        ),
        verbosity=0,
        mlflow_storage=None,
    )
    workflow = create_forecasting_workflow(config=config)
    result = workflow.fit(_to_dataset(train))
    metrics = {}
    if result is not None and result.metrics_full is not None:
        metrics = result.metrics_full.to_dataframe().round(4).to_dict()

    fc = workflow.predict(_to_dataset(predict), forecast_start=issue_time.tz_convert("UTC").to_pydatetime())
    q = fc.quantiles_data.copy()
    q.index = pd.DatetimeIndex(q.index).tz_convert(TZ)
    q.columns = [_quantile_label(c) for c in q.columns]
    q = q.loc[(q.index >= day) & (q.index < day_end)]

    # 分位点の交差を防ぐ（P10 ≤ P50 ≤ P90）
    cols = sorted(q.columns, key=lambda c: int(c[1:]))
    q = q[cols]
    q = pd.DataFrame(np.sort(q.to_numpy(), axis=1), index=q.index, columns=cols)

    if len(q) != 48:
        raise RuntimeError(f"予測コマ数が48ではありません: {len(q)}")
    return q.round(1), metrics


def _quantile_label(col) -> str:
    s = str(col)
    for token in s.replace("quantile_", "").replace("P", "").split("_"):
        try:
            v = float(token)
        except ValueError:
            continue
        if v < 1:
            v *= 100
        return f"p{int(round(v))}"
    return s
