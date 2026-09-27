"""Clear Linux live images: the init looks for a device labelled CLR_ISO and loop-mounts
images/rootfs.img from it. iPXE puts rootfs.img in the initramfs instead."""

import re

from .base import Recipe, fit_payload, inject_files, overlay_init

ROOTFS = "/pontifex/rootfs.img"
FIND = ("    installer=$(find_installer)\n", "    installer=pontifex\n")
MOUNT = ('    mount_installer "$installer"\n', f"""    @JOIN@
    mkdir -p /mnt/rootfs
    mount --read-only $(losetup -fP --show {ROOTFS}) /mnt/rootfs
""")


class ClearLinux(Recipe):
    name = "clear-linux"

    def detect(self, ctx):
        return ctx.iso.has("images/rootfs.img") and bool(self._entry(ctx.iso))

    @staticmethod
    def _entry(iso):
        return next((n for n in iso.all(r"loader/entries/[^/]+\.conf") if "checksum" not in n), None)

    def prepare(self, ctx):
        iso = ctx.iso
        conf = iso.read_member(self._entry(iso))
        kernel = re.search(r"^linux\s+/?(\S+)", conf, re.M)
        initrds = re.findall(r"^initrd\s+/?(\S+)", conf, re.M)
        options = re.search(r"^options\s+(.*)$", conf, re.M)
        if not kernel or not initrds:
            return False
        members = [kernel.group(1).lower()] + [i.lower() for i in initrds]
        iso.extract(members + ["images/rootfs.img"], ctx.dest)
        inject, join = inject_files(ctx, [[iso.orig("images/rootfs.img"), ROOTFS]])
        mount = (MOUNT[0], MOUNT[1].replace("@JOIN@", "; ".join(join) or ":"))
        main = iso.orig(members[-1])     # the initrd with /init is listed last
        args = (options.group(1) if options else "").split()
        ctx.meta.update(recipe=self.name,
                        files={"kernel": iso.orig(members[0]),
                               "initrds": [iso.orig(m) for m in members[1:]]
                                          + [overlay_init(ctx, main, [FIND, mount])],
                               "inject": inject},
                        # its own rootfstype= list is for the installed system; ramfs lifts
                        # the tmpfs cap on the initramfs
                        args=" ".join(a for a in args if not a.startswith("console=ttyS"))
                             + " rootfstype=ramfs",
                        notes="rootfs.img is loaded into RAM with the initrd; needs SSE4.2")
        fit_payload(ctx)
        return True
