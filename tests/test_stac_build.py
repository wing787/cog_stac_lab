"""stac_build の純粋ロジックのテスト。

GDAL もネットワークもファイルも使わない。dict と datetime を渡すだけで回る。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cogstac.stac_build import (
    SceneMetadata,
    bbox_from_geometry,
    build_catalog,
    build_collection,
    build_item,
    union_bbox,
)

# scene_nw の実際の footprint（UTM の長方形を WGS84 に投影したもの＝長方形ではない）。
_FOOTPRINT = {
    "type": "Polygon",
    "coordinates": [
        [
            [138.96958, 36.49949],
            [138.98519, 35.89285],
            [139.59541, 35.90154],
            [139.58452, 36.50837],
            [138.96958, 36.49949],
        ]
    ],
}


def _metadata() -> SceneMetadata:
    return SceneMetadata(
        scene_id="scene_nw",
        footprint=_FOOTPRINT,
        acquired=datetime(2026, 7, 1, 1, 30, tzinfo=UTC),
        epsg=32654,
        shape=(3329, 2724),
        transform=(20.2251, 0.0, 318152.9, 0.0, -20.2251, 4041268.1),
        asset_href="/tmp/scene_nw.tif",
    )


def test_bbox_order_is_west_south_east_north() -> None:
    """RFC 7946 の順序（経度が先）。緯度経度を入れ替えても検証は通るので、ここで守る。"""
    assert bbox_from_geometry(_FOOTPRINT) == [138.96958, 35.89285, 139.59541, 36.50837]


def test_bbox_is_degrees_not_metres() -> None:
    """UTM 座標が漏れていないことの番人。

    native CRS のまま入れても JSON スキーマ検証は通ってしまう（数値4つに見えるだけ）ので、
    値域で検出する。
    """
    west, south, east, north = bbox_from_geometry(_FOOTPRINT)
    assert -180 <= west < east <= 180
    assert -90 <= south < north <= 90


def test_bbox_contains_every_vertex() -> None:
    """外接矩形なので、全頂点を含む。footprint が長方形でなくても成り立つ。"""
    west, south, east, north = bbox_from_geometry(_FOOTPRINT)
    for lon, lat in _FOOTPRINT["coordinates"][0]:
        assert west <= lon <= east
        assert south <= lat <= north


def test_bbox_handles_multipolygon_nesting() -> None:
    """入れ子の深さが違う geometry でも動く（深さを決め打ちしていない）。"""
    multi = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]],
            [[[5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 5.0]]],
        ],
    }
    assert bbox_from_geometry(multi) == [0.0, 0.0, 6.0, 6.0]


def test_bbox_rejects_empty_geometry() -> None:
    with pytest.raises(ValueError):
        bbox_from_geometry({"type": "Polygon", "coordinates": []})


def test_item_datetime_is_timezone_aware() -> None:
    """naive な datetime は STAC では不正。撮影日時が現在時刻に化けていないことも見る。"""
    item = build_item(_metadata())
    assert item.datetime is not None
    assert item.datetime.tzinfo is not None
    assert item.datetime == datetime(2026, 7, 1, 1, 30, tzinfo=UTC)


def test_item_bbox_matches_geometry() -> None:
    item = build_item(_metadata())
    assert item.bbox == bbox_from_geometry(_FOOTPRINT)


def test_asset_declares_cog_media_type() -> None:
    """GEOTIFF と宣言すると、クライアントは Range 読みの利得を捨てる。"""
    asset = build_item(_metadata()).assets["image"]
    assert asset.media_type == "image/tiff; application=geotiff; profile=cloud-optimized"
    assert asset.roles == ["data"]


def test_collection_holds_the_items() -> None:
    collection = build_collection([build_item(_metadata())])
    assert [item.id for item in collection.get_items()] == ["scene_nw"]


def test_catalog_holds_the_collections() -> None:
    collection = build_collection([build_item(_metadata())])
    catalog = build_catalog([collection])
    assert [child.id for child in catalog.get_children()] == [collection.id]
    # Item はもう直下にいないので、recursive を付けないと拾えない
    assert [item.id for item in catalog.get_items(recursive=True)] == ["scene_nw"]


def test_union_bbox_accepts_a_generator() -> None:
    """一度しか走査できない入力でも正しく動く。

    型ヒントが Iterable である以上ジェネレータは合法な入力。
    複数回走査する側が list()
    で固定する責任を負う（過去に2周目が空になるバグを出した）。
    """
    bboxes = [[0.0, 0.0, 1.0, 1.0], [10.0, -5.0, 11.0, 2.0]]
    assert union_bbox(b for b in bboxes) == union_bbox(bboxes)
