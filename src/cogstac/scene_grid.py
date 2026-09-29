"""大きなラスターを格子状に切り分けるための窓計算（純粋ロジック）。

用途: 1枚の画像から「複数シーン」を作り、STAC カタログの検索対象にする。
カタログの検証には bbox の異なる複数 Item が要るが、実業務データは持ち込まないので
合成ラスターを分割して代用する。

I/O は行わない。GDAL にも依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    """ピクセル座標の切り出し窓（gdal_translate の -srcwin と同じ並び）。"""

    col: int
    row: int
    width: int
    height: int

    def as_srcwin(self) -> tuple[str, str, str, str]:
        """``-srcwin`` に渡す文字列 4 つ。"""
        return (str(self.col), str(self.row), str(self.width), str(self.height))


def split_windows(width: int, height: int, n_cols: int, n_rows: int) -> list[Window]:
    """画像を ``n_cols x n_rows`` に分割した窓を、行優先（左上→右下）で返す。

    割り切れない場合、右端・下端の窓が余りを吸収する（画像の外にはみ出さない）。
    はみ出すとタイルが黒く埋まったり GDAL が警告を出したりするので、ここが要点。
    """
    if width <= 0 or height <= 0:
        raise ValueError("width と height は正である必要がある")
    if n_cols <= 0 or n_rows <= 0:
        raise ValueError("n_cols と n_rows は正である必要がある")
    if n_cols > width or n_rows > height:
        raise ValueError("分割数がピクセル数を超えている")

    base_w, base_h = width // n_cols, height // n_rows
    windows: list[Window] = []
    for r in range(n_rows):
        row = r * base_h
        h = base_h if r < n_rows - 1 else height - row
        for c in range(n_cols):
            col = c * base_w
            w = base_w if c < n_cols - 1 else width - col
            windows.append(Window(col=col, row=row, width=w, height=h))
    return windows
