"""A CD-only ISO as a UEFI stick (Rufus style): MBR + one active FAT32 partition with the
ISO's files. Windows: install.wim over 4GB is split into .swm parts, and syslinux
chainloads bootmgr so the stick boots on BIOS too. Built in the background on request
(a minute or two, and about the ISO's size in the cache)."""

import json
import os
import shutil
import threading
import traceback

from ..image import IsoListing, eltorito, iso_volume_label
from ..util import check, log, run
from .. import worker
from .floppy import write_mbr_code

FAT32_MAX_FILE = 4 * 2**30 - 1
MIB = 2**20
SYSLINUX_BIOS = "/usr/lib/syslinux/modules/bios"
_build_lock = threading.Lock()   # one build at a time
_running = set()


def candidate(e, kind):
    """CD-only ISOs with an EFI El Torito entry (hybrid ISOs already work written raw)."""
    return (kind == "cd-only" and e.is_iso and e.key
            and any(p == "efi" for p, _ in eltorito(e.real)))


def paths(e):
    d = os.path.join(e.cache_dir, "variants")
    return d, os.path.join(d, "uefi.img"), os.path.join(d, "uefi.json")


def status(e):
    _, img, st_path = paths(e)
    try:
        with open(st_path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {"state": "not-built"}
    if st.get("state") == "ready" and not os.path.exists(img):
        st = {"state": "not-built"}
    return st


def _set_status(e, **st):
    d, _, st_path = paths(e)
    os.makedirs(d, exist_ok=True)
    with open(st_path + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(st_path + ".tmp", st_path)


def start_build(e):
    with worker.lock:
        if e.key in _running:
            return
        _running.add(e.key)
    _set_status(e, state="building", step="queued")
    threading.Thread(target=_build_thread, args=(e,), daemon=True).start()


def _build_thread(e):
    try:
        with _build_lock:
            size, boot_hint = build(e)
        _set_status(e, state="ready", size=size, boot_hint=boot_hint)
    except Exception as ex:  # shown to the user by the writer
        traceback.print_exc()
        _set_status(e, state="error", error=str(ex)[:300])
    finally:
        with worker.lock:
            _running.discard(e.key)


def _remove(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def build(e):
    d, img, _ = paths(e)
    tree = os.path.join(d, "uefi-tree")
    tmp = img + ".tmp"
    for leftover in (tree, tmp):   # half-built leftovers from an interrupted build
        _remove(leftover)

    iso = IsoListing.read(e.real)
    if not iso.has("efi/boot/bootx64.efi"):
        raise RuntimeError("this ISO has no \\EFI\\BOOT\\BOOTX64.EFI to boot on UEFI")
    windows = iso.has("bootmgr") and iso.has("sources/boot.wim")

    _set_status(e, state="building", step="extracting the ISO")
    iso.extract_orig(iso.top_level(), tree)

    wim = next((os.path.join(root, f) for root, _, files in os.walk(tree) for f in files
                if f.lower() == "install.wim"), None)
    if wim and os.path.getsize(wim) > FAT32_MAX_FILE:
        _set_status(e, state="building", step="splitting install.wim for FAT32")
        swm = os.path.join(os.path.dirname(wim), "install.swm")
        check(run(["wimlib-imagex", "split", wim, swm, "3800"]), "wimlib split")
        os.remove(wim)

    total = sum(os.path.getsize(os.path.join(root, f))
                for root, _, files in os.walk(tree) for f in files)
    part = ((int(total * 1.04) + 96 * MIB) // MIB + 1) * MIB   # FAT overhead + slack
    with open(tmp, "wb") as f:
        f.truncate(MIB + part)                                    # sparse
    check(run(["sfdisk", "--no-reread", "--no-tell-kernel", tmp],
              input="label: dos\nstart=2048, type=c, bootable\n"), "sfdisk")

    _set_status(e, state="building", step="formatting FAT32")
    check(run(["mkfs.fat", "-F", "32", "-n", iso_volume_label(e.real), "--offset=2048",
               tmp, str(part // 1024)]), "mkfs.fat")

    _set_status(e, state="building", step="copying files")
    fat = f"{tmp}@@{MIB}"
    top = [os.path.join(tree, n) for n in sorted(os.listdir(tree))]
    check(run(["mcopy", "-s", "-Q", "-m", "-i", fat, *top, "::/"], timeout=7200), "mcopy")

    boot_hint = "UEFI (USB)"
    if windows:
        # BIOS path: syslinux in the partition chainloads Windows' bootmgr
        cfg = os.path.join(d, "syslinux.cfg")
        with open(cfg, "w") as f:
            f.write("DEFAULT win\nPROMPT 0\nTIMEOUT 1\nLABEL win\n"
                    "  COM32 chain.c32\n  APPEND fs ntldr=/bootmgr\n")
        check(run(["syslinux", "--offset", str(MIB), "--install", tmp]), "syslinux")
        check(run(["mcopy", "-i", fat, f"{SYSLINUX_BIOS}/chain.c32", f"{SYSLINUX_BIOS}/libcom32.c32",
                   f"{SYSLINUX_BIOS}/libutil.c32", cfg, "::/"]), "mcopy")
        write_mbr_code(tmp)
        boot_hint = "UEFI (USB), or USB-HDD on BIOS"

    shutil.rmtree(tree)
    os.replace(tmp, img)
    log("built uefi stick for", e.rel, f"{os.path.getsize(img) / 2**30:.1f} GB")
    return os.path.getsize(img), boot_hint
