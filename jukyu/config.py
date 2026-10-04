"""需給管理プロトタイプの設定。

実運用ではここを自社のBG・電源構成に合わせて書き換えます。
"""

from dataclasses import dataclass, field

SLOT_MINUTES = 30          # 同時同量の計画単位（30分コマ）
SLOTS_PER_DAY = 48
TZ = "Asia/Tokyo"


@dataclass
class AreaConfig:
    """供給エリア（予測の祝日・座標に使用）。"""

    key: str = "tokyo"                          # コマンドライン・出力フォルダ用の識別子
    name: str = "東京エリア"
    latitude: float = 35.68
    longitude: float = 139.76
    country_code: str = "JP"
    data_path: str = "data/sample_30min.csv"    # このエリアの30分値データ


@dataclass
class ContractConfig:
    """相対契約（ブロック電源）。kWは30分平均出力。"""

    name: str
    kw_day: float      # 昼間帯（8:00-22:00）
    kw_night: float    # 夜間帯


@dataclass
class SolarConfig:
    """太陽光（FIT特定卸・自社電源など）。"""

    name: str = "太陽光（FIT特定卸）"
    capacity_kw: float = 12_000.0
    performance_ratio: float = 0.78


@dataclass
class PlanningConfig:
    """調達計画のパラメータ。"""

    # JEPXスポット買い入札の基準にする予測分位点（0.5=中央値）。
    # 不足インバランスを避けたい場合は 0.6〜0.7 に上げる。
    bid_quantile: float = 0.5
    # スポット買い入札の上限価格（円/kWh）。これを超えるコマは約定しないものとして扱う。
    bid_price_cap_yen: float = 40.0
    # 余剰時の売り入札最低価格（円/kWh）
    sell_floor_yen: float = 0.01
    # インバランス単価の簡易モデル：スポット価格 × 係数（不足は高く、余剰は安く精算）
    shortage_price_factor: float = 1.3
    surplus_price_factor: float = 0.7


@dataclass
class Settings:
    """1エリア分の設定。同時同量の計画はエリア（BG）ごとに立てる。"""

    bg_name: str = "サンプル新電力 東京BG"
    area: AreaConfig = field(default_factory=AreaConfig)
    contracts: list[ContractConfig] = field(
        default_factory=lambda: [
            ContractConfig(name="相対契約A（ベース）", kw_day=18_000, kw_night=18_000),
            ContractConfig(name="相対契約B（昼間）", kw_day=8_000, kw_night=0),
        ]
    )
    solar: SolarConfig = field(default_factory=SolarConfig)
    planning: PlanningConfig = field(default_factory=PlanningConfig)
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9)


# 管理するエリア。追加するときはここに Settings を足し、data_path のデータを用意する。
AREAS: dict[str, Settings] = {
    "tokyo": Settings(),
    "tohoku": Settings(
        bg_name="サンプル新電力 東北BG",
        # 座標は岩手県陸前高田市付近（気象・日射の地点）
        area=AreaConfig(
            key="tohoku",
            name="東北エリア",
            latitude=39.02,
            longitude=141.63,
            data_path="data/sample_tohoku_30min.csv",
        ),
        contracts=[
            ContractConfig(name="相対契約A（ベース）", kw_day=7_000, kw_night=7_000),
            ContractConfig(name="相対契約B（昼間）", kw_day=3_000, kw_night=0),
        ],
        solar=SolarConfig(capacity_kw=6_000.0),
    ),
}

SETTINGS = AREAS["tokyo"]   # エリアを指定しない処理の既定値
