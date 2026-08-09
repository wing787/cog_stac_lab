"""COG の部分読み出しで実際に何バイト転送されるかを測る（M3 タスク4）。

用途: 同じ画像の複数レイアウト（strip / COG / 圧縮オプション違い）に対して同じ問いを投げ、
**転送バイトとリクエスト数**を並べる。速度ではなく物理量で COG の効果を語るための道具。

2つの軸を測る:
- 空間軸  : 小さな窓を読む（``-srcwin``）→ 内部タイル化が効く
- 解像度軸: 全域を低解像度で読む（``-outsize``）→ overview が効く

計測の仕組み: ``CPL_DEBUG=ON`` が吐く ``VSICURL: Downloading A-B`` を集計する
（解析は cogstac.vsi_stats の純粋ロジック）。

前提:
- GDAL の CLI (gdal_translate) が PATH にあること。
- /vsis3/ を使う場合、GDAL は SSO トークンを自動更新しないため、事前に::

      eval "$(aws configure export-credentials --format env)"

  で一時認証情報を環境変数に流すこと（鍵をファイルに書かない）。

実行例::

    python3 scripts/measure_cog_transfer.py \\
        --base /vsicurl/http://127.0.0.1:8899 \\
        sample_striped.tif sample_cog_zstd.tif
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cogstac.vsi_stats import TransferSummary, summarize_transfer  # noqa: E402

# 窓は 3000,3000 起点＝512 の倍数でない。タイル境界に整列しない窓が
# 余分なタイルを引く（部分読みの最小単位はタイル）ことを見せるため意図的にずらす。
_UNALIGNED_WINDOW = ("-srcwin", "3000", "3000", "512", "512")
_ALIGNED_WINDOW = ("-srcwin", "3072", "3072", "512", "512")
_OVERVIEW_VIEW = ("-outsize", "750", "750")


@dataclass(frozen=True)
class Query:
    """1つの問い（ラベルと gdal_translate に渡す引数）。"""

    label: str
    args: tuple[str, ...]


_QUERIES = (
    Query("窓512²(非整列)", _UNALIGNED_WINDOW),
    Query("窓512²(タイル整列)", _ALIGNED_WINDOW),
    Query("全域750x750", _OVERVIEW_VIEW),
)


def run_query(url: str, query: Query) -> TransferSummary:
    """1つの問いを gdal_translate で実行し、転送量サマリを返す。"""
    with tempfile.TemporaryDirectory() as tmp:
        completed = subprocess.run(
            ["gdal_translate", "-q", *query.args, url, str(Path(tmp) / "out.tif")],
            capture_output=True,
            text=True,
            # READDIR_ON_OPEN を切らないと、GDAL がサイドカー探索で余計な要求を出す。
            env={
                **_inherited_env(),
                "CPL_DEBUG": "ON",
                "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            },
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"gdal_translate failed for {url}:\n{completed.stderr[:800]}")
    return summarize_transfer(completed.stderr.splitlines())


def _inherited_env() -> dict[str, str]:
    import os

    return dict(os.environ)


def _format_mb(n_bytes: int) -> str:
    return f"{n_bytes / 1e6:.3f} MB"


def main() -> None:
    parser = argparse.ArgumentParser(description="COG の Range GET 転送量を測る")
    parser.add_argument(
        "--base",
        default="/vsicurl/http://127.0.0.1:8899",
        help="ファイル名の前に付くベース（例: /vsis3/<bucket>/cog）",
    )
    parser.add_argument("files", nargs="+", help="比較するファイル名")
    args = parser.parse_args()

    header = f"{'ファイル':<26}" + "".join(f"{q.label:>22}" for q in _QUERIES)
    print(header)
    print("-" * len(header))
    for name in args.files:
        url = f"{args.base.rstrip('/')}/{name}"
        cells = []
        for query in _QUERIES:
            summary = run_query(url, query)
            cells.append(f"{summary.n_requests:>3}req {_format_mb(summary.n_bytes):>12}")
        print(f"{name:<26}" + "".join(f"{c:>22}" for c in cells))


if __name__ == "__main__":
    main()
