---
name: import-data
description: 需要実績（送配電の30分値など）・気象データ・JEPX価格のファイルを、このプロジェクトの入力形式（30分間隔の統合CSV）に変換して検証する。実データを取り込みたいときに使う。
argument-hint: "<ファイルパス...>"
allowed-tools: Bash(.venv/bin/python tools/validate_data.py *) Bash(.venv/bin/python -c *) Read Write
---

# 実データの取り込み

対象ファイル: `$ARGUMENTS`

## 目標の形式

`data/<名前>_30min.csv`。列は次の通り（README の「実データへの差し替え」と同じ）:

| 列 | 単位 | 必須 |
|---|---|---|
| `timestamp` | タイムゾーン付き日時（JST、コマ開始時刻） | ○ |
| `load` | kW（30分平均） | ○ |
| `temperature` | ℃ | ○ |
| `relative_humidity` | % | ○ |
| `windspeed` | m/s | ○ |
| `pressure` | hPa | ○ |
| `radiation` | W/m² | ○ |
| `jepx_spot` | 円/kWh | 計画のコスト計算に使う。なければ省略可 |

## 手順

1. 各ファイルの先頭20行と行数・文字コード（Shift_JIS のことが多い）を確認し、列の意味をユーザーに確かめる。推測で単位を決めない。
2. よくある変換:
   - 需要が **kWh/コマ** なら ×2 して kW にする。需要家別なら BG 合計に集計する。
   - 時刻が「コマ終了時刻」（例 0:30 が1コマ目）なら30分引いて開始時刻にする。「24:00」表記は翌日0:00に直す。
   - 気象が1時間値なら30分に線形補間する（日射量は0未満にしない）。
   - JEPX のエリアプライスは対象エリアの列を使う。
3. 変換スクリプトは `tools/convert_<データ名>.py` として保存し、何度でも再実行できるようにする。
4. 検証する:
   ```
   .venv/bin/python tools/validate_data.py data/<名前>_30min.csv
   ```
   エラーがなくなるまで直す。警告（欠損コマ、外れ値）は内容をユーザーに報告して判断を仰ぐ。
5. 最後に `--backtest-days 3` で動作確認する（`/daily-plan` または `/backtest`）。

## 注意

- 需要家ごとのデータは個人・企業の情報にあたる。中身をチャットに貼らず、件数・期間・集計値で報告する。
- 元ファイルは書き換えない。変換結果は `data/` に新しいファイルとして作る。
