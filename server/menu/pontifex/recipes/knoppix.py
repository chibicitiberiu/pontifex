"""Knoppix 6 and later: the init skips its device search when /mnt-system/KNOPPIX/KNOPPIX
is already readable, so iPXE puts the cloop images there. (Knoppix 5 and older have an
ext2 initrd, which can't take extra files; see unsupported.py.)"""

import gzip

from .base import Recipe, fit_payload, inject_files, overlay_init

BOOT = "boot/isolinux/"
# the cheat codes Knoppix's own boot menu passes
# the init checks for the images right after this; split ones are joined just before
FOUND = "#@@@GvR Bootfrom Section start\n"
ARGS = "lang=en apm=power-off nomce hpsa.hpsa_allow_any=1 loglevel=1"


def is_cpio(path):
    try:
        with gzip.open(path) as f:
            return f.read(6) in (b"070701", b"070702")
    except OSError:
        return False


class Knoppix(Recipe):
    name = "knoppix"

    def detect(self, ctx):
        iso = ctx.iso
        return iso.has("knoppix/knoppix") and iso.has(BOOT + "minirt.gz") and iso.has(BOOT + "linux")

    def prepare(self, ctx):
        iso = ctx.iso
        iso.extract([BOOT + "minirt.gz"], ctx.dest)
        if not is_cpio(ctx.cache_path(iso.orig(BOOT + "minirt.gz"))):
            return False
        # KNOPPIX, KNOPPIX1..: the compressed images, and what init reads next to them
        payload = iso.all(r"knoppix/knoppix\d*") + iso.all(r"knoppix/kversion")
        kernels = [BOOT + k for k in ("linux64", "linux") if iso.has(BOOT + k)]
        iso.extract(kernels + payload, ctx.dest)
        initrd = iso.orig(BOOT + "minirt.gz")
        inject, join = inject_files(ctx, [[iso.orig(n), "/mnt-system/" + iso.orig(n)] for n in payload])
        initrds = [initrd]
        if join:
            initrds.append(overlay_init(ctx, initrd, [(FOUND, "\n".join(join) + "\n" + FOUND)]))
        files = {"kernel": iso.orig(kernels[0]), "initrds": initrds, "inject": inject}
        if len(kernels) > 1:
            files["kernel32"] = iso.orig(BOOT + "linux")
        ctx.meta.update(recipe=self.name, files=files, args=f"{ARGS} rootfstype=ramfs",
                        notes="the KNOPPIX images are loaded into RAM with the initrd")
        fit_payload(ctx)
        return True
