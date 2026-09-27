import struct
import unittest

from tests.helpers import TempSite  # noqa: F401  (sets PXE_BASE_URL)
from pontifex.image import IsoListing, classify, parse_boot_catalog


def catalog(default_platform, default_media, sections=()):
    """Build an El Torito boot catalog: validation + default entry + sections."""
    cat = bytearray(2048)
    cat[0], cat[1] = 1, default_platform
    cat[32], cat[33] = 0x88, default_media
    off = 64
    for i, (platform, entries) in enumerate(sections):
        cat[off] = 0x91 if i == len(sections) - 1 else 0x90
        cat[off + 1] = platform
        struct.pack_into("<H", cat, off + 2, len(entries))
        off += 32
        for media in entries:
            cat[off], cat[off + 1] = 0x88, media
            off += 32
    return bytes(cat)


class ElToritoTest(unittest.TestCase):
    def test_hybrid_bios_and_efi(self):
        cat = catalog(0, 0, [(0xEF, [0])])
        self.assertEqual(parse_boot_catalog(cat), [("pcbios", "none"), ("efi", "none")])

    def test_floppy_emulation(self):
        self.assertEqual(parse_boot_catalog(catalog(0, 2)), [("pcbios", "floppy")])
        self.assertEqual(parse_boot_catalog(catalog(0, 4)), [("pcbios", "hdd")])

    def test_not_a_catalog(self):
        self.assertEqual(parse_boot_catalog(bytes(2048)), [])


class ClassifyTest(unittest.TestCase):
    def mbr(self, with_partition=True):
        m = bytearray(512)
        m[510:512] = b"\x55\xaa"
        if with_partition:
            m[446 + 4] = 0x17
        return bytes(m)

    def test_kinds(self):
        self.assertEqual(classify(self.mbr(), iso=True), "hybrid")
        self.assertEqual(classify(bytes(512), iso=True), "cd-only")
        self.assertEqual(classify(self.mbr(), iso=False), "disk")
        self.assertEqual(classify(self.mbr(with_partition=False), iso=False), "unknown")


class ListingTest(unittest.TestCase):
    def test_lookup_is_case_insensitive(self):
        iso = IsoListing("/x.iso", "bsdtar", ["CASPER/VMLINUZ", "casper/initrd", ".disk/info"])
        self.assertTrue(iso.has("casper/vmlinuz"))
        self.assertEqual(iso.orig("casper/vmlinuz"), "CASPER/VMLINUZ")
        self.assertEqual(iso.first(r"casper/initrd(\.lz)?"), "casper/initrd")
        self.assertEqual(iso.top_level(), [".disk", "CASPER", "casper"])
