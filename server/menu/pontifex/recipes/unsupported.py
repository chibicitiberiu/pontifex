"""Families known not to work over the network. They still get the generic fallback (their
boot loader starts), but the menu says why they won't get further instead of [unknown]."""

# (test on the listing, menu tag, explanation)
KNOWN = [
    (lambda iso: iso.has("knoppix/knoppix"), "needs a CD",
     "Knoppix 5 and older: the ext2 initrd can't take the KNOPPIX image, and it only looks on CDs"),
    (lambda iso: iso.has("base/morphix"), "needs a CD",
     "Morphix-based live CD (Ubuntu 4.10 live and friends): its initrd only looks on CDs"),
    (lambda iso: bool(iso.first(r"dists/corellinux[^/]*")), "needs a CD",
     "Corel Linux: the installer reads its packages from the CD"),
    (lambda iso: iso.has("livecd.sqfs") and iso.has("isolinux/initrd.gz"), "use a USB stick",
     "mklivecd (PCLinuxOS): an ext2 initrd that only looks for livecd.sqfs on disks; "
     "write the ISO to a USB stick with the disk writer"),
    (lambda iso: bool(iso.first(r"win9[58x]|win98|win95|i386/txtsetup\.sif")), "setup needs a CD",
     "Windows 9x/NT/2000/XP setup: it boots, but setup can't see the CD afterwards"),
]


def reason(iso):
    """(tag, explanation) if the listing is a known non-netbootable family, else None."""
    for test, tag, why in KNOWN:
        if test(iso):
            return tag, why
    return None
