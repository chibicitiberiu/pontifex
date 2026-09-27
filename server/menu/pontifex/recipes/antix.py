"""antiX and MX Linux: the init searches block devices for antiX/linuxfs. iPXE puts the
file in the initramfs instead, and an overlay /init replaces the search."""

from .base import Recipe, fit_payload, inject_files, overlay_init

HERE = "/live/pontifex"
# A bind mount makes the directory a mountpoint, which the init later moves around
FIND = ("find_linuxfs_file() {\n", f"""find_linuxfs_file() {{
    # pontifex: linuxfs came with the initrd
    @JOIN@
    mount --bind {HERE} {HERE}
    BOOT_MP={HERE} SQFILE_MP={HERE} SQFILE_DEV= DID_ISO=true
    SQFILE_FULL=$BOOT_MP/$SQFILE_FILE SQFILE_PATH=${{SQFILE_FILE%/*}}
    DEFAULT_PERSIST_PATH=$SQFILE_PATH
    SQFILE_DIR=$(dirname $SQFILE_FULL) DEFAULT_DIR=$SQFILE_DIR
}}

find_linuxfs_file_on_devices() {{
""")


class Antix(Recipe):
    name = "antix"

    def detect(self, ctx):
        return all(ctx.iso.has(f"antix/{n}") for n in ("linuxfs", "vmlinuz", "initrd.gz"))

    def prepare(self, ctx):
        iso = ctx.iso
        kernel, initrd, sqfs = (iso.orig(f"antix/{n}") for n in ("vmlinuz", "initrd.gz", "linuxfs"))
        iso.extract(["antix/vmlinuz", "antix/initrd.gz", "antix/linuxfs"], ctx.dest)
        inject, join = inject_files(ctx, [[sqfs, f"{HERE}/{sqfs}"]])
        find = (FIND[0], FIND[1].replace("@JOIN@", "; ".join(join) or ":"))
        ctx.meta.update(recipe=self.name,
                        files={"kernel": kernel,
                               "initrds": [initrd, overlay_init(ctx, initrd, [find])],
                               "inject": inject},
                        args="quiet rootfstype=ramfs",
                        notes="linuxfs is loaded into RAM with the initrd")
        fit_payload(ctx)
        return True
