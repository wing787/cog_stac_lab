"""検証用の合成ラスター（strip レイアウトの素の GeoTIFF）を作る。

なぜ合成データか: 会社の衛星データは持ち込まない。かつ「COG 化で何が変わるか」を
見るには、著作権も容量も気にせず再現できる小さなサンプルの方が実験に向く。

値はランダムではなく滑らかな地形風パターン。ランダムだと圧縮が一切効かず、
PREDICTOR や ZSTD の効果という現実に重要な軸が観測できなくなるため。

実行（GDAL の Python バインディングを持つインタプリタで）::

    python3 scripts/make_sample_raster.py --out data/sample_striped.tif
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from osgeo import gdal, osr

_DEFAULT_SIZE = 6000  # 6000x6000 uint16 = 72MB(無圧縮)。strip と tile の差が明確に出る大きさ。
_DEFAULT_OUT = Path("data/sample_striped.tif")


def build_pattern(size: int) -> np.ndarray:
    """滑らかな地形風の uint16 配列を作る（純粋関数: I/O を持たない）。"""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    values = (
        3000
        + 1500 * np.sin(xs / 700.0) * np.cos(ys / 900.0)
        + 300 * np.sin((xs + ys) / 120.0)
    )
    return values.astype(np.uint16)


def write_striped_geotiff(data: np.ndarray, out: Path) -> None:
    """strip レイアウト（= 創作オプション未指定）の GeoTIFF として書き出す。"""
    gdal.UseExceptions()
    out.parent.mkdir(parents=True, exist_ok=True)
    height, width = data.shape
    driver = gdal.GetDriverByName("GTiff")
    # options 未指定 = タイル化しない = Block が「全幅 x 1行」になる（今回の比較対象）。
    dataset = driver.Create(str(out), width, height, 1, gdal.GDT_UInt16)
    # 東京付近を覆う geotransform（左上原点 + ピクセルサイズ）。
    dataset.SetGeoTransform([139.0, 0.0002, 0.0, 36.5, 0.0, -0.0002])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    dataset.SetProjection(srs.ExportToWkt())
    dataset.GetRasterBand(1).WriteArray(data)
    dataset.FlushCache()
    dataset = None  # noqa: F841  GDAL はデストラクタでファイルを閉じる


def main() -> None:
    parser = argparse.ArgumentParser(description="検証用の strip レイアウト GeoTIFF を作る")
    parser.add_argument("--size", type=int, default=_DEFAULT_SIZE, help="1辺のピクセル数")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="出力パス")
    args = parser.parse_args()

    write_striped_geotiff(build_pattern(args.size), args.out)
    print(f"wrote {args.out} ({args.size}x{args.size})")


if __name__ == "__main__":
    main()
