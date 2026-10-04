"""results.json からダッシュボード（単一HTMLファイル）を作る。

使い方:
    python -m jukyu.dashboard output/results.json output/dashboard.html
"""

from __future__ import annotations

import sys
from pathlib import Path

TEMPLATE = Path(__file__).with_name("dashboard_template.html")


def build(results_json: Path, out_html: Path) -> Path:
    data = results_json.read_text(encoding="utf-8")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data.replace("</", "<\\/"))
    out_html.write_text(html, encoding="utf-8")
    return out_html


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "output/results.json")
    dst = Path(sys.argv[2] if len(sys.argv) > 2 else "output/dashboard.html")
    print(build(src, dst))
