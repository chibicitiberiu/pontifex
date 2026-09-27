import json
import os
import unittest

from tests.helpers import TempSite
from pontifex import config, library, menu
from pontifex.http import parse_range


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.site = TempSite()

    def tearDown(self):
        self.site.close()

    def prepared(self, rel, meta, size=4096):
        """Link an image and give it a ready meta.json, as the worker would."""
        self.site.link(rel, self.site.image(os.path.basename(rel), size))
        e = next(x for x in library.scan() if x.rel == rel)
        os.makedirs(e.cache_dir)
        with open(os.path.join(e.cache_dir, "meta.json"), "w") as f:
            json.dump({"state": "ready", "platforms": ["pcbios", "efi"], **meta}, f)
        return e

    def test_unknown_and_unsupported_tags(self):
        e = self.prepared("retro/os2.iso", {"recipe": "sanboot"})
        self.assertIn("[unknown]", menu.menu_item(e, {"recipe": "sanboot"})[0])
        line = menu.menu_item(e, {"recipe": "sanboot", "unsupported": "needs a CD"})[0]
        self.assertIn("[needs a CD]", line)
        self.assertNotIn("[unknown]", line)

    def test_kernel_initrd_entry(self):
        e = self.prepared("linux/deb.iso", {"recipe": "debian-live", "args": "boot=live",
                                            "files": {"kernel": "live/vmlinuz", "initrd": "live/initrd.img"}})
        script = menu.render_entry(e, "efi")
        self.assertIn(f"kernel {e.cache_url}/live/vmlinuz initrd=initrd0.img boot=live", script)
        self.assertIn(f"initrd -n initrd0.img {e.cache_url}/live/initrd.img", script)

    def test_sanboot_bios_memdisk_iso(self):
        e = self.prepared("tools/memtest.iso", {"recipe": "sanboot", "bios_method": "memdisk-iso"})
        self.assertIn("memdisk iso raw", menu.render_entry(e, "pcbios"))
        self.assertIn("sanboot --no-describe", menu.render_entry(e, "efi"))

    def test_wimboot_skips_bootmgr_on_efi(self):
        files = {"bootmgr": "bootmgr", "sources/boot.wim": "sources/boot.wim"}
        e = self.prepared("windows/pe.iso", {"recipe": "wimboot", "files": files})
        self.assertIn("bootmgr", menu.render_entry(e, "pcbios"))
        self.assertNotIn("-n bootmgr", menu.render_entry(e, "efi"))

    def test_menu_filters_platforms_and_tags_unknown(self):
        self.prepared("retro/dos.img", {"recipe": "memdisk", "platforms": ["pcbios"], "args": "floppy"}, 1474560)
        self.prepared("tools/odd.iso", {"recipe": "sanboot"})
        bios, efi = menu.render_menu("pcbios", ""), menu.render_menu("efi", "")
        self.assertIn("dos", bios)
        self.assertNotIn("dos", efi)
        self.assertIn("odd  [unknown]", efi)

    def test_hosts_preselect(self):
        e = self.prepared("tools/memtest.iso", {"recipe": "sanboot"})
        with open(config.HOSTS_FILE, "w") as f:
            f.write("52:54:00:00:00:01 tools/memtest.iso 3\n")
        self.assertIn(f"choose --default e{e.id} --timeout 3000", menu.render_menu("pcbios", "52-54-00-00-00-01"))


class RangeTest(unittest.TestCase):
    def test_ranges(self):
        self.assertIsNone(parse_range("", 100))
        self.assertEqual(parse_range("bytes=10-", 100), (10, 99))
        self.assertEqual(parse_range("bytes=10-19", 100), (10, 19))
        self.assertEqual(parse_range("bytes=10-500", 100), (10, 99))
        self.assertEqual(parse_range("bytes=100-", 100), "unsatisfiable")
