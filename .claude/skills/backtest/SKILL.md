---
name: backtest
description: 過去の期間で予測と計画を検証し、モデルや入札基準分位点ごとの MAPE・インバランス量・損失を比較する。予測精度の確認やモデル・設定の比較を頼まれたときに使う。
argument-hint: "[終了日 YYYY-MM-DD] [日数] [モデル名...]"
allowed-tools: Bash(.venv/bin/python run_daily.py *) Bash(.venv/bin/python -c *) Read
---

# バックテスト

引数: `$ARGUMENTS`
- 1つ目: 検証の終了日の翌日（`--date` に渡す日。省略時はデータ末尾の翌日）
- 2つ目: 検証日数（省略時 14）
- 3つ目以降: 比較するモデル（省略時 `xgboost gblinear`）

## 手順

1. モデルごとに出力先を分けて実行する（1日7秒前後。日数×モデル数で時間を見積もり、5分を超えるなら先にユーザーに伝える）:
   ```
   .venv/bin/python run_daily.py --date <日付> --backtest-days <日数> --model <モデル> --out output/bt_<モデル>
   ```
   全エリアが対象になる。1エリアだけにするときは `--area <エリア>` を付ける。
2. 各 `output/bt_<モデル>/results.json` の `areas[].days[].summary` から、検証日（`has_actual: true`）についてエリアごとに集計する（エリアをまたいで平均しない）:
   平均MAPE、最大MAPEの日、不足・余剰インバランス合計（MWh）、インバランス損失合計（万円）。
3. 入札基準分位点の比較を頼まれた場合は、`jukyu/config.py` の該当エリアの `PlanningConfig.bid_quantile` を変えて実行する。終わったら元の値に戻し、戻したことを報告する。

## 報告の形

- エリア別・モデル（または設定）ごとの集計表
- 曜日・祝日別に誤差が大きい日の傾向
- どれを採用すべきかの提案と、その理由（損失合計を主、MAPEを従に見る）
- サンプルデータの場合はその旨
