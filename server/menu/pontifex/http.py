"""HTTP routes (nginx proxies everything that isn't a static file here).

  /menu.ipxe?platform=pcbios|efi&mac=..      the boot menu
  /entry/<id>.ipxe?platform=..               boot script for one entry
  /writer.ipxe?platform=..                   boots the brandr disk writer
  /images.json                               image catalog for the disk writer
  /variant/<id>/usb-hdd.img | usb-zip.img    a floppy image as a USB-HDD/ZIP disk (built once)
  /variant/<id>/uefi.json | uefi.img         a CD-only ISO as a FAT32 UEFI stick (built in background)
  /status, /                                 plain-text overview
  /rescan                                    scan the library now
  /reprepare?id=<id>[,<id>]                  move entries' cache to cache/.old/ and prepare again
"""

import json
import os
import re
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler

from . import catalog, library, menu, worker, writer
from .image import disk_kind
from .util import log
from .variants import floppy, uefi

ENTRY_ID = r"([0-9a-f]{12})"


class Handler(BaseHTTPRequestHandler):
    server_version = "pxe-menu/1"

    # --- responses ------------------------------------------------------------------
    def _send(self, body, ctype="text/plain; charset=utf-8", code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path):
        """Static file with single-range support (the writer resumes with Range)."""
        size = os.path.getsize(path)
        rng = parse_range(self.headers.get("Range", ""), size)
        if rng == "unsatisfiable":
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = rng or (0, size - 1)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    # --- routes ---------------------------------------------------------------------
    def route_menu(self, qs, platform):
        return self._send(menu.render_menu(platform, qs.get("mac", "")))

    def route_entry(self, qs, platform, entry_id):
        e = library.find(entry_id)
        if not e:
            return self._send("#!ipxe\necho Unknown entry\nprompt\n", code=404)
        return self._send(menu.render_entry(e, platform))

    def route_uefi(self, qs, platform, entry_id, what):
        e = library.find(entry_id)
        if not (e and e.key and not e.error):
            return self._send("unknown image\n", code=404)
        st = uefi.status(e)
        if st["state"] in ("not-built", "error") and qs.get("build", "1") == "1" \
                and uefi.candidate(e, disk_kind(e.real, e.size)):
            uefi.start_build(e)
            st = uefi.status(e)
        if what == "json":
            return self._send(json.dumps(st), ctype="application/json")
        if st["state"] == "ready":
            return self._send_file(uefi.paths(e)[1])
        return self._send(json.dumps(st), ctype="application/json", code=503)

    def route_floppy_layout(self, qs, platform, entry_id, layout):
        e = library.find(entry_id)
        if not (e and e.key and not e.error):
            return self._send("unknown image\n", code=404)
        return self._send_file(floppy.build(e, layout))

    def route_writer(self, qs, platform):
        return self._send(writer.render(platform))

    def route_images(self, qs, platform):
        return self._send(catalog.render(), ctype="application/json")

    def route_status(self, qs, platform):
        return self._send(menu.render_status())

    def route_reprepare(self, qs, platform):
        done = worker.reprepare(qs.get("id", "").split(","))
        return self._send("".join(f"re-preparing {r}\n" for r in done) or "nothing matched\n")

    def route_rescan(self, qs, platform):
        worker.enqueue_all(library.scan())
        return self._send("rescan queued\n")

    ROUTES = [
        (r"/menu\.ipxe", route_menu),
        (rf"/entry/{ENTRY_ID}\.ipxe", route_entry),
        (rf"/variant/{ENTRY_ID}/uefi\.(img|json)", route_uefi),
        (rf"/variant/{ENTRY_ID}/(usb-hdd|usb-zip)\.img", route_floppy_layout),
        (r"/writer\.ipxe", route_writer),
        (r"/images\.json", route_images),
        (r"/(?:status)?", route_status),
        (r"/reprepare", route_reprepare),
        (r"/rescan", route_rescan),
    ]

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        qs = dict(urllib.parse.parse_qsl(url.query))
        platform = qs.get("platform", "pcbios")
        platform = platform if platform in ("pcbios", "efi") else "pcbios"
        try:
            for pattern, handler in self.ROUTES:
                m = re.fullmatch(pattern, url.path)
                if m:
                    return handler(self, qs, platform, *m.groups())
            self._send("not found\n", code=404)
        except Exception:
            traceback.print_exc()
            self._send("#!ipxe\necho pxe-menu internal error, see container log\nprompt\n", code=500)

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        log(self.address_string(), fmt % args)


def parse_range(header, size):
    """(start, end) for 'bytes=N-[M]', None without a Range, "unsatisfiable" past the end."""
    m = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
    if not m:
        return None
    start = int(m.group(1))
    if start >= size:
        return "unsatisfiable"
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    return start, end
