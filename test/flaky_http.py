#!/usr/bin/env python3
"""Flaky static file server for testing brandr's HTTP retries.

Serves one file with Range support, but misbehaves on purpose:
  - request 1 (GET): sends DROP_AT bytes, then slams the connection shut
  - request 2 (GET): sends a little, then goes silent for STALL seconds (idle timeout path)
  - request 3: 503 Service Unavailable
  - later requests behave
Every request is logged, so the test can check the Range offsets.
usage: flaky_http.py <file> <port> [drop_at_bytes] [stall_seconds]
"""
import os, sys, time, socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PATH, PORT = sys.argv[1], int(sys.argv[2])
DROP_AT = int(sys.argv[3]) if len(sys.argv) > 3 else 50 * 2**20
STALL = int(sys.argv[4]) if len(sys.argv) > 4 else 40
SIZE = os.path.getsize(PATH)
gets = 0

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", str(SIZE))
        self.end_headers()

    def do_GET(self):
        global gets
        gets += 1
        n = gets
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
        print(f"GET #{n} range={rng!r} start={start}", flush=True)
        if n == 3:
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers()
            return
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(SIZE - start))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{SIZE-1}/{SIZE}")
        self.end_headers()
        with open(PATH, "rb") as f:
            f.seek(start)
            sent = 0
            while True:
                chunk = f.read(1 << 16)
                if not chunk:
                    break
                if n == 1 and sent >= DROP_AT:
                    print(f"  #{n}: dropping connection after {sent} bytes", flush=True)
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if n == 2 and sent >= 4 * 2**20:
                    print(f"  #{n}: stalling {STALL}s after {sent} bytes", flush=True)
                    time.sleep(STALL)
                    return
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    print(f"  #{n}: client went away after {sent} bytes", flush=True)
                    return
                sent += len(chunk)
        print(f"  #{n}: complete, {sent} bytes", flush=True)

    def log_message(self, *a):
        pass

ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
