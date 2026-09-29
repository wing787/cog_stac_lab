"""メタデータ → STAC Item / Catalog を組み立てる（純粋ロジック）。

    SceneMetadata → build_item → pystac.Item（Asset 付き）→ build_catalog → pystac.Catalog

GDAL にもファイルシステムにもネットワークにも触れない。
なぜ分けるか: この層が純粋なら「bbox が geometry の外接矩形か」「datetime が tz-aware か」
といった検証が、TIFF もネットワークも無しに数ミリ秒で回る。
入力元を gdalinfo から別の何かに変えても、この層は無傷で済む。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pystac

_DEFAULT_CATALOG_ID = "cog-stac-lab"
_DEFAULT_CATALOG_DESCRIPTION = (
    "合成ラスターから作った"
    "COG シーンのカタログ（部分読み出しの検証用）"
)
# Asset のキー。STAC に決まりは無いが、本体データには慣習的に image / data を使う。
_DATA_ASSET_KEY = "image"

_DEFAULT_COLLECTION_ID = "cog-stac-lab-scenes"
_DEFAULT_COLLECTION_DESCRIPTION = (
    "合成ラスターから作った"
    "COG シーンのコレクション（部分読み出しの検証用）"
)



@dataclass(frozen=True)
class SceneMetadata:
    """1シーンをカタログ化するのに必要な最小限のメタデータ。

    契約は受け取る側（＝この純粋層）が定める。I/O 層はこの形に詰め替える責任を負う。

    Attributes:
        scene_id: カタログ内で一意な ID。
        footprint: **WGS84** の GeoJSON geometry。native CRS のままでは仕様違反。
        acquired: 撮影日時。**tz-aware** であること（naive は STAC では不正）。
        epsg: native CRS の EPSG コード。footprint が WGS84 に潰されて失う情報を保持する。
        shape: (rows, cols)。``gdalinfo`` の ``size`` は (cols, rows) で**順序が逆**。
        transform: native CRS でのアフィン変換係数。
        asset_href: 実データ（COG）の場所。
    """

    scene_id: str
    footprint: dict[str, Any]
    acquired: datetime
    epsg: int
    shape: tuple[int, int]
    transform: tuple[float, ...]
    asset_href: str


def _iter_positions(coordinates: Any) -> Iterator[list[float]]:
    """入れ子の座標配列から、末端の座標（``[lon, lat, ...]``）だけを取り出す。

    Polygon / MultiPolygon / LineString で入れ子の深さが違う。
    深さを決め打ちせず「数値が現れたらそこが末端」と判定することで、種類を問わず扱える。
    """
    if coordinates and isinstance(coordinates[0], (int, float)):
        yield coordinates
        return
    for child in coordinates:
        yield from _iter_positions(child)


def bbox_from_geometry(geometry: dict[str, Any]) -> list[float]:
    """GeoJSON geometry の外接矩形を ``[west, south, east, north]`` で返す。

    要素の順序は RFC 7946 で決まっており、**経度が先**（南西の点 → 北東の点）。
    緯度と経度を取り違えても JSON スキーマ検証は通ってしまうので、ここはテストで守る。

    Raises:
        ValueError: 座標が1つも含まれていないとき。
    """
    positions = list(_iter_positions(geometry["coordinates"]))
    if not positions:
        raise ValueError("geometry に座標が含まれていない")
    lons = [p[0] for p in positions]
    lats = [p[1] for p in positions]
    return [min(lons), min(lats), max(lons), max(lats)]

def union_bbox(bboxes: Iterable[Sequence[float]]) -> list[float]:
    """複数の bbox を覆う1つの bbox。west/south は min、east/north は max。"""
    bboxes = list(bboxes)
    # bbox_from_geometry と同じ構造。あちらを見返すと形が見える
    lons = [val for p in bboxes for val in (p[0], p[2])]
    lats = [val for p in bboxes for val in (p[1], p[3])]
    return [min(lons), min(lats), max(lons), max(lats)]
    

def temporal_range(datetimes: Iterable[datetime]) -> list[datetime | None]:
    """[最古, 最新]。要素2つのリストで返す（None を許すのは開区間を表せるようにするため）。"""
    values = sorted(datetimes)
    if not values:
        raise ValueError("datetimes が空")
    return [values[0], values[-1]]

def build_item(meta: SceneMetadata) -> pystac.Item:
    """1シーンぶんの :class:`pystac.Item` を組み立てる（Asset 付き）。

    ``bbox`` は geometry から導出する。両方を :class:`SceneMetadata` に持たせないのは、
    食い違った状態をそもそも作れなくするため。
    """
    item = pystac.Item(
        id=meta.scene_id,
        geometry=meta.footprint,
        bbox=bbox_from_geometry(meta.footprint),
        datetime=meta.acquired,
        # pystac は datetime をこの dict に書き込むので、書き込み先が要る（None は不可）。
        properties={},
    )
    item.add_asset(
        key=_DATA_ASSET_KEY,
        asset=pystac.Asset(
            href=meta.asset_href,
            # COG であることを宣言する。GEOTIFF と書くとクライアントは
            # 「先頭から全部読むしかないファイル」と解釈し、Range 読みの利得を捨てる。
            media_type=pystac.MediaType.COG,
            roles=["data"],
            title="Cloud Optimized GeoTIFF",
        ),
    )
    return item

def build_collection(
    items: Iterable[pystac.Item],
    collection_id: str = _DEFAULT_COLLECTION_ID,
    description: str = _DEFAULT_COLLECTION_DESCRIPTION,
) -> pystac.Collection:
    """Item 群を束ねた :class:`pystac.Collection` を返す。

    この時点ではまだ href を持たない。配置は I/O 層の ``normalize_hrefs()`` が決める。
    """
    items = list(items)          # ← ジェネレータだと2回走査できないので必ず固定する
    extent = pystac.Extent(
        spatial=pystac.SpatialExtent(bboxes=[union_bbox(item.bbox for item in items)]),
        temporal=pystac.TemporalExtent(intervals=[temporal_range(item.datetime for item in items)])
    )

    collection = pystac.Collection(
        id = collection_id,
        description = description,
        extent = extent,
        license="MIT"
    )
    for item in items:
        collection.add_item(item)
    return collection


def build_catalog(
    collections: Iterable[pystac.Collection],
    catalog_id: str = _DEFAULT_CATALOG_ID,
    description: str = _DEFAULT_CATALOG_DESCRIPTION,
) -> pystac.Catalog:
    """Collection 群を束ねた :class:`pystac.Catalog` を返す。

    この時点ではまだ href を持たない。配置は I/O 層の ``normalize_hrefs()`` が決める。
    """
    catalog = pystac.Catalog(id=catalog_id, description=description)
    for collection in collections:
        catalog.add_child(collection)
    return catalog
