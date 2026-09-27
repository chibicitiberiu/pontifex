import unittest

from tests.helpers import TempSite
from pontifex import recipes
from pontifex.image import IsoListing
from pontifex.recipes.base import Ctx

# File listings (as bsdtar prints them) that identify each family. The key is the recipe
# name, optionally followed by a variant.
LISTINGS = {
    "wimboot": ["bootmgr", "boot/bcd", "boot/boot.sdi", "sources/boot.wim", "sources/install.wim"],
    "casper": ["casper/vmlinuz", "casper/initrd", ".disk/info"],
    "debian-installer": [".disk/info", "install.amd/vmlinuz", "install.amd/initrd.gz"],
    "debian-live": ["live/vmlinuz", "live/initrd.img", "live/filesystem.squashfs"],
    "archiso": ["arch/boot/x86_64/vmlinuz-linux", "arch/boot/x86_64/initramfs-linux.img",
                "arch/x86_64/airootfs.sfs"],
    "archiso i686": ["sysresccd/boot/i686/vmlinuz", "sysresccd/boot/i686/sysresccd.img",
                     "sysresccd/i686/airootfs.sfs"],
    "sysrcd-legacy": ["sysrcd.dat", "isolinux/initram.igz", "isolinux/rescue64"],
    "dracut-live": ["LiveOS/squashfs.img", "images/pxeboot/vmlinuz", "images/pxeboot/initrd.img"],
    "anaconda-old": ["Fedora/base/stage2.img", "images/pxeboot/vmlinuz", "images/pxeboot/initrd.img"],
    "mandrake": ["isolinux/alt0/vmlinuz", "isolinux/alt0/all.rdz", "Mandrake/base/hdlists"],
    "puppy": ["vmlinuz", "initrd.gz", "puppy_s15pup32_22.12.sfs", "zdrv_s15pup32_22.12.sfs",
              "isolinux.bin"],
    "knoppix": ["boot/isolinux/linux", "boot/isolinux/linux64", "boot/isolinux/minirt.gz",
                "KNOPPIX/KNOPPIX", "KNOPPIX/KNOPPIX1"],
    "antix": ["antiX/vmlinuz", "antiX/initrd.gz", "antiX/linuxfs", ".disk/info"],
    "clear-linux": ["images/rootfs.img", "loader/entries/Clear-linux-native-6.9.10-1451.conf",
                    "loader/entries/iso-checksum.conf", "EFI/BOOT/initrd.gz"],
    "esxi": ["BOOT.CFG", "EFI/BOOT/BOOT.CFG", "EFI/BOOT/BOOTX64.EFI", "MBOOT.C32", "B.B00"],
    "anaconda": ["images/pxeboot/vmlinuz", "images/pxeboot/initrd.img", "images/install.img", ".treeinfo"],
}


class StubEntry:
    cache_dir = None


def detected(names):
    ctx = Ctx(StubEntry(), IsoListing("/x.iso", "bsdtar", names), {})
    return next((r.name for r in recipes.ISO_RECIPES if r.detect(ctx)), "sanboot")


class DetectionTest(unittest.TestCase):
    def test_each_family(self):
        for expected, names in LISTINGS.items():
            with self.subTest(expected):
                self.assertEqual(detected(names), expected.split()[0])

    def test_unknown_falls_back_to_sanboot(self):
        self.assertEqual(detected(["isolinux/isolinux.bin", "OS2/OS2KRNL"]), "sanboot")

    def test_debian_live_wins_over_installer(self):
        # Debian live ISOs carry the installer too; live/ must win
        names = LISTINGS["debian-installer"] + LISTINGS["debian-live"]
        self.assertEqual(detected(names), "debian-live")

    def test_dracut_live_without_kernel_falls_through(self):
        site = TempSite()
        try:
            e = type("E", (), {"cache_dir": site.cache, "size": 1, "iso_url": "u", "real": "/x"})()
            ctx = Ctx(e, IsoListing("/x.iso", "bsdtar", ["LiveOS/squashfs.img"]), {})
            self.assertFalse(recipes.BY_NAME["dracut-live"].prepare(ctx))
        finally:
            site.close()


class UnsupportedTest(unittest.TestCase):
    def test_known_families_say_why(self):
        from pontifex.recipes.unsupported import reason
        for names, tag in ((["KNOPPIX/KNOPPIX", "boot/isolinux/minirt.gz"], "needs a CD"),
                           (["base/morphix", "boot/miniroot.gz"], "needs a CD"),
                           (["livecd.sqfs", "isolinux/initrd.gz"], "use a USB stick"),
                           (["WIN98/SETUP.EXE", "WIN98"], "setup needs a CD"),
                           (["OS2/OS2KRNL"], None)):
            with self.subTest(names[0]):
                r = reason(IsoListing("/x.iso", "bsdtar", names))
                self.assertEqual(r[0] if r else None, tag)
