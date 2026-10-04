---
name: daily-plan
description: 指定日（省略時は翌日）の48コマ需給計画を作り、需要計画・JEPX入札量・注意すべきコマを報告する。日次の計画作成を頼まれたときに使う。
argument-hint: "[YYYY-MM-DD] [--data path]"
allowed-tools: Bash(.venv/bin/python run_daily.py *) Bash(.venv/bin/python -m jukyu.dashboard *) Read
---

# 翌日需給計画の作成

引数: `$ARGUMENTS`（対象日 YYYY-MM-DD。省略時は今日の翌日。`--data` でデータファイルを指定できる）

## 手順

1. 対象日を決める。引数がなければ今日の翌日（JST）。入力データの末尾が対象日の前日 9:00 より前なら、予測に必要な実績が足りないので実行せずにその旨を伝える。
2. 計画を作る（過去3日の検証も付ける）:
   ```
   .venv/bin/python run_daily.py --date <対象日> --backtest-days 3 [--data <path>]
   ```
3. ダッシュボードを更新する:
   ```
   .venv/bin/python -m jukyu.dashboard output/results.json output/dashboard.html
   ```
4. `output/<対象日>/需給計画_<対象日>.csv` と `JEPX入札_<対象日>.csv` を読み、次を報告する。

## 報告の形

- 需要計画（MWh）、最大需要計画（kW）とそのコマ
- JEPXスポット買・売の合計（MWh）と、買入札量が最も大きい3コマ
- 予測幅（P90−P10）が広いコマ上位3つ。不足リスクとして入札量の上乗せを検討する候補
- 直近3日の検証の MAPE とインバランス損失。MAPE が7%を超えた日があれば原因の見立て（祝日・急な気温変化など）
- 出力ファイルのパス

## 注意

- 入札・計画提出は実行しない。ファイルを作って報告するところまで。
- サンプルデータで実行した場合は、結果がサンプルであることを冒頭に書く。
