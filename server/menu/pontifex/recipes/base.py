"""What a recipe is, and the shared kernel+initrd boot script."""

from .. import config
from ..util import q


class Ctx:
    """Everything a recipe sees while preparing one image."""

    def __init__(self, entry, iso, meta):
        self.entry = entry
        self.iso = iso              # IsoListing (None for floppy/disk images)
        self.meta = meta            # dict written to meta.json; recipes update it
        self.dest = entry.cache_dir

    def cache_path(self, *parts):
        import os
        return os.path.join(self.dest, *parts)


class Recipe:
    """A way to boot a family of images.

    detect(ctx)  cheap check on the file listing: does this recipe apply?
    prepare(ctx) extract what's needed and fill ctx.meta (recipe, files, args, notes,
                 platforms, label_hint). Return False to let the next recipe try.
    render(...)  the iPXE lines that boot a prepared entry.
    """

    name = ""

    def detect(self, ctx):
        return False

    def prepare(self, ctx):
        raise NotImplementedError

    def render(self, entry, meta, platform, extra):
        return kernel_initrd(entry, meta, extra)


def kernel_initrd(entry, meta, extra):
    """kernel + initrd(s) from the cache, with the recipe's arguments."""
    c = entry.cache_url
    f = meta.get("files", {})
    initrds = f.get("initrds") or [f["initrd"]]
    names = [f"initrd{i}.img" for i in range(len(initrds))]
    args = " ".join(x for x in [" ".join(f"initrd={n}" for n in names), meta.get("args", ""), extra] if x)
    lines = []
    if f.get("kernel32"):  # old CPUs without long mode get the 32-bit kernel
        lines += [f"set kernel {c}/{q(f['kernel32'])}",
                  f"cpuid --ext 29 && set kernel {c}/{q(f['kernel'])} ||",
                  f"kernel ${{kernel}} {args}"]
    else:
        lines.append(f"kernel {c}/{q(f['kernel'])} {args}")
    lines += [f"initrd -n {n} {c}/{q(path)}" for n, path in zip(names, initrds)]
    lines.append("boot")
    return lines


def memdisk_url():
    return f"{config.BASE_URL}/files/memdisk"
