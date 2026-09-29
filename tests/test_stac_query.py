"""stac_query の純粋ロジックのテスト。

GDAL もネットワークもファイルも使わない。dict と datetime を渡すだけで回る。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pystac
import pytest

from cogstac.stac_query import bboxes_intersect, filter_items, is_within

_SCENE_NW = [138.96958, 35.89285, 139.59541, 36.50837]
_SCENE_NE = [139.58452, 35.90154, 140.20606, 36.51409]
_INTERSECT_CASES = [
    # 半分ずつ重なる
    ([0, 0, 2, 2], [1, 1, 3, 3], True),
    # a が b を完全に含む
    ([0, 0, 4, 4], [1, 1, 2, 2], True),
    # 辺が接するだけ（docstring の方針どおり「重なる」）
    ([0, 0, 1, 1], [1, 1, 2, 2], True),
    # 完全に離れている
    ([0, 0, 1, 1], [5, 5, 6, 6], False),
    # 経度は重なるが緯度は重ならない（軸を混ぜた実装を捕まえる）
    ([0, 0, 2, 2], [1, 3, 3, 5], False),
]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    _INTERSECT_CASES,
)
def test_bboxes_intersect(a: list, b: list, expected: bool) -> None:
    assert bboxes_intersect(a, b) is expected


@pytest.mark.parametrize(("a", "b", "expected"), _INTERSECT_CASES)
def test_bboxes_intersect_is_symmetric(a: list[float], b: list[float], expected: bool) -> None:
    assert bboxes_intersect(a, b) is bboxes_intersect(b, a)


def test_adjacent_scenes_overlap_at_the_seam() -> None:
    """UTM から投影したシーンは継ぎ目で重なる。
    「西半分」を検索するとき、この重なりを踏むと4枚すべてがヒットする。
    """
    assert bboxes_intersect(_SCENE_NW, _SCENE_NE) is True


def test_is_within_includes_both_ends() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
    assert is_within(start, start, end) is True  # 下端ちょうど
    assert is_within(end, start, end) is True  # 上端ちょうど

    moment = datetime(2026, 8, 1, tzinfo=UTC)
    assert is_within(moment, start, end) is False  # 範囲外（8月）


def _item(item_id: str, bbox: list[float], acquired: datetime) -> pystac.Item:
    """filter_items が見るのは bbox と datetime だけ。他は最小限で埋める。

    stac_build.build_item を使わないのは、あちらのバグでこのテストが
    無関係な理由で落ちるのを避けるため。geometry は必須なので bbox と揃えた矩形を入れる。
    """
    west, south, east, north = bbox
    return pystac.Item(
        id=item_id,
        geometry={
            "type": "Polygon",
            "coordinates": [
                [
                    [west, south],
                    [east, south],
                    [east, north],
                    [west, north],
                    [west, south],
                ]
            ],
        },
        bbox=bbox,
        datetime=acquired,
        properties={},
    )


# 架空の配置。継ぎ目の重なりは上のテストが見るので、ここでは重ねない（期待値を自明にする）。
# 各軸だけで2枚・AND で1枚になるよう置くのが肝。3つの答えがすべて違う。
_SEARCH_WEST = [0.0, 0.0, 10.0, 10.0]
_JULY_START = datetime(2026, 7, 1, tzinfo=UTC)
_JULY_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)

_ITEMS = [
    _item("west_july", [1.0, 1.0, 4.0, 4.0], datetime(2026, 7, 15, tzinfo=UTC)),
    _item("east_july", [20.0, 1.0, 24.0, 4.0], datetime(2026, 7, 15, tzinfo=UTC)),
    _item("west_august", [1.0, 1.0, 4.0, 4.0], datetime(2026, 8, 15, tzinfo=UTC)),
]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # 空間だけ。時間条件が勝手に効いていれば west_august が落ちる
        ({"bbox": _SEARCH_WEST}, ["west_july", "west_august"]),
        # 時間だけ。空間条件が勝手に効いていれば east_july が落ちる
        ({"start": _JULY_START, "end": _JULY_END}, ["west_july", "east_july"]),
        # 両方。AND が OR になっていれば3枚返る
        ({"bbox": _SEARCH_WEST, "start": _JULY_START, "end": _JULY_END}, ["west_july"]),
        # 条件なしは素通し。None 判定を壊したときの番人
        ({}, ["west_july", "east_july", "west_august"]),
    ],
)
def test_filter_items_narrows_by_space_and_time(
    kwargs: dict[str, Any], expected: list[str]
) -> None:
    """条件の取り違え・片方の無視・AND/OR の誤りを、3つの答えの違いで捕まえる。

    リストで比較するので、入力の順序が保たれることも同時に固定している。
    """
    assert [item.id for item in filter_items(_ITEMS, **kwargs)] == expected
