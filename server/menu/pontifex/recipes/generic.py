"""Images booted without looking inside: floppy/disk images (memdisk) and the generic
CD-emulation fallback (sanboot)."""

from ..config import FLOPPY_MAX
from ..image import eltorito
from .base import Recipe, memdisk_url


class Memdisk(Recipe):
    """Floppy and disk images, loaded whole into RAM by memdisk (BIOS only)."""

    name = "memdisk"

    def prepare(self, ctx):
        ctx.meta.update(recipe=self.name, platforms=["pcbios"],
                        args="floppy" if ctx.entry.size <= FLOPPY_MAX else "harddisk")
        return True

    def render(self, entry, meta, platform, extra):
        return [f"kernel {memdisk_url()} {meta['args']} {extra}".rstrip(),
                f"initrd {entry.iso_url}", "boot"]


class Sanboot(Recipe):
    """Anything else: iPXE emulates a CD drive over HTTP.

    iPXE's sanboot only does "no emulation" El Torito; BIOS boot images that emulate a
    floppy or hard disk (memtest, many DOS/utility CDs) go through memdisk instead, which
    loads the ISO into RAM. Platforms without a boot entry are hidden from the menu.
    """

    name = "sanboot"

    def detect(self, ctx):
        return True

    def prepare(self, ctx):
        entry, meta = ctx.entry, ctx.meta
        boot = eltorito(entry.real)
        bios = [m for p, m in boot if p == "pcbios"]
        platforms = []
        if bios:
            platforms.append("pcbios")
        if any(p == "efi" for p, _ in boot):
            platforms.append("efi")
        bios_method = "sanboot"
        if bios and "none" not in bios:
            bios_method = "memdisk-iso"
            if entry.size > 64 * 2**20:  # memdisk holds the whole ISO in RAM
                meta["label_hint"] = f"BIOS: RAM {entry.size / 2**30 + 0.5:.1f}GB+"
        meta.update(recipe=self.name, platforms=platforms or ["pcbios", "efi"], bios_method=bios_method,
                    eltorito=[f"{p}:{m}" for p, m in boot],
                    notes="generic CD emulation: works until an OS kernel loads its own disk drivers"
                          + ("; BIOS uses memdisk (floppy-emulation boot image), ISO loaded into RAM"
                             if bios_method == "memdisk-iso" else ""))
        return True

    def render(self, entry, meta, platform, extra):
        if platform == "pcbios" and meta.get("bios_method") == "memdisk-iso":
            return [f"kernel {memdisk_url()} iso raw {extra}".rstrip(), f"initrd {entry.iso_url}", "boot"]
        return [f"sanboot --no-describe {entry.iso_url}"]
