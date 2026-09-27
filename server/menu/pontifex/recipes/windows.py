"""Windows PE and Windows setup media, booted with wimboot."""

import os

from .. import config
from ..util import q
from .base import Recipe, need_ram

# ISO path (lower case) -> the name wimboot expects
WIMBOOT_FILES = {"bootmgr": "bootmgr", "boot/bcd": "BCD", "boot/boot.sdi": "boot.sdi",
                 "sources/boot.wim": "boot.wim", "efi/boot/bootx64.efi": "bootx64.efi"}


class Wimboot(Recipe):
    name = "wimboot"

    def detect(self, ctx):
        iso = ctx.iso
        return iso.has("sources/boot.wim") and (iso.has("boot/bcd") or iso.has("efi/microsoft/boot/bcd"))

    def prepare(self, ctx):
        iso, meta = ctx.iso, ctx.meta
        members = [m for m in WIMBOOT_FILES if iso.has(m)]
        iso.extract(members, ctx.dest)
        meta.update(recipe=self.name, files={m: iso.orig(m) for m in members})
        wim = os.path.getsize(ctx.cache_path(iso.orig("sources/boot.wim")))
        if wim > 600 * 2**20:  # WinPE needs roughly twice the WIM in RAM for its ramdisk
            need_ram(meta, max(4 << 30, wim * 2 + (1 << 30)))
        if iso.has("sources/install.wim") or iso.has("sources/install.esd"):
            meta["notes"] = ("Windows setup: needs the install media on an SMB share "
                             "(Windows phase, not set up yet); WinPE itself boots.")
        return True

    def render(self, entry, meta, platform, extra):
        f, c = meta.get("files", {}), entry.cache_url
        lines = [f"kernel {config.BASE_URL}/files/wimboot {extra}".rstrip()]
        for low, name in WIMBOOT_FILES.items():
            if low in f and not (low == "bootmgr" and platform == "efi"):
                lines.append(f"initrd -n {name} {c}/{q(f[low])} {name}")
        lines.append("boot")
        return lines
