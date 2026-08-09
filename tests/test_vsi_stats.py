"""vsi_stats のテスト。入力は実際の GDAL CPL_DEBUG 出力から採った本物の文字列。"""

from __future__ import annotations

from cogstac.vsi_stats import (
    parse_file_size,
    parse_range_requests,
    summarize_transfer,
)

# 実測時の striped GeoTIFF 読み取りログ（そのまま貼り付け）。
_STRIPED_LOG = [
    "VSICURL: GetFileSize(http://127.0.0.1:8899/sample_striped.tif)=72036366  response_code=200",
    "VSICURL: Downloading 0-16383 (http://127.0.0.1:8899/sample_striped.tif)...",
    "VSICURL: Downloading 32768-49151 (http://127.0.0.1:8899/sample_striped.tif)...",
    "VSICURL: Downloading 16384-32767 (http://127.0.0.1:8899/sample_striped.tif)...",
    "VSICURL: Downloading 36028416-42188799 (http://127.0.0.1:8899/sample_striped.tif)...",
]


def test_parse_range_requests_counts_every_download() -> None:
    requests = parse_range_requests(_STRIPED_LOG)
    assert len(requests) == 4


def test_range_bytes_are_inclusive() -> None:
    # 0-16383 は 16384 バイト（閉区間）。off-by-one すると転送量が全体的にずれる。
    requests = parse_range_requests(["VSICURL: Downloading 0-16383 (x)..."])
    assert requests[0].n_bytes == 16384


def test_parse_file_size() -> None:
    assert parse_file_size(_STRIPED_LOG) == 72036366


def test_summarize_striped_matches_measured_total() -> None:
    summary = summarize_transfer(_STRIPED_LOG)
    assert summary.n_requests == 4
    # 3本の 16KB ヘッダ読み + strip 本体 6,160,384 バイト
    assert summary.n_bytes == 16384 * 3 + 6_160_384
    assert summary.file_size == 72036366


def test_striped_strip_read_matches_theory() -> None:
    """本命の 1 本が「512行 x 全幅6000px x 2バイト」に一致することを固定する。

    strip レイアウトでは窓が行方向にしか刈られない、という COG の存在理由そのもの。
    理論値 6,144,000 バイトに対し、GDAL は 16KB 境界に合わせて開始を切り下げ・終端を
    切り上げるため、余剰は最大 2 チャンク（実測はちょうど 1 チャンクぶん）。
    """
    requests = parse_range_requests(_STRIPED_LOG)
    body = max(requests, key=lambda r: r.n_bytes)
    theoretical = 512 * 6000 * 2
    assert 0 <= body.n_bytes - theoretical <= 2 * 16384


def test_summary_without_file_size_has_no_fraction() -> None:
    summary = summarize_transfer(["VSICURL: Downloading 0-99 (x)..."])
    assert summary.file_size is None
    assert summary.fraction is None


def test_fraction_uses_file_size() -> None:
    summary = summarize_transfer(
        [
            "VSICURL: GetFileSize(http://h/f.tif)=1000  response_code=200",
            "VSICURL: Downloading 0-99 (http://h/f.tif)...",
        ]
    )
    assert summary.fraction == 0.1


def test_ignores_unrelated_lines() -> None:
    summary = summarize_transfer(["GDAL: GDALOpen(...)", "OGR: something", ""])
    assert summary.n_requests == 0
    assert summary.n_bytes == 0
