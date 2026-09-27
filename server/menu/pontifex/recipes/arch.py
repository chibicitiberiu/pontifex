"""archiso (Arch, EndeavourOS, SystemRescue 6+) and the old Gentoo-based SystemRescueCd."""

import os
import re

from ..util import q
from .base import Recipe

ARCHES = ("x86_64", "i686")   # i686: SystemRescue 8 32-bit, archlinux32


def kernel_of(iso):
    """(kernel path, arch) of the first architecture the image has."""
    for arch in ARCHES:
        k = iso.first(rf"([^/]+)/boot/{arch}/vmlinuz[^/]*")
        if k and iso.first(rf"[^/]+/{arch}/airootfs\.(sfs|erofs)"):
            return k, arch
    return None, None


class Archiso(Recipe):
    name = "archiso"

    def detect(self, ctx):
        return kernel_of(ctx.iso)[0] is not None

    def prepare(self, ctx):
        iso, entry, meta = ctx.iso, ctx.entry, ctx.meta
        kernel, arch = kernel_of(iso)
        basedir = kernel.split("/")[0]
        bootdir = f"{basedir}/boot/{arch}/"
        main_initrd = iso.first(re.escape(bootdir) + r"(initramfs-linux|sysresccd)[^/]*\.img")
        ucode = iso.all(re.escape(basedir) + r"/boot/(intel|amd)[-_]ucode\.img")
        if not main_initrd:
            raise RuntimeError(f"no initramfs under {bootdir}")
        iso.extract([basedir], ctx.dest)
        meta.update(recipe=self.name, files={"kernel": iso.orig(kernel),
                                             "initrds": [iso.orig(u) for u in ucode] + [iso.orig(main_initrd)]},
                    args=f"archisobasedir={iso.orig(basedir)} archiso_http_srv={entry.cache_url}/ ip=dhcp",
                    notes="root filesystem is fetched into RAM, then copied once more (archiso forces copytoram over HTTP)")
        if arch != "x86_64":
            meta["platforms"] = ["pcbios"]   # our UEFI iPXE is x86_64 only
        sfs = iso.first(rf"[^/]+/{arch}/airootfs\.(sfs|erofs)")
        size = os.path.getsize(ctx.cache_path(iso.orig(sfs))) if sfs else 0
        meta["label_hint"] = f"RAM {max(2, round(size * 2 / 2**30 + 1))}GB+"
        return True


class SysrcdLegacy(Recipe):
    """SystemRescueCd 5.x and older: netboot= the sysrcd.dat, 32-bit kernel on old CPUs."""

    name = "sysrcd-legacy"

    def detect(self, ctx):
        return ctx.iso.has("sysrcd.dat") and ctx.iso.has("isolinux/initram.igz")

    def prepare(self, ctx):
        iso, entry = ctx.iso, ctx.entry
        k64, k32 = "isolinux/rescue64", "isolinux/rescue32"
        members = [m for m in (k64, k32, "isolinux/initram.igz", "sysrcd.dat", "sysrcd.md5") if iso.has(m)]
        iso.extract(members, ctx.dest)
        files = {"kernel": iso.orig(k64) if iso.has(k64) else iso.orig(k32),
                 "initrd": iso.orig("isolinux/initram.igz")}
        if iso.has(k64) and iso.has(k32):
            files["kernel32"] = iso.orig(k32)
        ctx.meta.update(recipe=self.name, files=files,
                        args=f"netboot={entry.cache_url}/{q(iso.orig('sysrcd.dat'))} dodhcp setkmap=us",
                        notes="sysrcd.dat is fetched into RAM; 32-bit kernel on non-64-bit CPUs")
        return True
