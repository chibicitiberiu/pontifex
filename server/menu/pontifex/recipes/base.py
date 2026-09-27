"""What a recipe is, and the shared kernel+initrd boot script."""

import os

from .. import config
from ..image import bzimage_info, write_cpio
from ..util import q, run


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
    """kernel + initrd(s) from the cache, with the recipe's arguments. files.inject lists
    [cache path, path in the initramfs] pairs: iPXE wraps each in a cpio header, so the
    file appears inside the initramfs (see fit_payload)."""
    c = entry.cache_url
    f = meta.get("files", {})
    initrds = f.get("initrds") or [f["initrd"]]
    inject = f.get("inject", [])
    names = [f"initrd{i}.img" for i in range(len(initrds))]
    # EFI stubs load only the initrd= files; initrd.magic is iPXE's concatenation of all
    initrd_args = "initrd=initrd.magic" if inject else " ".join(f"initrd={n}" for n in names)
    args = " ".join(x for x in [initrd_args, meta.get("args", ""), extra] if x)
    lines = []
    if f.get("kernel32"):  # old CPUs without long mode get the 32-bit kernel
        lines += [f"set kernel {c}/{q(f['kernel32'])}",
                  f"cpuid --ext 29 && set kernel {c}/{q(f['kernel'])} ||",
                  f"kernel ${{kernel}} {args}"]
    else:
        lines.append(f"kernel {c}/{q(f['kernel'])} {args}")
    lines += [f"initrd -n {n} {c}/{q(path)}" for n, path in zip(names, initrds)]
    # named, since iPXE replaces an image when a second one has the same name
    lines += [f"initrd -n inject{i} {c}/{q(src)} {dst} mkdir=-1" for i, (src, dst) in enumerate(inject)]
    lines.append("boot")
    return lines


# The kernel unpacks each initramfs file with one write, which stops at 2 GiB - 4 KiB
# (MAX_RW_COUNT) and leaves the rest zero. Bigger files go in parts, joined by the init.
MAX_INJECT = (1 << 31) - (1 << 20)


def inject_files(ctx, pairs):
    """[cache path, initramfs path] pairs -> (files.inject list, shell lines that rejoin
    split files). Files over MAX_INJECT are split in the cache into <path>.partN."""
    inject, join = [], []
    for src, dst in pairs:
        path = ctx.cache_path(src)
        size = os.path.getsize(path)
        inject.append([src, dst])
        if size <= MAX_INJECT:
            continue
        parts = []
        with open(path, "r+b") as f:
            for n, off in enumerate(range(MAX_INJECT, size, MAX_INJECT), 1):
                f.seek(off)
                with open(f"{path}.part{n}", "wb") as out:
                    remaining = min(MAX_INJECT, size - off)
                    while remaining:
                        chunk = f.read(min(remaining, 64 << 20))
                        out.write(chunk)
                        remaining -= len(chunk)
                parts.append(n)
            f.truncate(MAX_INJECT)
        inject += [[f"{src}.part{n}", f"{dst}.part{n}"] for n in parts]
        names = " ".join(f"{dst}.part{n}" for n in parts)
        join.append(f"for p in {names}; do cat $p >> {dst} && rm -f $p; done")
    return inject, join


def ram_text(mb):
    return f"{mb / 1024:.1f}".removesuffix(".0") + "GB" if mb >= 1024 else f"{-(-mb // 64) * 64}MB"


def need_ram(meta, nbytes, platform=None):
    """Record how much RAM an entry needs (one platform only, if given). The menu shows it,
    and warns before booting on a BIOS machine that reports less (iPXE's ${memsize})."""
    mb = -(-int(nbytes) // (1 << 20))
    meta["ram_mb"], meta["ram_platform"] = mb, platform
    meta["label_hint"] = ("BIOS: " if platform == "pcbios" else "") + f"RAM {ram_text(mb)}+"


# iPXE on BIOS puts initrds between the kernel and initrd_max; leave room for the kernel.
# A 32-bit kernel also needs them in its low memory (under ~896 MB).
BIOS_KERNEL_ROOM = 128 << 20
LOWMEM_32 = 0x38000000
# Our iPXE (ipxe/patches) lets kernels that accept initrds above 4 GB have them anywhere
# below 4 GB; a BIOS PC has about this much usable RAM there
BELOW_4G = 3 << 30


def fit_payload(ctx):
    """For recipes whose initrds carry the whole system (files.inject): choose the
    platforms the payload can boot on, and say how much RAM it takes. The kernel unpacks
    the payload into rootfs while the initrd copy is still in memory: about twice its size."""
    f, meta = ctx.meta["files"], ctx.meta
    info = bzimage_info(ctx.cache_path(f["kernel"]))
    if not info:
        raise RuntimeError(f"{f['kernel']} is not a Linux kernel")
    paths = (f.get("initrds") or [f["initrd"]]) + [src for src, _ in f.get("inject", [])]
    total = sum(os.path.getsize(ctx.cache_path(p)) for p in paths)
    if not info["is64"]:
        limit = min(info["initrd_max"], LOWMEM_32)
    elif info["above4g"]:
        limit = BELOW_4G
    else:
        limit = info["initrd_max"]
    platforms = []
    if total + BIOS_KERNEL_ROOM < limit:
        platforms.append("pcbios")
    if info["efi64"]:
        platforms.append("efi")
    meta["platforms"] = platforms
    meta["payload"] = total
    need_ram(meta, total * 2 + (512 << 20))
    if "pcbios" not in platforms:
        why = f"{total >> 20} MB is too big for a BIOS netboot" + (
            "" if info["efi64"] else ", and the kernel has no x86_64 UEFI entry")
        meta["notes"] = (meta.get("notes", "") + "; " if meta.get("notes") else "") + why
    return platforms


def memdisk_url():
    return f"{config.BASE_URL}/files/memdisk"


def overlay_init(ctx, initrd, edits, member="init", name="pontifex-overlay.cpio"):
    """Build a cpio that replaces the initrd's /init with an edited copy; list it after the
    initrd. edits are (old, new) string replacements, each of which must match."""
    r = run(["bsdtar", "-xOf", ctx.cache_path(initrd), member], text=False)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"can't read /{member} from {initrd}")
    text = r.stdout.decode("latin-1")   # shell scripts; keep every byte as it was
    for old, new in edits:
        if old not in text:
            raise RuntimeError(f"/{member} of {initrd} has changed: {old.strip()[:60]!r} not found")
        text = text.replace(old, new)
    write_cpio(ctx.cache_path(name), {member: (text.encode("latin-1"), 0o755)})
    return name
