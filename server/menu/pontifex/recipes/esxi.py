"""VMware ESXi installer: iPXE chains the ISO's mboot (EFI/BOOT/BOOTX64.EFI) with a boot.cfg
whose prefix= is the cache folder, and mboot fetches the kernel and modules over HTTP.
UEFI only: the BIOS path needs an old pxelinux for mboot.c32."""

import os
import re

from .base import Recipe, need_ram

BOOTCFG = "pontifex-boot.cfg"


def lowercase_tree(root):
    """The ISO's 8.3 names come out upper case; boot.cfg asks for lower case."""
    for d, dirs, files in os.walk(root, topdown=False):
        for n in dirs + files:
            if n != n.lower():
                os.rename(os.path.join(d, n), os.path.join(d, n.lower()))


class Esxi(Recipe):
    name = "esxi"

    def detect(self, ctx):
        iso = ctx.iso
        return iso.has("efi/boot/boot.cfg") and iso.has("mboot.c32") and iso.has("b.b00")

    def prepare(self, ctx):
        iso, entry = ctx.iso, ctx.entry
        iso.extract_orig(iso.top_level(), ctx.dest)
        lowercase_tree(ctx.dest)
        with open(ctx.cache_path("efi/boot/boot.cfg")) as f:
            cfg = f.read()
        # prefix= makes every path relative to the cache folder, so drop the leading /;
        # cdromBoot would send the installer looking for the CD
        cfg = re.sub(r"(?m)^prefix=.*$", f"prefix={entry.cache_url}", cfg)
        cfg = re.sub(r"(?m)^(kernel=)/", r"\1", cfg)
        cfg = re.sub(r"(?m)^(modules=.*)$", lambda m: m.group(1).replace("/", ""), cfg)
        cfg = re.sub(r"(?m)^(kernelopt=.*?)\s*\bcdromBoot\b", r"\1", cfg)
        with open(ctx.cache_path(BOOTCFG), "w") as f:
            f.write(cfg)
        ctx.meta.update(recipe=self.name, platforms=["efi"],
                        notes="ESXi installer over HTTP (UEFI only); needs 8 GB RAM and a supported NIC",
)
        need_ram(ctx.meta, 8 << 30)
        return True

    def render(self, entry, meta, platform, extra):
        c = entry.cache_url
        return [f"chain {c}/efi/boot/bootx64.efi -c {c}/{BOOTCFG}"]
