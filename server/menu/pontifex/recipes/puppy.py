"""Puppy Linux: its init looks for the .sfs files inside the initramfs before searching
disks ("humongous initrd"), so iPXE drops them at /."""

import posixpath

from ..util import run
from .base import Recipe, fit_payload, overlay_init

# With enough RAM, init moves an in-initrd .sfs to /mnt/tmpfs, and that path has bugs:
INIT_FIXES = [
    # newer inits check the old path after moving it (the loader looks in /mnt/tmpfs first)
    ("mv -f $ONE_FN /mnt/tmpfs/",
     [('if ! [ -s "$ONE_FN" ] ; then', 'if ! [ -s "$ONE_FN" -o -s "/mnt/tmpfs/$ONE_BASENAME" ] ; then')]),
    # older ones (xenialpup) move it with `mv -a`, which busybox doesn't have
    ("mv -af $ONE_FN /mnt/tmpfs/", [("mv -af $ONE_FN /mnt/tmpfs/", "mv -f $ONE_FN /mnt/tmpfs/")]),
]

SFS = r"(?:[^/]+/)?puppy_[^/]+\.sfs"


class Puppy(Recipe):
    name = "puppy"

    def detect(self, ctx):
        sfs = ctx.iso.first(SFS)
        if not sfs:
            return False
        base = posixpath.dirname(sfs)
        return ctx.iso.has(posixpath.join(base, "vmlinuz")) and bool(self._initrd(ctx.iso, base))

    @staticmethod
    def _initrd(iso, base):
        return iso.first(rf"{base + '/' if base else ''}initrd\.(gz|xz)")

    def prepare(self, ctx):
        iso = ctx.iso
        base = posixpath.dirname(iso.first(SFS))
        prefix = base + "/" if base else ""
        kernel, initrd = prefix + "vmlinuz", self._initrd(iso, base)
        # puppy_*.sfs plus the optional layers: zdrv (drivers), fdrv (firmware), adrv, ydrv
        layers = iso.all(rf"{prefix}[a-z]?(drv|puppy)_[^/]+\.sfs")
        iso.extract([kernel, initrd, *layers], ctx.dest)
        initrds = [iso.orig(initrd)]
        init = run(["bsdtar", "-xOf", ctx.cache_path(initrds[0]), "init"], errors="replace").stdout
        edits = [e for marker, fix in INIT_FIXES if marker in init for e in fix]
        if edits:
            initrds.append(overlay_init(ctx, initrds[0], edits))
        ctx.meta.update(
            recipe=self.name,
            files={"kernel": iso.orig(kernel), "initrds": initrds,
                   "inject": [[iso.orig(s), "/" + posixpath.basename(iso.orig(s))] for s in layers]},
            # pfix=ram: no save file (there's no disk to look on); ramfs: no tmpfs size cap
            args="pfix=ram rootfstype=ramfs",
            notes="the .sfs files are loaded into RAM with the initrd")
        fit_payload(ctx)
        return True
