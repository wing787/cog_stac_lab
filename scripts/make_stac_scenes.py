"""1枚の合成ラスターを 2x2 に切り分け、4シーンぶんの COG を作る。

なぜ必要か: STAC カタログの価値は「多数の中から選ぶ」ことにあるので、
bbox と撮影日時が異なる **複数の Item** が無いと検証にならない。
実業務データは持ち込まない方針なので、合成ラスターを分割して疑似シーンにする。

各シーンには撮影日時を **TIFF のメタデータとして埋め込む**（``-mo``）。
STAC を作るとき、日時をスクリプトに直書きするのではなく
**元ファイルから読み出す**形にできるようにするため（実際のパイプラインもそうなる）。

シーンは **UTM (EPSG:32654) に投影してから**切り出す。実際の衛星画像は UTM 系が普通で、
一方 STAC Item の geometry は GeoJSON 仕様により **WGS84 固定**。
つまり「native CRS ≠ カタログ上の CRS」という食い違いが必ず起きる。
この食い違いを体験しないと proj 拡張（``proj:epsg`` / ``proj:shape`` / ``proj:transform``）が
何のためにあるか分からないので、素材の側でわざと作っている。

出力:
    data/sample_utm.tif        投影変換した中間ファイル
    data/scenes/scene_{nw,ne,sw,se}.tif

実行::

    python3 scripts/make_stac_scenes.py --src data/sample_striped.tif --out-dir data/scenes
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cogstac.scene_grid import Window, split_windows  # noqa: E402

# COG 化のオプション。既定の COMPRESS=LZW は UInt16 の連続値にほぼ効かないので
# PREDICTOR=2（隣接差分）を明示する。詳細は docs/cog-partial-read.md の 3 節。
_COG_OPTIONS = (
    "-of", "COG",
    "-co", "COMPRESS=ZSTD",
    "-co", "PREDICTOR=2",
    "-co", "LEVEL=9",
)

# 北側 2 枚と南側 2 枚で撮影日を変える。
# こうすると「この bbox に重なる & 7月に撮影」で 4 枚中 1 枚だけが残り、
# 空間と時間の両方で絞り込めていることを確認できる。
_NORTH_DATETIME = "2026-07-01T01:30:00Z"
_SOUTH_DATETIME = "2026-08-01T01:30:00Z"


@dataclass(frozen=True)
class Scene:
    """1シーン＝切り出し窓 + 出力名 + 撮影日時。"""

    name: str
    window: Window
    datetime: str


def build_scenes(width: int, height: int) -> list[Scene]:
    """2x2 分割の 4 シーンを組み立てる（行優先＝北西・北東・南西・南東の順）。"""
    windows = split_windows(width, height, 2, 2)
    names = ("scene_nw", "scene_ne", "scene_sw", "scene_se")
    datetimes = (_NORTH_DATETIME, _NORTH_DATETIME, _SOUTH_DATETIME, _SOUTH_DATETIME)
    return [
        Scene(name=n, window=w, datetime=d)
        for n, w, d in zip(names, windows, datetimes, strict=True)
    ]


def warp_to(src: Path, dst: Path, t_srs: str) -> Path:
    """投影変換する。

    ``-dstnodata 0`` を明示するのは、緯度経度の矩形を UTM に投影すると
    四隅が埋まらず nodata の楔ができるため。実データのシーン端と同じ状況で、
    「footprint（実データの輪郭）と bbox（外接矩形）は一致しない」という
    STAC の定番の論点がここから生まれる。
    """
    subprocess.run(
        [
            "gdalwarp", "-q", "-overwrite",
            "-t_srs", t_srs,
            "-r", "bilinear",
            "-dstnodata", "0",
            str(src), str(dst),
        ],
        check=True,
    )
    return dst


def raster_size(src: Path) -> tuple[int, int]:
    """gdalinfo -json でピクセルサイズを取る（GDAL 依存の I/O）。"""
    import json

    out = subprocess.run(
        ["gdalinfo", "-json", str(src)], capture_output=True, text=True, check=True
    ).stdout
    size = json.loads(out)["size"]
    return int(size[0]), int(size[1])


def write_scene(src: Path, scene: Scene, out_dir: Path) -> Path:
    """1シーンを COG として書き出す。"""
    dst = out_dir / f"{scene.name}.tif"
    subprocess.run(
        [
            "gdal_translate", "-q",
            "-srcwin", *scene.window.as_srcwin(),
            *_COG_OPTIONS,
            "-mo", f"ACQUISITION_DATETIME={scene.datetime}",
            "-mo", f"SCENE_ID={scene.name}",
            str(src),
            str(dst),
        ],
        check=True,
    )
    return dst


def main() -> None:
    parser = argparse.ArgumentParser(description="合成ラスターを 4 シーンの COG に分割する")
    parser.add_argument("--src", type=Path, default=Path("data/sample_striped.tif"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/scenes"))
    parser.add_argument(
        "--t-srs",
        default="EPSG:32654",
        help="シーンの native CRS（既定: UTM 54N）。STAC の geometry は常に WGS84 になる",
    )
    parser.add_argument("--warped", type=Path, default=Path("data/sample_utm.tif"))
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    src = warp_to(args.src, args.warped, args.t_srs)
    width, height = raster_size(src)
    for scene in build_scenes(width, height):
        dst = write_scene(src, scene, args.out_dir)
        print(f"{dst}  {scene.window}  {scene.datetime}  {dst.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
