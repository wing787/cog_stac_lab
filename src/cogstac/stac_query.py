"""保存済み STAC カタログから、条件に合う Item を引く（純粋ロジック）。

pystac にも datetime にも依存する箇所はあるが、ファイルもネットワークも触らない。
カタログを読むのは I/O 層（scripts/）の責任。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

import pystac


def bboxes_intersect(a: Sequence[float], b: Sequence[float]) -> bool:
    """2つの bbox が重なるか。どちらも [west, south, east, north]。

    辺が接するだけ（a の東端 == b の西端）は「重なる」と扱う。
    継ぎ目の地物を取りこぼさないためだが、二重にヒットする副作用がある。
    """
    # 「重ならない」= 次の4つのどれか。west=0, south=1, east=2, north=3
    return not (
        a[2] < b[0]   # a が完全に西（a の東端が b の西端より西）
        or a[0] > b[2]  # a が完全に東（a の西端が b の東端より東）
        or a[3] < b[1]  # a が完全に南（a の北端が b の南端より南）
        or a[1] > b[3]  # a が完全に北（a の南端が b の北端より北）
    )


def is_within(
        moment: datetime,
        start: datetime | None,
        end: datetime | None
) -> bool:
    """moment が [start, end] に入るか（両端を含む）。
    None は無制限として扱う。与えた値はすべて tz-aware であること。
    """
    if start is not None and moment < start:
        return False
    
    if end is not None and end < moment:
        return False

    return True



def filter_items(
    items: Iterable[pystac.Item],
    bbox: Sequence[float] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[pystac.Item]:
    """空間・時間の条件で Item を絞る。None の条件は「指定なし」として素通しする。

    Item から値を取り出すのはここ。bboxes_intersect と is_within は
    pystac を知らないまま素の値で完結する。
    """
    result = []
    for item in items:
        if bbox is not None and not bboxes_intersect(item.bbox, bbox):
            continue
        if not is_within(item.datetime, start, end):
            continue
        result.append(item)
    return result