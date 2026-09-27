import os
import struct
import unittest

from tests.helpers import TempSite
from pontifex.image import bzimage_info, write_cpio
from pontifex.recipes.base import Ctx, fit_payload, kernel_initrd


def bzimage(path, is64=True, initrd_max=0x7fffffff, pe=True):
    """A file with just enough of a Linux boot header."""
    h = bytearray(0x1000)
    h[0x202:0x206] = b"HdrS"
    struct.pack_into("<H", h, 0x206, 0x20f)
    struct.pack_into("<I", h, 0x22c, initrd_max)
    struct.pack_into("<H", h, 0x236, 0x3 if is64 else 0)
    if pe:
        h[:2] = b"MZ"
        struct.pack_into("<I", h, 0x3c, 0x80)
        h[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", h, 0x84, 0x8664)
    with open(path, "wb") as f:
        f.write(h)


def read_cpio(data):
    """(name, mode, data) of each newc entry, as the kernel reads them."""
    out, off = [], 0
    while True:
        assert data[off:off + 6] == b"070701", "bad magic"
        field = lambda i: int(data[off + 6 + 8 * i:off + 14 + 8 * i], 16)
        mode, size, namesize = field(1), field(6), field(11)
        name = data[off + 110:off + 110 + namesize - 1].decode()
        off = (off + 110 + namesize + 3) & ~3
        if name == "TRAILER!!!":
            return out
        out.append((name, mode, data[off:off + size]))
        off = (off + size + 3) & ~3


class Entry:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.cache_url = "http://pxe.test:8069/cache/k"


class BzImageTest(unittest.TestCase):
    def setUp(self):
        self.site = TempSite()

    def tearDown(self):
        self.site.close()

    def test_header(self):
        p = os.path.join(self.site.images, "k")
        bzimage(p)
        self.assertEqual(bzimage_info(p), {"version": 0x20f, "is64": True, "efi64": True,
                                           "initrd_max": 0x7fffffff})
        bzimage(p, is64=False, pe=False)
        self.assertFalse(bzimage_info(p)["efi64"])
        self.assertIsNone(bzimage_info(self.site.image("not-a-kernel")))

    def test_cpio(self):
        p = os.path.join(self.site.images, "o.cpio")
        write_cpio(p, {"init": (b"#!/bin/sh\n", 0o755), "a/b/c": (b"xyz", 0o644)})
        with open(p, "rb") as f:
            entries = read_cpio(f.read())
        self.assertEqual(entries, [("init", 0o100755, b"#!/bin/sh\n"), ("a", 0o40755, b""),
                                   ("a/b", 0o40755, b""), ("a/b/c", 0o100644, b"xyz")])


class InjectTest(unittest.TestCase):
    def setUp(self):
        self.site = TempSite()
        self.dir = os.path.join(self.site.cache, "k")
        os.makedirs(self.dir)

    def tearDown(self):
        self.site.close()

    def ctx(self, payload, **kernel):
        bzimage(os.path.join(self.dir, "vmlinuz"), **kernel)
        for name, size in (("initrd.gz", 1 << 20), ("root.sfs", payload)):
            with open(os.path.join(self.dir, name), "wb") as f:
                f.truncate(size)
        meta = {"files": {"kernel": "vmlinuz", "initrd": "initrd.gz",
                          "inject": [["root.sfs", "/root.sfs"]]}, "args": "quiet"}
        return Ctx(Entry(self.dir), None, meta)

    def test_render(self):
        ctx = self.ctx(1 << 20)
        lines = kernel_initrd(ctx.entry, ctx.meta, "")
        self.assertIn("kernel http://pxe.test:8069/cache/k/vmlinuz initrd=initrd.magic quiet", lines)
        self.assertIn("initrd -n inject0 http://pxe.test:8069/cache/k/root.sfs /root.sfs mkdir=-1", lines)

    def test_platforms_by_size(self):
        self.assertEqual(fit_payload(self.ctx(500 << 20)), ["pcbios", "efi"])
        self.assertEqual(fit_payload(self.ctx(2100 << 20)), ["efi"])        # over initrd_max
        self.assertEqual(fit_payload(self.ctx(900 << 20, is64=False, pe=False)), [])  # 32-bit lowmem
        ctx = self.ctx(2100 << 20)
        fit_payload(ctx)
        self.assertEqual(ctx.meta["label_hint"], "RAM 5GB+")
