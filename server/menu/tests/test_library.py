import os
import unittest

from tests.helpers import TempSite
from pontifex import library


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.site = TempSite()

    def tearDown(self):
        self.site.close()

    def scan(self):
        return {e.rel: e for e in library.scan()}

    def test_sections_labels_and_sidecar(self):
        s = self.site
        s.link("10-linux/debian.iso", s.image("debian-13.iso"))
        s.write("10-linux/debian.iso.yaml", "label: Debian 13\nargs: nomodeset\n")
        e = self.scan()["10-linux/debian.iso"]
        self.assertEqual((e.section, e.label, e.side["args"]), ("linux", "Debian 13", "nomodeset"))
        self.assertTrue(e.iso_url.endswith("/iso/10-linux/debian.iso"))

    def test_generic_names_get_the_folder(self):
        s = self.site
        s.link("retro/Windows 3.11/DISK1.IMG", s.image("w1.img", 1474560))
        e = self.scan()["retro/Windows 3.11/DISK1.IMG"]
        self.assertEqual(e.label, "Windows 3.11: DISK1")
        self.assertEqual(e.subsection, "Windows 3.11")

    def test_numbered_floppies_become_a_set(self):
        s = self.site
        for n in (1, 2, 3):
            s.link(f"retro/dos/Disk {n}.img", s.image(f"d{n}.img", 1474560))
        entries = self.scan()
        head = entries["retro/dos/Disk 1.img"]
        self.assertEqual(len(head.set_members), 3)
        self.assertIn("[set of 3]", head.label)
        self.assertTrue(entries["retro/dos/Disk 2.img"].hidden)
        self.assertIs(entries["retro/dos/Disk 3.img"].set_head, head)

    def test_set_yaml_forces_a_set(self):
        s = self.site
        s.link("retro/mdk/a.iso", s.image("a.iso"))
        s.link("retro/mdk/b.iso", s.image("b.iso"))
        s.write("retro/mdk/set.yaml", "label: Mandrake\n")
        entries = self.scan()
        self.assertEqual(entries["retro/mdk/a.iso"].label, "Mandrake  [set of 2]")
        self.assertTrue(entries["retro/mdk/b.iso"].hidden)

    def test_dangling_link_and_duplicates(self):
        s = self.site
        s.link("tools/gone.iso", "/nonexistent/gone.iso")
        s.link("retro/x.img", s.image("x.img", 1474560))
        s.link("retro/x.img.flp", s.image("x.flp", 1474560))
        entries = self.scan()
        self.assertIn("dangling link", entries["tools/gone.iso"].error)
        self.assertNotIn("retro/x.img.flp", entries)

    def test_cache_key_follows_the_target(self):
        s = self.site
        target = s.image("v1.iso")
        s.link("linux/x.iso", target)
        k1 = self.scan()["linux/x.iso"].key
        os.utime(target, (1, 1))
        self.assertNotEqual(k1, self.scan()["linux/x.iso"].key)
