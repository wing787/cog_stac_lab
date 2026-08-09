"""HTTP Range に対応した最小サーバ（部分読み出しの検証用）。

なぜ自作か: ``python -m http.server`` は **Range ヘッダを無視して 200 で全量返す**。
それに気づかず計測すると「COG にしても減らない」という誤った結論に着地する。
S3 にお金を払う前に、ローカルで無料・無リスクに Range GET の効きを確かめるための足場。

サーバ側から見た実転送バイトを TSV に記録する（クライアント側の CPL_DEBUG と
突き合わせる groundtruth になる）。

実行::

    python3 scripts/range_server.py --root data --port 8899
"""

from __future__ import annotations

import argparse
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 1 << 20


class RangeHandler(BaseHTTPRequestHandler):
    """Range 対応の read-only ハンドラ。root 配下のファイルのみ配る。"""

    root: Path
    access_log: Path

    def log_message(self, *args: object) -> None:
        """デフォルトの標準エラー出力を抑止（計測ログを読みやすく保つ）。"""

    def _resolve(self) -> Path | None:
        """パストラバーサルを防ぎつつ、実ファイルのみ返す。"""
        candidate = (self.root / self.path.lstrip("/")).resolve()
        if not candidate.is_relative_to(self.root.resolve()) or not candidate.is_file():
            return None
        return candidate

    def _record(self, method: str, status: int, n_bytes: int) -> None:
        with self.access_log.open("a") as handle:
            handle.write(f"{method}\t{status}\t{n_bytes}\t{self.path}\n")

    def do_HEAD(self) -> None:  # noqa: N802  BaseHTTPRequestHandler の規約
        path = self._resolve()
        if path is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._record("HEAD", 200, 0)

    def do_GET(self) -> None:  # noqa: N802  BaseHTTPRequestHandler の規約
        path = self._resolve()
        if path is None:
            self.send_error(404)
            return
        size = path.stat().st_size
        match = _RANGE_RE.search(self.headers.get("Range", "") or "")
        if match:
            start = int(match.group(1)) if match.group(1) else 0
            end = min(int(match.group(2)) if match.group(2) else size - 1, size - 1)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            start, length = 0, size
            self.send_response(200)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self._send_body(path, start, length)
        self._record("GET", 206 if match else 200, length)

    def _send_body(self, path: Path, start: int, length: int) -> None:
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(_CHUNK, remaining))
                if not chunk:
                    return
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    # GDAL は必要な分だけ読んで接続を切ることがある（正常系）。
                    return
                remaining -= len(chunk)


def main() -> None:
    parser = argparse.ArgumentParser(description="Range 対応の検証用 HTTP サーバ")
    parser.add_argument("--root", type=Path, default=Path("data"), help="配信ルート")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument(
        "--access-log", type=Path, default=Path("data/access_log.tsv"), help="転送記録の出力先"
    )
    args = parser.parse_args()

    RangeHandler.root = args.root.resolve()
    RangeHandler.access_log = args.access_log
    args.access_log.parent.mkdir(parents=True, exist_ok=True)
    args.access_log.write_text("")

    print(f"serving {RangeHandler.root} on http://127.0.0.1:{args.port} (Range 対応)")
    # 127.0.0.1 に限定してバインド（検証用なので外部公開しない）。
    ThreadingHTTPServer(("127.0.0.1", args.port), RangeHandler).serve_forever()


if __name__ == "__main__":
    os.umask(0o077)
    main()
