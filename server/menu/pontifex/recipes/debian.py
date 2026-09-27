"""Debian family: Ubuntu/Mint live (casper), the Debian installer, Debian live."""

import os
import re

from ..util import download, q
from .base import Recipe, need_ram

DEBIAN_NETBOOT = ("https://deb.debian.org/debian/dists/{codename}/main/installer-amd64/"
                  "current/images/netboot/debian-installer/amd64/")


class Casper(Recipe):
    """Ubuntu, Mint: casper downloads the whole ISO into RAM (url=)."""

    name = "casper"

    def detect(self, ctx):
        return ctx.iso.has("casper/vmlinuz")

    def prepare(self, ctx):
        iso, entry, meta = ctx.iso, ctx.entry, ctx.meta
        initrd = iso.first(r"casper/initrd(\.lz|\.gz|\.img)?")
        iso.extract(["casper/vmlinuz", initrd], ctx.dest)
        # casper downloads the ISO into the initramfs tmpfs; kernels 6.17+ cap that low
        # unless initramfs_options raises it (older kernels ignore the option)
        ram_gb = entry.size / 2**30 * 1.5 + 1.5
        meta.update(recipe=self.name, files={"kernel": iso.orig("casper/vmlinuz"), "initrd": iso.orig(initrd)},
                    args=(f"boot=casper ip=dhcp url={entry.iso_url} cloud-config-url=/dev/null "
                          "initramfs_options=size=90%"),
                    notes=f"downloads the whole ISO into RAM: needs about {ram_gb:.0f}GB RAM")
        need_ram(meta, ram_gb * 2**30)
        info = iso.read_member(".disk/info") if iso.has(".disk/info") else ""
        m = re.search(r"\b(\d{1,2})\.(\d{2})", info)
        if m and int(m.group(1)) < 18:  # casper learned url= around 18.04
            meta["label_hint"] = "casper too old for HTTP, won't find its media"
            meta.pop("ram_mb", None)
            meta["notes"] = f"{info.strip()[:60]}: casper before 18.04 has no url= support"
        return True


class DebianInstaller(Recipe):
    """Debian netinst/DVD: the CD initrd can't netboot, so use the release's netboot
    kernel+initrd from deb.debian.org (packages come from the mirror, like netinst)."""

    name = "debian-installer"

    def detect(self, ctx):
        iso = ctx.iso
        return iso.has(".disk/info") and iso.has("install.amd/vmlinuz") and not iso.under("live/")

    def prepare(self, ctx):
        info = ctx.iso.read_member(".disk/info")
        m = re.search(r'"([A-Za-z]+)"', info)
        if not m:
            raise RuntimeError(f"can't find the release codename in .disk/info: {info!r}")
        codename = m.group(1).lower()
        base = DEBIAN_NETBOOT.format(codename=codename)
        os.makedirs(ctx.dest, exist_ok=True)
        download(base + "linux", ctx.cache_path("linux"))
        download(base + "initrd.gz", ctx.cache_path("initrd.gz"))
        ctx.meta.update(recipe=self.name, files={"kernel": "linux", "initrd": "initrd.gz"},
                        args="", notes=f"netboot installer for '{codename}' from deb.debian.org "
                                       "(packages come from the internet mirror, like netinst)")
        return True


class DebianLive(Recipe):
    """live-boot systems: GParted, Clonezilla, Debian live, Kali (fetch= the squashfs)."""

    name = "debian-live"

    def detect(self, ctx):
        return ctx.iso.has("live/filesystem.squashfs")

    def prepare(self, ctx):
        iso, entry = ctx.iso, ctx.entry
        kernel = iso.first(r"live/vmlinuz[^/]*")
        initrd = iso.first(r"live/initrd[^/]*")
        if not kernel or not initrd:
            raise RuntimeError("live/ found but no vmlinuz/initrd in it")
        iso.extract([kernel, initrd, "live/filesystem.squashfs"], ctx.dest)
        ctx.meta.update(recipe=self.name, files={"kernel": iso.orig(kernel), "initrd": iso.orig(initrd)},
                        args=(f"boot=live components union=overlay noswap "
                              f"fetch={entry.cache_url}/{q(iso.orig('live/filesystem.squashfs'))}"),
                        notes="squashfs is fetched into RAM")
        return True
