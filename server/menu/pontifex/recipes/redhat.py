"""Red Hat family: Fedora-style live ISOs (dracut), Anaconda installers old and new."""

import os
import re

from .base import Recipe

# Boot ISOs of these carry no packages: point Anaconda at the release's mirror
ANACONDA_REPOS = {
    "almalinux": "https://repo.almalinux.org/almalinux/{major}/BaseOS/x86_64/os/",
    "rocky": "https://download.rockylinux.org/pub/rocky/{major}/BaseOS/x86_64/os/",
}


class DracutLive(Recipe):
    """Fedora/Nobara live: root=live: downloads the whole ISO into RAM."""

    name = "dracut-live"

    def detect(self, ctx):
        iso = ctx.iso
        return iso.has("liveos/squashfs.img") or bool(iso.first(r"liveos/[^/]+\.(img|sfs)"))

    def prepare(self, ctx):
        iso, entry = ctx.iso, ctx.entry
        kernel = ("images/pxeboot/vmlinuz" if iso.has("images/pxeboot/vmlinuz")
                  else iso.first(r"(isolinux|syslinux)/vmlinuz[^/]*")
                  or iso.first(r"boot/x86_64/loader/linux|boot/vmlinuz[^/]*"))
        initrd = ("images/pxeboot/initrd.img" if iso.has("images/pxeboot/initrd.img")
                  else iso.first(r"(isolinux|syslinux)/initrd[^/]*")
                  or iso.first(r"boot/x86_64/loader/initrd|boot/initr[^/]*"))
        if not (kernel and initrd):
            return False  # a LiveOS/ dir without a kernel we can find: let others try
        iso.extract([kernel, initrd], ctx.dest)
        ram_gb = entry.size / 2**30 + 1.5
        ctx.meta.update(recipe=self.name, files={"kernel": iso.orig(kernel), "initrd": iso.orig(initrd)},
                        args=f"root=live:{entry.iso_url} rd.live.image rd.neednet=1 ip=dhcp",
                        notes=f"downloads the whole ISO into RAM: needs about {ram_gb:.0f}GB RAM")
        ctx.meta["label_hint"] = f"RAM {ram_gb:.0f}GB+"
        return True


class AnacondaOld(Recipe):
    """Red Hat 7-9, Fedora Core 1-6 (*/base/stage2.img): method= HTTP install tree."""

    name = "anaconda-old"

    def detect(self, ctx):
        return bool(ctx.iso.first(r"(redhat|fedora|centos)/base/stage2\.img")) \
            and ctx.iso.has("images/pxeboot/vmlinuz")

    def prepare(self, ctx):
        iso, entry = ctx.iso, ctx.entry
        iso.extract_orig(iso.top_level(), ctx.cache_path("tree"))
        ctx.meta.update(recipe=self.name, platforms=["pcbios"],
                        files={"kernel": "tree/" + iso.orig("images/pxeboot/vmlinuz"),
                               "initrd": "tree/" + iso.orig("images/pxeboot/initrd.img")},
                        args=f"method={entry.cache_url}/tree/",
                        notes="old Anaconda: HTTP install from the extracted tree (it asks for network settings)")
        return True


class Anaconda(Recipe):
    """Alma, Rocky, Fedora netinst and DVD: inst.stage2= the extracted tree."""

    name = "anaconda"

    def detect(self, ctx):
        iso = ctx.iso
        return iso.has("images/pxeboot/vmlinuz") and (iso.has("images/install.img") or iso.has(".treeinfo"))

    def prepare(self, ctx):
        iso, entry, meta = ctx.iso, ctx.entry, ctx.meta
        # stage2 needs .treeinfo + images/, an offline install needs the packages too:
        # simplest is the whole tree (a boot ISO is ~1GB, a DVD ~10GB)
        tree = ctx.cache_path("tree")
        iso.extract_orig(iso.top_level(), tree)
        tree_url = f"{entry.cache_url}/tree/"
        args = f"inst.stage2={tree_url} ip=dhcp"
        notes = "installer tree extracted to cache"
        if iso.has("baseos/repodata/repomd.xml") or iso.has("packages"):
            args += f" inst.repo={tree_url}"
            notes += "; packages from the ISO (offline install)"
        else:
            family, major = release(iso)
            if family and family.lower() in ANACONDA_REPOS:
                repo = ANACONDA_REPOS[family.lower()].format(major=major)
                args += f" inst.repo={repo}"
                notes += f"; packages from {repo}"
            else:
                notes += "; package source: the installer's default"
        img = os.path.join(tree, iso.orig("images/install.img")) if iso.has("images/install.img") else None
        if img and os.path.exists(img):  # stage2 lives in RAM, plus the installer itself
            # (Fedora 44 on UEFI failed with 3GB, hence the 4GB floor)
            meta["label_hint"] = f"RAM {max(4, round(os.path.getsize(img) * 2 / 2**30 + 1.5))}GB+"
        meta.update(recipe=self.name, files={"kernel": "tree/" + iso.orig("images/pxeboot/vmlinuz"),
                                             "initrd": "tree/" + iso.orig("images/pxeboot/initrd.img")},
                    args=args, notes=notes)
        return True


def release(iso):
    """(family, major) of an Anaconda ISO, e.g. ("AlmaLinux", "10"), or (None, None)."""
    if iso.has(".treeinfo"):
        ti = iso.read_member(".treeinfo")
        fam = re.search(r"^\s*(?:family|name)\s*=\s*([A-Za-z]+)", ti, re.M | re.I)
        ver = re.search(r"^\s*version\s*=\s*(\d+)", ti, re.M)
        if fam and ver:
            return fam.group(1), ver.group(1)
    # Alma 10's boot ISO has no .treeinfo; the volume label in its boot config says it,
    # e.g. "inst.stage2=hd:LABEL=AlmaLinux-10-2-x86_64-dvd"
    for cfg in ("efi/boot/grub.cfg", "boot/grub2/grub.cfg", "isolinux/isolinux.cfg"):
        if iso.has(cfg):
            m = re.search(r"LABEL=([A-Za-z]+)[-_ ](\d+)", iso.read_member(cfg))
            if m:
                return m.group(1), m.group(2)
    return None, None
