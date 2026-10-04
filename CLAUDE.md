# CLAUDE.md

新電力（小売電気事業者）向けの需給管理プロトタイプ。OpenSTEF v4 で翌日30分×48コマの需要を予測し、調達計画・JEPXスポット入札量・インバランス評価・計画CSVを作る。

## 環境とコマンド

- Python 3.12 以上（OpenSTEF v4 の要件）。仮想環境は `.venv/`。
- Python は必ず仮想環境のものを使う：`.venv/bin/python`（Windows は `.venv\Scripts\python.exe`）。以下では `PY` と書く。
- 初回セットアップ：`python3.12 -m venv .venv && PY -m pip install -r requirements-dev.txt`

| 目的 | コマンド |
|---|---|
| サンプルデータ生成 | `PY -m jukyu.sample_data data/sample_30min.csv` |
| 翌日計画＋検証 | `PY run_daily.py --date YYYY-MM-DD --backtest-days 14` |
| ダッシュボード生成 | `PY -m jukyu.dashboard output/results.json output/dashboard.html` |
| 入力データの検証 | `PY tools/validate_data.py data/xxx.csv` |
| テスト（数秒） | `PY -m pytest -q` |

予測は1日あたりCPUで7秒前後。14日の検証で約2分かかるので、コードを変えたあとの確認はまず `pytest`、次に `--backtest-days 1` で行う。

## 構成

```
run_daily.py            日次実行の入口（予測→計画→評価→CSV→results.json）
jukyu/config.py         BG名・エリア・相対契約・太陽光・入札方針（ここだけ変えれば電源構成が変わる）
jukyu/forecast.py       OpenSTEFのラッパー。前日9:00時点の情報だけで翌日48コマを予測
jukyu/planning.py       調達計画・JEPX入札量・インバランス評価・サマリー
jukyu/export.py         CSV出力（UTF-8 BOM付き、Excel向け）
jukyu/dashboard.py      results.json を dashboard_template.html に埋め込む
jukyu/sample_data.py    合成サンプルデータ
tools/validate_data.py  入力CSVのスキーマ・欠損・間隔チェック
tests/                  OpenSTEFの学習を伴わない単体テスト
```

## 守ること（ドメインの約束）

- **単位を列名の接尾辞で区別する。** `_kw` は30分平均電力（kW）＝JEPX入札量の kWh/h と同じ数値、`_kwh` は1コマの電力量（kW×0.5）。新しい列にも必ず付ける。kW と kWh を混ぜて足さない。
- **時刻は内部では JST（Asia/Tokyo）。** OpenSTEF に渡す直前だけ UTC に変換する（`forecast._to_dataset`）。タイムスタンプはコマの開始時刻。1日は必ず48コマで、23:30開始のコマの時刻帯表記は `23:30-24:00`。
- **予測時点より後の実績を使わない（リーク禁止）。** 予測時点は前日 9:00（`FORECAST_HOUR`、JEPXスポット入札締切 10:00 の前）。需要実績は予測時点以降を欠損にしてから学習・予測する。JEPX価格は予測の特徴量に入れない。
- **祝日は日本の暦。** `LocationConfig.country_code="JP"`。お盆・年末年始はサンプルデータ側で休日扱いにしている。
- **計画差（調達−需要計画）は入札量の丸め分だけ残る。** 広域機関への提出には一致させる処理が別途必要（未実装）。
- **インバランス単価は「スポット価格×係数」の簡易モデル。** 実際の精算額と称して表示しない。正式な算定式を実装する場合は `planning.evaluate_imbalance` を差し替える。

## してはいけないこと

- 広域機関（OCCTO）への計画提出や JEPX への入札を、自動で実行するコードを書かない。出力は人が確認するためのファイルまでにする。
- 制度上の数値（入札単位、ゲートクローズ時刻、インバランス料金の算定式など）を推測で書かない。不明なら TODO として残し、ユーザーに確認する。
- `data/` の実データ（需要家の使用量）をログ・チャット・外部サービスに出さない。集計値で話す。
- サンプルデータの結果を実績や実在事業者の数値として説明しない。

## 用語

| 用語 | 意味 |
|---|---|
| コマ | 30分の計画単位。1日48コマ |
| 同時同量 | コマごとに調達計画と需要実績を一致させる義務 |
| BG | バランシンググループ。需給を一括で管理する単位 |
| インバランス | 調達計画と実績の差。不足・余剰とも精算される |
| JEPXスポット | 前日10:00締切の一日前市場 |
| 時間前市場 | 実需給1時間前（ゲートクローズ）までの調整市場。未実装 |
| P10/P50/P90 | 予測の分位点。入札基準は `PlanningConfig.bid_quantile` |

## 変更したら

1. `PY -m pytest -q` が通ること。
2. 予測や計画のロジックを変えたら `PY run_daily.py --date 2026-10-05 --backtest-days 3` を流し、MAPE とインバランス損失が変更前より悪化していないか比べて報告する。
3. results.json の項目を変えたら `dashboard_template.html` 側の参照も直し、ダッシュボードを再生成する。
