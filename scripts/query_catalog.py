"""保存済みカタログを読み直して絞り込む（I/O 層）。

メモリ上のオブジェクトではなく stac/catalog.json から読むのが要点。
そうでないと「カタログが索引として働く」ことの確認にならない。

実行::

    uv run python scripts/query_catalog.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pystac

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cogstac.stac_query import filter_items  # noqa: E402

# 西半分。継ぎ目（経度 139.58 付近）まで届かせないこと。
# 届くと4枚すべてがヒットする（シーンは斜めに重なっているため）。
_WEST_HALF = [138.9521, 35.4046, 139.5736, 36.0176]
_JULY_START = datetime(2026, 7, 1, tzinfo=UTC)
_JULY_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)


def main() -> None:
    catalog = pystac.Catalog.from_file("stac/catalog.json")
    # Collection を挟んだので recursive が要る
    items = list(catalog.get_items(recursive=True))
    print(f"カタログ全体: {len(items)} 枚")

    for label, kwargs in [
        ("空間だけ", {"bbox": _WEST_HALF}),
        ("時間だけ", {"start": _JULY_START, "end": _JULY_END}),
        ("空間 AND 時間", {"bbox": _WEST_HALF, "start": _JULY_START, "end": _JULY_END}),
    ]:
        hits = filter_items(items, **kwargs)
        print(f"{label}: {len(hits)} 枚  {[i.id for i in hits]}")


if __name__ == "__main__":
    main()