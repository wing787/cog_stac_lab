"""pystac の読み込みを数える StacIO（計測用の I/O アダプタ）。

なぜ必要か: カタログの JSON を読むのは GDAL ではなく pystac（中身は urllib）なので、
GDAL の CPL_DEBUG には1行も出ない。pystac が読み込みを委ねる StacIO を差し替えて、
「何回読んだか」「何バイト受け取ったか」をクライアント側で記録する。

使い方（scripts/measure_stac_flow.py から）::

    stac_io = CountingStacIO()
    catalog = pystac.Catalog.from_file(url, stac_io=stac_io)
    items = list(catalog.get_items(recursive=True))   # 遅延読み込みなので回し切る
    print(stac_io.n_requests, stac_io.n_bytes)

満たすべき仕様（measure_stac_flow.py はこれだけを前提にしている）:

- ``pystac.stac_io.DefaultStacIO`` を継承する
- 属性 ``n_requests: int`` … ``read_text`` が呼ばれた回数（＝ JSON を1つ読むごとに +1）
- 属性 ``n_bytes: int``    … 受け取った中身の **UTF-8 バイト数**の合計
  （``len(str)`` は文字数。description の日本語は1文字3バイトなのでずれる）
- ``read_text(self, source, *args, **kwargs) -> str`` を上書きし、
  実際の読み込みは親クラスに任せる
"""

from __future__ import annotations

from typing import Any

from pystac.stac_io import HREF, DefaultStacIO


class CountingStacIO(DefaultStacIO):
    """pystac の読み込み回数と受信バイト数を数える StacIO。"""

    def __init__(self) -> None:
        # 親の初期化を先に呼ぶ（HTTP ヘッダなど、内部の設定を用意してもらう）
        super().__init__()
        self.n_requests = 0
        self.n_bytes = 0

    def read_text(self, source: HREF, *args: Any, **kwargs: Any) -> str:
        # 実際の読み込みは親に任せる。自分では urllib を触らない
        text = super().read_text(source, *args, **kwargs)
        self.n_requests += 1
        self.n_bytes += len(text.encode("utf-8"))   # 文字数ではなく UTF-8 のバイト数
        return text
