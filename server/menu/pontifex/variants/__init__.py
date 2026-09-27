"""Alternative layouts of an image that the server builds for the disk writer."""

from .. import config
from . import floppy, uefi


def variants_of(e, kind):
    """The variants the catalog offers for an image."""
    if kind == "floppy" and e.size <= config.FLOPPY_MAX:
        return [{"method": name, "url": f"{config.BASE_URL}/variant/{e.id}/{name}.img",
                 "size": floppy.SIZE, "boot_hint": hint, "state": "ready"}
                for name, (_, hint) in floppy.LAYOUTS.items()]
    if uefi.candidate(e, kind):
        st = uefi.status(e)
        return [{"method": "uefi", "url": f"{config.BASE_URL}/variant/{e.id}/uefi.img",
                 "status_url": f"{config.BASE_URL}/variant/{e.id}/uefi.json",
                 "size": st.get("size"), "state": st["state"],
                 "boot_hint": st.get("boot_hint", "UEFI (USB)")}]
    return []
