"""Boot recipes: how each family of images is prepared and booted.

prepare(entry) picks the first recipe whose detect() matches (in the order below) and
returns the meta dict written to meta.json. render(entry, meta, platform) produces the
entry's iPXE lines. To add a family: write a Recipe (see base.py) and list it in ISO_RECIPES
before the families it must win against.
"""

from ..config import DISK_EXT
from ..image import IsoListing
from .arch import Archiso, SysrcdLegacy
from .base import Ctx, Recipe, kernel_initrd
from .debian import Casper, DebianInstaller, DebianLive
from .generic import Memdisk, Sanboot
from .mandrake import Mandrake
from .puppy import Puppy
from .redhat import Anaconda, AnacondaOld, DracutLive
from .windows import Wimboot

MEMDISK = Memdisk()
SANBOOT = Sanboot()

# Order matters: the first match wins
ISO_RECIPES = [
    Wimboot(),
    Casper(),
    DebianInstaller(),
    DebianLive(),
    Archiso(),
    SysrcdLegacy(),
    DracutLive(),
    AnacondaOld(),
    Mandrake(),
    Anaconda(),
    Puppy(),
]

BY_NAME = {r.name: r for r in [MEMDISK, SANBOOT, *ISO_RECIPES]}


def prepare(entry):
    """Detect the recipe and extract what it needs. Returns the meta dict."""
    meta = {"rel": entry.rel, "real": entry.real, "recipe": None, "platforms": ["pcbios", "efi"],
            "files": {}, "args": "", "notes": ""}
    forced = entry.side.get("recipe")   # sidecar override
    if entry.rel.lower().endswith(DISK_EXT) or forced == "memdisk":
        MEMDISK.prepare(Ctx(entry, None, meta))
        return meta

    iso = IsoListing.read(entry.real)
    meta["tool"] = iso.reader
    ctx = Ctx(entry, iso, meta)
    for recipe in ISO_RECIPES:
        if forced in (None, "", "auto", recipe.name) and recipe.detect(ctx) and recipe.prepare(ctx):
            return meta
    SANBOOT.prepare(ctx)
    return meta


def render(entry, meta, platform, extra):
    """The iPXE lines that boot a prepared entry (after the echo header)."""
    recipe = BY_NAME.get(meta["recipe"])
    return recipe.render(entry, meta, platform, extra) if recipe else kernel_initrd(entry, meta, extra)
