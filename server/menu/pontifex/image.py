"""Looking inside boot images: file listings, extraction, El Torito and disk layout."""

import os
import re
import struct
import subprocess

from .config import FLOPPY_SIZES
from .util import check, run

# bsdtar reads ISO9660 with Rock Ridge/Joliet names but chokes on some images (Vista x86);
# 7z covers those, and UDF-only images (Windows 7+) need 7z in UDF mode.
READERS = {
    "bsdtar": (["bsdtar", "-tf"], None),
    "7z-iso": (["7z", "l", "-ba", "-slt", "-tiso"], "-tiso"),
    "7z-udf": (["7z", "l", "-ba", "-slt", "-tudf"], "-tudf"),
}


class IsoListing:
    """The files in an ISO, listed once. Names are compared lower-cased; `orig()` maps
    back to the on-disc spelling, which extraction needs."""

    def __init__(self, path, reader, names):
        self.path = path
        self.reader = reader
        self.names = [n.lower() for n in names]
        self._orig = {n.lower(): n for n in names}
        self._set = set(self.names)

    @classmethod
    def read(cls, path):
        """List with the reader that sees the most files."""
        best = ("bsdtar", [])
        for reader, (cmd, _) in READERS.items():
            r = run(cmd + [path], timeout=300)
            if r.returncode != 0:
                continue
            if reader == "bsdtar":
                names = [n.rstrip("/") for n in r.stdout.splitlines() if n.strip()]
                names = [n[2:] if n.startswith("./") else n for n in names]
            else:
                names = [l[7:].replace("\\", "/") for l in r.stdout.splitlines() if l.startswith("Path = ")]
            if len(names) > len(best[1]):
                best = (reader, names)
            if reader == "bsdtar" and len(names) >= 5 and any("/" in n for n in names):
                break  # good enough, skip the slower 7z passes
        return cls(path, *best)

    def has(self, name):
        return name in self._set

    def orig(self, name):
        return self._orig[name]

    def first(self, pattern):
        """First (sorted) name that fully matches a regex, or None."""
        rx = re.compile(pattern)
        hits = sorted(n for n in self.names if rx.fullmatch(n))
        return hits[0] if hits else None

    def all(self, pattern):
        rx = re.compile(pattern)
        return sorted(n for n in self.names if rx.fullmatch(n))

    def under(self, prefix):
        return [n for n in self.names if n.startswith(prefix)]

    def top_level(self):
        """Top-level names in on-disc spelling (to extract a whole tree)."""
        return sorted({self.orig(n).split("/")[0] for n in self.names})

    def extract(self, members, dest):
        """Extract members (lower-case names) into dest."""
        extract(self.path, self.reader, [self.orig(m) for m in members], dest)

    def extract_orig(self, members, dest):
        """Extract members given in on-disc spelling."""
        extract(self.path, self.reader, members, dest)

    def read_member(self, name):
        """A small file's text (lower-case name), '' if unreadable."""
        member = self.orig(name)
        if self.reader == "bsdtar":
            cmd = ["bsdtar", "-xOf", self.path, member]
        else:
            cmd = ["7z", "e", "-so", READERS[self.reader][1], self.path, member]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


def extract(image, reader, members, dest):
    """Extract the given members (on-disc spelling) from an image into dest."""
    os.makedirs(dest, exist_ok=True)
    if not members:
        return
    if reader == "bsdtar":
        r = run(["bsdtar", "-xf", image, "-C", dest, *members])
    else:
        r = run(["7z", "x", "-y", READERS[reader][1], f"-o{dest}", image, *members])
    # ISO permissions come out read-only: later extractions into the same tree (CD2 of a
    # set) and cache cleanup both need write access
    run(["chmod", "-R", "u+w", dest])
    check(r, f"{reader} extract failed")


def eltorito(path):
    """Boot entries of an ISO's El Torito catalog as [(platform, media)], where platform
    is "pcbios" or "efi" and media is "none" (no emulation), "floppy" or "hdd"."""
    entries = []
    try:
        with open(path, "rb") as f:
            f.seek(17 * 2048)
            brvd = f.read(2048)
            if brvd[0:6] != b"\x00CD001" or not brvd[7:30].startswith(b"EL TORITO"):
                return entries
            f.seek(struct.unpack_from("<I", brvd, 0x47)[0] * 2048)
            cat = f.read(2048)
    except OSError:
        return entries
    return parse_boot_catalog(cat)


def parse_boot_catalog(cat):
    """[(platform, media)] from a 2048-byte El Torito boot catalog."""
    entries = []
    plat = lambda b: {0: "pcbios", 0xEF: "efi"}.get(b, f"other{b}")
    media = lambda b: "none" if (b & 0x0F) == 0 else ("hdd" if (b & 0x0F) == 4 else "floppy")
    if len(cat) < 64 or cat[0] != 1:
        return entries
    if cat[32] == 0x88:
        entries.append((plat(cat[1]), media(cat[33])))
    off = 64
    while off + 32 <= len(cat) and cat[off] in (0x90, 0x91):
        platform, count = plat(cat[off + 1]), struct.unpack_from("<H", cat, off + 2)[0]
        last = cat[off] == 0x91
        off += 32
        for _ in range(count):
            if off + 32 > len(cat):
                break
            if cat[off] == 0x88:
                entries.append((platform, media(cat[off + 1])))
            off += 32
            while off + 32 <= len(cat) and cat[off] == 0x44:  # extension entries
                off += 32
        if last:
            break
    return entries


def disk_kind(path, size):
    """What a raw write of the image to a disk gives you: "floppy", "hybrid" (ISO that
    also boots as a disk), "cd-only", "disk" or "unknown"."""
    if size in FLOPPY_SIZES:
        return "floppy"
    try:
        with open(path, "rb") as f:
            mbr = f.read(512)
            f.seek(0x8001)
            iso = f.read(5) == b"CD001"
    except OSError:
        return "unknown"
    return classify(mbr, iso)


def classify(mbr, iso):
    has_mbr = len(mbr) == 512 and mbr[510:512] == b"\x55\xaa" and any(
        mbr[446 + 16 * i + 4] != 0 for i in range(4))  # boot signature + a partition type set
    if iso:
        return "hybrid" if has_mbr else "cd-only"
    return "disk" if has_mbr else "unknown"


def iso_volume_label(path):
    """FAT label (11 chars, upper case) from the ISO's volume id."""
    try:
        with open(path, "rb") as f:
            f.seek(0x8028)
            vid = f.read(32).decode("ascii", "replace").strip()
    except OSError:
        vid = ""
    label = re.sub(r"[^A-Z0-9_-]", "_", vid.upper())[:11].rstrip("_")
    return label or "PONTIFEX"
