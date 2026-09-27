"""The library: a folder of (symlinked) boot images, scanned into Entry objects.

Folders become menu sections, `<image>.yaml` sidecars carry per-image options, and
numbered files (Disk 1..N, cd1..3) are grouped into sets.
"""

import hashlib
import json
import os
import re

from . import config
from .config import DISK_EXT, ISO_EXT
from .image import eltorito
from .util import q

# "Disk01", "Setup", "CD-ROM" say nothing on their own: the folder name is prefixed
GENERIC_NAME = re.compile(r"(disk|disc|cd|dvd|boot|setup|install\w*|cd-rom|bootdisk)[\s_\-]*\d*", re.I)
# Numbered set members: "<stem> Disk 1", "<stem>-cd2", "DISK03"
SET_RX = re.compile(r"^(?P<stem>.*?)[\s_\-\(\[]*(?:cd|disc|disk|dvd)[\s_\-]*0*(?P<n>\d{1,2})(?!\d)", re.I)


def strip_order_prefix(name):
    """'10-linux' -> 'linux': numeric prefixes only control sort order."""
    return re.sub(r"^\d+[-_ ]+", "", name)


def read_sidecar(path):
    """Optional '<image>.yaml' next to an entry. Flat 'key: value' lines only."""
    side = {}
    for candidate in (path + ".yaml", path + ".yml"):
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and ":" in line:
                        k, v = line.split(":", 1)
                        side[k.strip().lower()] = v.strip().strip('"').strip("'")
            break
    return side


class Entry:
    """One image in the library."""

    def __init__(self, rel, path):
        self.rel = rel                      # path relative to the library, e.g. linux/debian.iso
        self.path = path                    # path in the library (may be a symlink)
        self.id = hashlib.sha1(rel.encode()).hexdigest()[:12]
        parts = rel.split("/")
        self.section = strip_order_prefix(parts[0]) if len(parts) > 1 else "other"
        self.subsection = " / ".join(strip_order_prefix(p) for p in parts[1:-1])
        self.side = read_sidecar(path)
        base = os.path.basename(rel)
        self.label = self.side.get("label") or re.sub(r"\.(iso|img|ima|vfd|flp|dsk)$", "", base, flags=re.I)
        if not self.side.get("label") and GENERIC_NAME.fullmatch(self.label) and len(parts) > 1:
            self.label = f"{parts[-2]}: {self.label}"
        self.hidden = self.side.get("hide", "").lower() in ("1", "yes", "true")
        self.set_members = [self]
        self.set_head = None
        self.forced_set = False
        self.error = None
        try:
            self.real = os.path.realpath(path)
            st = os.stat(self.real)
            self.size = st.st_size
            # the target file's identity is part of the key, so repointing a link never
            # serves stale extractions
            ident = f"{self.real}|{st.st_size}|{st.st_mtime_ns}|{config.RECIPE_VERSION}"
            self.key = hashlib.sha1(ident.encode()).hexdigest()[:16]
        except OSError as e:
            self.real, self.size, self.key = None, 0, None
            self.error = f"dangling link ({e.strerror})"

    @property
    def is_iso(self):
        return self.rel.lower().endswith(ISO_EXT)

    @property
    def iso_url(self):
        return f"{config.BASE_URL}/iso/{q(self.rel)}"

    @property
    def cache_dir(self):
        return os.path.join(config.CACHE, self.key) if self.key else None

    @property
    def cache_url(self):
        return f"{config.BASE_URL}/cache/{self.key}"

    @property
    def heading(self):
        return self.section + (f" / {self.subsection}" if self.subsection else "")

    def meta(self):
        """The prepared meta.json, or None if not prepared (yet)."""
        if not self.key:
            return None
        try:
            with open(os.path.join(self.cache_dir, "meta.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


def scan(library=None):
    """Walk the library. Directory symlinks are followed, so a whole existing folder
    can be linked in once."""
    library = library or config.LIBRARY
    entries = []
    if not os.path.isdir(library):
        return entries
    seen = set()
    for root, dirs, files in os.walk(library, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:  # symlink loop or the same folder linked twice
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        lower = {n.lower() for n in files}
        for name in sorted(files):
            n = name.lower()
            if name.startswith(".") or not n.endswith(ISO_EXT + DISK_EXT):
                continue
            if n.endswith(".flp") and n[:-4] in lower:  # "x.img.flp" duplicates "x.img"
                continue
            path = os.path.join(root, name)
            entries.append(Entry(os.path.relpath(path, library), path))
    group_sets(entries)
    return entries


def find(entry_id):
    return next((e for e in scan() if e.id == entry_id), None)


def group_sets(entries):
    """'Disk 1..22.img', 'cd1/cd2/cd3.iso' in one folder -> one menu entry (the lowest
    number); the rest are kept as set members, e.g. for a merged install tree."""
    # a "set.yaml" in a folder makes all images in it one set (e.g. bootable CD2/CD3)
    forced = {}
    for e in entries:
        folder = os.path.dirname(e.path)
        if os.path.isfile(os.path.join(folder, "set.yaml")):
            forced.setdefault(folder, []).append(e)
    for folder, members in forced.items():
        members.sort(key=lambda e: e.rel.lower())
        head = members[0]
        head.set_members = members
        side = read_sidecar(os.path.join(folder, "set"))
        head.label = side.get("label", head.label) + f"  [set of {len(members)}]"
        for e in members[1:]:
            e.hidden, e.set_head = True, head
        for e in members:
            e.forced_set = True

    groups = {}
    for e in entries:
        m = SET_RX.match(os.path.basename(e.rel))
        if m and not e.forced_set:
            kind = "iso" if e.is_iso else "disk"
            key = (os.path.dirname(e.rel), m.group("stem").strip(" _-([").lower(), kind)
            groups.setdefault(key, []).append((int(m.group("n")), e))
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda t: t[0])
        head = members[0][1]
        head.set_members = [e for _, e in members]
        folded = 0
        for _, e in members[1:]:
            # a bootable disc 2 (e.g. a second standalone DVD) stays in the menu
            if e.real and e.is_iso and eltorito(e.real):
                continue
            e.hidden = True
            e.set_head = head
            folded += 1
        if folded:
            head.label += f"  [set of {len(members)}]"


def section_key(name):
    return (config.SECTION_ORDER.index(name) if name in config.SECTION_ORDER
            else len(config.SECTION_ORDER), name)
