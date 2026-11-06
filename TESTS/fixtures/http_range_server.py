"""Tiny HTTP fixture for the installer's resume tests.

Serves one file, optionally honouring `Range:` requests, and records what it was
asked for so a test can prove a resume actually sent `bytes=N-` instead of
re-downloading everything.

Usage:
    python http_range_server.py <file> <port> [--ignore-range] [--log <path>]
"""
import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("port", type=int)
    ap.add_argument("--ignore-range", action="store_true")
    ap.add_argument("--reject-range", action="store_true",
                    help="answer any Range request with 416, like a server that cannot resume")
    ap.add_argument("--log", default=None)
    args = ap.parse_args()

    payload = open(args.file, "rb").read()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            start = 0
            header = self.headers.get("Range")
            if header and args.reject_range:
                if args.log:
                    with open(args.log, "a", encoding="utf-8") as f:
                        f.write(f"{header} rejected\n")
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(payload)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if header and not args.ignore_range:
                # "bytes=1234-" is the only form curl -C - sends.
                try:
                    start = int(header.split("=", 1)[1].split("-", 1)[0] or 0)
                except ValueError:
                    start = 0
            if args.log:
                with open(args.log, "a", encoding="utf-8") as f:
                    f.write(f"{header or 'none'} {start}\n")
            body = payload[start:]
            self.send_response(206 if (header and not args.ignore_range) else 200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "none" if args.ignore_range else "bytes")
            if header and not args.ignore_range:
                self.send_header("Content-Range",
                                 f"bytes {start}-{len(payload) - 1}/{len(payload)}")
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    print("ready", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
