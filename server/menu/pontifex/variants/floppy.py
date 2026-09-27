"""A floppy image as a small bootable disk for old BIOSes that boot USB sticks as a hard
disk (USB-HDD) or a ZIP drive (USB-ZIP): MBR + FAT partition with syslinux, memdisk and
the floppy image, which memdisk then boots as drive A:. The writer writes it raw."""

import os
import threading

from ..util import check, log, run

SIZE = 8 * 2**20          # 8 cylinders x 64 heads x 32 sectors
LAYOUTS = {
    # name: (mkdiskimage flags, boot hint)
    "usb-hdd": ([], "USB-HDD"),
    "usb-zip": (["-z", "-4"], "USB-ZIP"),
}
MBR_BIN = "/usr/lib/syslinux/mbr/mbr.bin"
MEMDISK = "/usr/lib/syslinux/memdisk"
SYSLINUX_CFG = """DEFAULT floppy
PROMPT 0
TIMEOUT 1
LABEL floppy
  KERNEL memdisk
  APPEND initrd=floppy.img floppy
"""
_lock = threading.Lock()


def write_mbr_code(image):
    """Put syslinux's boot code (first 440 bytes of the MBR) into an image."""
    with open(MBR_BIN, "rb") as f:
        mbr = f.read(440)
    with open(image, "r+b") as f:
        f.write(mbr)


def build(entry, layout):
    """Build (once) and return the path of a USB-HDD/USB-ZIP layout of a floppy image."""
    flags, _ = LAYOUTS[layout]
    out_dir = os.path.join(entry.cache_dir, "variants")
    out = os.path.join(out_dir, f"{layout}.img")
    with _lock:
        if os.path.exists(out):
            return out
        os.makedirs(out_dir, exist_ok=True)
        tmp = out + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)  # half-built leftover from a crash, not a finished artefact
        cfg = os.path.join(out_dir, "syslinux.cfg")
        with open(cfg, "w") as f:
            f.write(SYSLINUX_CFG)
        # mkdiskimage: MBR + one FAT partition in 64-head/32-sector geometry (partition 4 for ZIP)
        offset = check(run(["mkdiskimage", "-o", *flags, tmp, "8", "64", "32"]), "mkdiskimage").stdout.strip()
        write_mbr_code(tmp)
        fat = f"{tmp}@@{offset}"
        for cmd in (["syslinux", "--offset", offset, "--install", tmp],
                    ["mcopy", "-i", fat, MEMDISK, "::/memdisk"],
                    ["mcopy", "-i", fat, entry.real, "::/floppy.img"],
                    ["mcopy", "-i", fat, cfg, "::/syslinux.cfg"]):
            check(run(cmd), cmd[0])
        if os.path.getsize(tmp) != SIZE:
            raise RuntimeError(f"unexpected size {os.path.getsize(tmp)}")
        os.replace(tmp, out)
        log("built", layout, "for", entry.rel)
        return out
