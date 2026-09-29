"""シーンの COG から STAC カタログを組み立てて保存する（I/O 層）。

    gdalinfo -json → dict → SceneMetadata → build_item/build_catalog → normalize_hrefs → save

GDAL は Python バインディングではなく ``gdalinfo`` コマンドを subprocess で叩く。
理由: この venv の Python(3.13) と brew の GDAL バインディング(3.14) は ABI が合わない。
プロセス境界で切ると、Python の依存グラフから GDAL が消えて衝突しなくなる。

実行::

    uv run python scripts/build_catalog.py --scene data/scenes/scene_nw.tif
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pystac

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cogstac.stac_build import (  # noqa: E402
    SceneMetadata,
    build_catalog,
    build_collection,
    build_item,
)


def get_raster_info(src: Path) -> dict:
    """``gdalinfo -json`` の出力を dict で返す（GDAL 依存の I/O）。"""
    out = subprocess.run(
        ["gdalinfo", "-json", str(src)], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def to_scene_metadata(info: dict, asset_href: str) -> SceneMetadata:
    """``gdalinfo`` の出力を純粋層の契約（SceneMetadata）に詰め替える。

    ここが唯一 ``gdalinfo`` の出力形式を知っている場所。入力元を差し替えるときは
    この関数だけを書き換えれば済む。
    """
    # gdalinfo は STAC 向けのフィールドを最初から出してくれる（proj:* / raster:* / eo:*）。
    stac_fields = info["stac"]
    # メタデータのキー "" は「ドメイン無し」の既定グループ。-mo で入れた値はここに入る。
    domain = info["metadata"][""]
    rows, cols = stac_fields["proj:shape"]

    return SceneMetadata(
        scene_id=domain["SCENE_ID"],
        # wgs84Extent は GeoJSON の Polygon そのもの。native CRS(UTM) ではなく
        # 経緯度に変換済みなので、STAC の geometry にそのまま使える。
        footprint=info["wgs84Extent"],
        # 末尾 Z 付きの文字列を tz-aware な datetime にする（Python 3.11 以降）。
        acquired=datetime.fromisoformat(domain["ACQUISITION_DATETIME"]),
        epsg=int(stac_fields["proj:epsg"]),
        shape=(rows, cols),
        transform=tuple(stac_fields["proj:transform"]),
        asset_href=asset_href,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="COG から STAC カタログを作る")
    # 既定値もリストにしておく。裸の Path だと、引数を省略したときだけ型が変わる。
    parser.add_argument(
        "--scene", type=Path, nargs="+", default=[Path("data/scenes/scene_nw.tif")]
    )
    parser.add_argument("--out-dir", type=Path, default=Path("stac"))
    args = parser.parse_args()

    # 1枚ぶんの関数（to_scene_metadata / build_item）をシーンの数だけ呼ぶ。
    items = [
        build_item(to_scene_metadata(get_raster_info(p), asset_href=str(p.resolve())))
        for p in args.scene
    ]
    # Catalog -> Collection -> Item の3階層。Catalog が直接持つのは Collection。
    catalog = build_catalog([build_collection(items)])

    # 「組み立て」と「配置」の2段構え。normalize_hrefs を経ないと href が無く save できない。
    catalog.normalize_hrefs(str(args.out_dir))
    # Asset の href は normalize_hrefs の対象外。ここで相対化しないと、
    # 手元の絶対パスがそのまま JSON に残り、他の環境で解決できなくなる。
    catalog.make_all_asset_hrefs_relative()
    catalog.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)

    try:
        catalog.validate_all()
        print("validate: OK")
    except Exception as exc:  # スキーマ取得はネットワークに出る。落ちても保存は済んでいる
        print(f"validate: 実行できず（{type(exc).__name__}: {exc}）")

    print(f"saved: {args.out_dir}/catalog.json")
    # Item は Collection の下にいるので、recursive を付けないと1枚も拾えない。
    for item in catalog.get_items(recursive=True):
        print(f"  {item.id}  bbox={[round(v, 5) for v in item.bbox]}  datetime={item.datetime}")


if __name__ == "__main__":
    main()
