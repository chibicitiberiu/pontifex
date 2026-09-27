"""Mandrake / Mandriva: stage1 installs over HTTP from the merged tree of all CDs."""

import re
import urllib.parse

from .. import config
from ..image import IsoListing
from .base import Recipe


class Mandrake(Recipe):
    name = "mandrake"

    def detect(self, ctx):
        return ctx.iso.has("isolinux/alt0/vmlinuz") and bool(ctx.iso.first(r"(mandrake|mandriva|media)/base/[^/]+"))

    def prepare(self, ctx):
        iso, entry = ctx.iso, ctx.entry
        tree = ctx.cache_path("tree")
        for member in entry.set_members:
            if member.real is None:
                raise RuntimeError(f"set member missing: {member.rel}")
            disc = iso if member is entry else IsoListing.read(member.real)
            disc.extract_orig(disc.top_level(), tree)
        cfg = iso.read_member("isolinux/isolinux.cfg") if iso.has("isolinux/isolinux.cfg") else ""
        m = re.search(r"append\s+initrd=\S+\s+(.*?automatic=)method:cdrom", cfg, re.I)
        base_args = m.group(1).replace("automatic=", "").strip() if m else "ramdisk_size=128000 root=/dev/ram3"
        # stage1 speaks HTTP on port 80 only: the server must answer on :80 (docs/SETUP.md)
        host = urllib.parse.urlsplit(config.BASE_URL).hostname
        ctx.meta.update(recipe=self.name, platforms=["pcbios"],
                        files={"kernel": "tree/" + iso.orig("isolinux/alt0/vmlinuz"),
                               "initrd": "tree/" + iso.orig("isolinux/alt0/all.rdz")},
                        args=(f"{base_args} automatic=method:http,network:dhcp,"
                              f"server:{host},directory:/cache/{entry.key}/tree"),
                        notes=(f"HTTP install from the merged tree of {len(entry.set_members)} CD(s), no disc "
                               "swapping; needs the server on port 80 (see docs/SETUP.md)"))
        return True
