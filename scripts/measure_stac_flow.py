"""「STAC で選ぶ → COG を部分読み」の流れ全体で、GET 回数と転送量を測る。

比べる3行:

1. カタログで選ぶ      … pystac が catalog / collection / Item の JSON を読み、
                        stac_query.filter_items で絞る（空間 AND 時間）
2. 選んだ1枚を部分読み … 残った Item の Asset href を GDAL で開き、512² の窓だけ読む
3. （比較）ヘッダを全部読む … カタログを使わず、全シーンの COG ヘッダを GDAL で読む。
                        bbox と CRS は分かるが、**撮影日時は TIFF に無いので時間では絞れない**

読み手が2種類あるので、メーターも2種類:

- pystac（urllib）… CountingStacIO（scripts/counting_stac_io.py）がクライアント側で数える
- GDAL（libcurl）… CPL_DEBUG の ``VSICURL: Downloading`` を cogstac.vsi_stats で集計する

どちらも、サーバ側の実転送（range_server のアクセスログ）と並べて突き合わせる。

S3 ではなくローカルの range_server を使う理由: GET 回数とバイト数は HTTP Range の
仕組みで決まり、S3 でもローカルでも同じ値になる（タスク4で /vsis3/ と同値を確認済み）。
非公開バケットを pystac の標準 StacIO（署名なし https）で読む手段が無い、という事情もある。

実行::

    uv run python scripts/measure_stac_flow.py
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pystac

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_SCRIPTS))

from counting_stac_io import CountingStacIO  # noqa: E402
from measure_cog_transfer import Query, run_query  # noqa: E402
from query_catalog import _JULY_END, _JULY_START, _WEST_HALF  # noqa: E402

from cogstac.stac_query import filter_items  # noqa: E402
from cogstac.vsi_stats import TransferSummary, summarize_transfer  # noqa: E402

# 部分読みの問い。タイル（512x512）境界に整列した 512² 窓を、シーンの内側に取る。
# タスク4の窓（3072,3072 起点）は 6000² 用で、2724x3329 のシーンでは x が画像の外に出る。
# そのとき gdal_translate -q は**タイルを1枚も読まずに終了コード 0 で終わる**ので、
# 「部分読みが効いた」ように見える誤計測になる。窓は必ず画像の内側か確かめること。
_WINDOW = ("-srcwin", "1024", "1024", "512", "512")
_WINDOW_QUERY = Query("窓512²(タイル整列)", _WINDOW)


@dataclass(frozen=True)
class Row:
    """表の1行。クライアント側とサーバ側を並べる。"""

    label: str
    client: TransferSummary
    server: TransferSummary
    note: str = ""


# ---------------------------------------------------------------- サーバ


@contextmanager
def serve(root: Path, port: int, access_log: Path) -> Iterator[None]:
    """range_server をバックグラウンドで起動し、終わったら止める。"""
    proc = subprocess.Popen(
        [
            sys.executable,
            str(_SCRIPTS / "range_server.py"),
            "--root", str(root),
            "--port", str(port),
            "--access-log", str(access_log),
        ],
        stdout=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"range_server が {port} 番で起動しない")


def _server_delta[T](access_log: Path, action: Callable[[], T]) -> tuple[T, TransferSummary]:
    """action の実行中にサーバが返した GET を、アクセスログの増分から集計する。

    HEAD はボディを返さないので回数に含めない（GDAL はファイルサイズを知るために打つ）。
    注意: GDAL が途中で接続を切った場合も、サーバは送ろうとした長さを記録する。
    なのでサーバ側はクライアント側以上になりうる（上限の見積もりとして読む）。
    """
    before = len(access_log.read_text().splitlines())
    result = action()
    lines = access_log.read_text().splitlines()[before:]
    gets = [line.split("\t") for line in lines if line.startswith("GET\t")]
    summary = TransferSummary(n_requests=len(gets), n_bytes=sum(int(g[2]) for g in gets))
    return result, summary


# ---------------------------------------------------------------- 3つの段階


def select_with_catalog(catalog_url: str) -> tuple[list[pystac.Item], TransferSummary]:
    """カタログを読み、空間 AND 時間で絞る。pystac 側の読み込みを数える。"""
    stac_io = CountingStacIO()
    catalog = pystac.Catalog.from_file(catalog_url, stac_io=stac_io)
    # get_items は遅延読み込み。list で回し切ってから数えないと、未読の Item が漏れる。
    items = list(catalog.get_items(recursive=True))
    hits = filter_items(items, bbox=_WEST_HALF, start=_JULY_START, end=_JULY_END)
    return hits, TransferSummary(n_requests=stac_io.n_requests, n_bytes=stac_io.n_bytes)


def read_window(item: pystac.Item) -> TransferSummary:
    """選ばれた Item の COG を、Asset href から辿って部分読みする。"""
    href = item.assets["image"].get_absolute_href()
    if href is None or not href.startswith("http"):
        raise RuntimeError(f"Asset href が HTTP に解決されない: {href}")
    return run_query(f"/vsicurl/{href}", _WINDOW_QUERY)


def read_header(url: str) -> TransferSummary:
    """COG のヘッダだけを読む（gdalinfo）。カタログなしで bbox を知る方法。"""
    completed = subprocess.run(
        ["gdalinfo", "-json", url],
        capture_output=True,
        text=True,
        env={
            **_env(),
            "CPL_DEBUG": "ON",
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        },
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"gdalinfo failed for {url}:\n{completed.stderr[:800]}")
    return summarize_transfer(completed.stderr.splitlines())


def _env() -> dict[str, str]:
    import os

    return dict(os.environ)


def _sum(summaries: list[TransferSummary]) -> TransferSummary:
    return TransferSummary(
        n_requests=sum(s.n_requests for s in summaries),
        n_bytes=sum(s.n_bytes for s in summaries),
    )


# ---------------------------------------------------------------- 表示


def _format(summary: TransferSummary) -> str:
    return f"{summary.n_requests:>3} GET {summary.n_bytes / 1e3:>9.1f} KB"


def print_table(rows: list[Row]) -> None:
    print(f"{'段階':<22}{'クライアント側':>20}{'サーバ側':>20}  備考")
    print("-" * 86)
    for row in rows:
        print(f"{row.label:<22}{_format(row.client):>20}{_format(row.server):>20}  {row.note}")


def main() -> None:
    parser = argparse.ArgumentParser(description="STAC で選ぶ → COG を部分読み、の転送量を測る")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--scenes", type=Path, default=Path("data/scenes"))
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    scene_names = sorted(p.name for p in (_REPO / args.scenes).glob("*.tif"))

    with tempfile.TemporaryDirectory() as tmp:
        access_log = Path(tmp) / "access_log.tsv"
        # リポジトリ最上位を配信する。stac/ と data/scenes/ の両方が見え、
        # Item の相対 href（../../../data/scenes/...）が HTTP 上でも解決できる。
        with serve(_REPO, args.port, access_log):
            (hits, catalog_client), catalog_server = _server_delta(
                access_log, lambda: select_with_catalog(f"{base}/stac/catalog.json")
            )
            if len(hits) != 1:
                raise RuntimeError(f"絞り込みで1枚にならない: {[h.id for h in hits]}")
            window_client, window_server = _server_delta(
                access_log, lambda: read_window(hits[0])
            )
            header_clients, header_server = _server_delta(
                access_log,
                lambda: [
                    read_header(f"/vsicurl/{base}/{args.scenes.as_posix()}/{name}")
                    for name in scene_names
                ],
            )

    print(f"選ばれた Item: {hits[0].id}  →  {hits[0].assets['image'].get_absolute_href()}\n")
    print_table(
        [
            Row(
                "1. カタログで選ぶ",
                catalog_client,
                catalog_server,
                "Item 数に比例して GET が増える",
            ),
            Row("2. 選んだ1枚を部分読み", window_client, window_server, _WINDOW_QUERY.label),
            Row(
                f"3. (比較) ヘッダ {len(scene_names)} 枚",
                _sum(header_clients),
                header_server,
                "撮影日時は TIFF に無く、時間では絞れない",
            ),
        ]
    )


if __name__ == "__main__":
    main()
