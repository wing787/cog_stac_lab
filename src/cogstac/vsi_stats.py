"""GDAL の ``CPL_DEBUG=ON`` 出力から、HTTP Range GET の転送量を集計する（純粋ロジック）。

なぜ必要か: クラウド上のラスターでは「速さ」より **転送量(egress)と リクエスト数** が
コストと体感を決める。COG 化の効果を体感や実行時間でなく**バイト**で語れるようにする。

GDAL は /vsicurl/ /vsis3/ 経由の読み取りで、要求したバイト範囲を次の形で吐く::

    VSICURL: Downloading 36028416-42188799 (http://host/file.tif)...

I/O を一切持たない（行の列を受け取って集計するだけ）ので、そのままテストできる。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

# 範囲は閉区間 [start, end]（HTTP Range と同じ流儀）なのでバイト数は end - start + 1。
_DOWNLOAD_RE = re.compile(r"Downloading (\d+)-(\d+)")
_FILESIZE_RE = re.compile(r"GetFileSize\([^)]*\)=(\d+)")


@dataclass(frozen=True)
class RangeRequest:
    """1本の Range GET が要求したバイト範囲（閉区間）。"""

    start: int
    end: int

    @property
    def n_bytes(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class TransferSummary:
    """1クエリぶんの転送量まとめ。"""

    n_requests: int
    n_bytes: int
    file_size: int | None = None

    @property
    def fraction(self) -> float | None:
        """ファイル全体に対する転送量の割合。全体サイズが不明なら None。

        注意: 割合は「分母」が版ごとに変わると簡単に誤読できる（圧縮すると
        ファイルが縮んで割合は上がる）。判断は必ず絶対バイトで行うこと。
        """
        if not self.file_size:
            return None
        return self.n_bytes / self.file_size


def parse_range_requests(lines: Iterable[str]) -> list[RangeRequest]:
    """CPL_DEBUG の行から Range 要求を抜き出す。1行に複数含まれても拾う。"""
    requests: list[RangeRequest] = []
    for line in lines:
        for match in _DOWNLOAD_RE.finditer(line):
            start, end = int(match.group(1)), int(match.group(2))
            if end >= start:
                requests.append(RangeRequest(start, end))
    return requests


def parse_file_size(lines: Iterable[str]) -> int | None:
    """``GetFileSize(...)=N`` からファイル全体のサイズを拾う（HEAD 応答由来）。"""
    for line in lines:
        match = _FILESIZE_RE.search(line)
        if match:
            return int(match.group(1))
    return None


def summarize_transfer(lines: Iterable[str]) -> TransferSummary:
    """CPL_DEBUG 出力を集計して転送量サマリを返す。"""
    materialized = list(lines)
    requests = parse_range_requests(materialized)
    return TransferSummary(
        n_requests=len(requests),
        n_bytes=sum(r.n_bytes for r in requests),
        file_size=parse_file_size(materialized),
    )
