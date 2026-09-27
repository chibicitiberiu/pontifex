"""/images.json: every library image (set members too) for the brandr disk writer."""

import json
import os

from . import config, library
from .image import disk_kind
from .variants import variants_of

# What a raw write of each kind of image gives you
BOOT_HINTS = {
    "hybrid": "USB-HDD / HDD",
    "disk": "HDD / USB-HDD",
    "floppy": "floppy / USB-FDD",
    "cd-only": "won't boot from a disk (CD-only image)",
    "unknown": "unknown",
}


def item(e):
    kind = disk_kind(e.real, e.size)
    in_set = e.set_head is not None or len(e.set_members) > 1
    it = {"name": os.path.basename(e.rel) if in_set else e.label,
          "section": e.heading, "path": e.rel, "url": e.iso_url,
          "size": e.size, "kind": kind, "boot_hint": BOOT_HINTS[kind],
          "variants": variants_of(e, kind)}
    head = e.set_head or (e if len(e.set_members) > 1 else None)
    if head:
        it["set"] = head.id
        it["set_index"] = head.set_members.index(e) + 1
        it["set_size"] = len(head.set_members)
    return it


def render():
    images = [item(e) for e in library.scan() if not e.error and e.real]
    images.sort(key=lambda i: (library.section_key(i["section"].split(" / ")[0]), i["section"], i["name"].lower()))
    return json.dumps({"server": config.BASE_URL, "images": images}, indent=1)
