import unittest

from tests.helpers import TempSite
from pontifex import recipes
from pontifex.image import IsoListing
from pontifex.recipes.base import Ctx

# File listings (as bsdtar prints them) that identify each family
LISTINGS = {
    "wimboot": ["bootmgr", "boot/bcd", "boot/boot.sdi", "sources/boot.wim", "sources/install.wim"],
    "casper": ["casper/vmlinuz", "casper/initrd", ".disk/info"],
    "debian-installer": [".disk/info", "install.amd/vmlinuz", "install.amd/initrd.gz"],
    "debian-live": ["live/vmlinuz", "live/initrd.img", "live/filesystem.squashfs"],
    "archiso": ["arch/boot/x86_64/vmlinuz-linux", "arch/boot/x86_64/initramfs-linux.img",
                "arch/x86_64/airootfs.sfs"],
    "sysrcd-legacy": ["sysrcd.dat", "isolinux/initram.igz", "isolinux/rescue64"],
    "dracut-live": ["LiveOS/squashfs.img", "images/pxeboot/vmlinuz", "images/pxeboot/initrd.img"],
    "anaconda-old": ["Fedora/base/stage2.img", "images/pxeboot/vmlinuz", "images/pxeboot/initrd.img"],
    "mandrake": ["isolinux/alt0/vmlinuz", "isolinux/alt0/all.rdz", "Mandrake/base/hdlists"],
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
                self.assertEqual(detected(names), expected)

    def test_unknown_falls_back_to_sanboot(self):
        self.assertEqual(detected(["isolinux/isolinux.bin", "KNOPPIX/KNOPPIX"]), "sanboot")

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
