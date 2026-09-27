#!/usr/bin/env python3
"""Pontifex menu service.

Builds the iPXE boot menu from a folder of (symlinked) ISOs at request time, and
prepares each ISO in the background: it detects which boot recipe fits by looking
inside the image, then extracts only the files that recipe needs into the cache.

  GET /menu.ipxe?platform=pcbios|efi&mac=..   the menu (what the embedded script chains)
  GET /entry/<id>.ipxe?platform=..            boot script for one library entry
  GET /status                                 plain-text overview for humans / curl
  GET /images.json                            image catalog for the brandr writer (size, URL, disk kind)
  GET /writer.ipxe?platform=..                boot script for the brandr disk writer
  GET /variant/<id>/usb-hdd.img|usb-zip.img   floppy image as a USB-HDD / USB-ZIP disk (built once)
  GET /variant/<id>/uefi.json|uefi.img        CD-only ISO as a FAT32 UEFI stick (built in background)
  GET /rescan                                 force a library scan now

nginx serves the actual bytes: /iso/ (library), /cache/ (extracted files), /files/
(memdisk, wimboot, iPXE binaries). Stdlib only; needs bsdtar and 7z on PATH.
"""

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LIBRARY = os.environ.get("PXE_LIBRARY", "/srv/pontifex/library")
CACHE = os.environ.get("PXE_CACHE", "/srv/pontifex/cache")
HOSTS_FILE = os.environ.get("PXE_HOSTS", "/srv/pontifex/hosts.conf")
BASE_URL = os.environ["PXE_BASE_URL"].rstrip("/")  # e.g. http://192.168.1.10:8069
LISTEN_PORT = int(os.environ.get("PXE_LISTEN_PORT", "8000"))
SCAN_INTERVAL = int(os.environ.get("PXE_SCAN_INTERVAL", "60"))
MENU_TIMEOUT = int(os.environ.get("PXE_MENU_TIMEOUT", "0"))  # seconds, 0 = wait forever
RECIPE_VERSION = 5  # bump to force re-detection of every cached entry

ISO_EXT = (".iso",)
DISK_EXT = (".img", ".ima", ".vfd", ".flp", ".dsk")
FLOPPY_MAX = 2949120  # 2.88MB; larger raw images are booted by memdisk as hard disks

# Shown first, in this order; anything else follows alphabetically
SECTION_ORDER = ["linux", "windows", "tools", "retro"]

# AlmaLinux/Rocky boot ISOs carry no packages; point Anaconda at the release's mirror
ANACONDA_REPOS = {
    "almalinux": "https://repo.almalinux.org/almalinux/{major}/BaseOS/x86_64/os/",
    "rocky": "https://download.rockylinux.org/pub/rocky/{major}/BaseOS/x86_64/os/",
}

DEBIAN_NETBOOT = ("https://deb.debian.org/debian/dists/{codename}/main/installer-amd64/"
                  "current/images/netboot/debian-installer/amd64/")


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, file=sys.stderr, flush=True)


def q(path):
    """Percent-encode a path for use in an iPXE/HTTP URL (keeps '/')."""
    return urllib.parse.quote(path, safe="/-_.~")


def ipxe_text(s):
    """Menu labels must not contain iPXE expansions or line breaks."""
    return re.sub(r"[\r\n\t]", " ", s).replace("${", "$ {").strip()


# --------------------------------------------------------------------------------------
# Library scanning
# --------------------------------------------------------------------------------------

def strip_order_prefix(name):
    """'10-linux' -> 'linux': numeric prefixes only control sort order."""
    return re.sub(r"^\d+[-_ ]+", "", name)


def read_sidecar(path):
    """Optional '<image>.yaml' next to an entry. Flat 'key: value' lines only."""
    side = {}
    for candidate in (path + ".yaml", path + ".yml"):
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and ":" in line:
                        k, v = line.split(":", 1)
                        side[k.strip().lower()] = v.strip().strip('"').strip("'")
            break
    return side


class Entry:
    def __init__(self, rel, path):
        self.rel = rel                      # path relative to LIBRARY, e.g. linux/debian.iso
        self.path = path                    # path in LIBRARY (may be a symlink)
        self.id = hashlib.sha1(rel.encode()).hexdigest()[:12]
        parts = rel.split("/")
        self.section = strip_order_prefix(parts[0]) if len(parts) > 1 else "other"
        self.subsection = " / ".join(strip_order_prefix(p) for p in parts[1:-1])
        self.side = read_sidecar(path)
        base = os.path.basename(rel)
        self.label = self.side.get("label") or re.sub(r"\.(iso|img|ima|vfd|flp|dsk)$", "", base, flags=re.I)
        if not self.side.get("label") and GENERIC_NAME.fullmatch(self.label) and len(parts) > 1:
            self.label = f"{parts[-2]}: {self.label}"  # "Disk01" says nothing on its own
        self.hidden = self.side.get("hide", "").lower() in ("1", "yes", "true")
        self.set_members = [self]
        self.set_head = None
        self.error = None
        try:
            self.real = os.path.realpath(path)
            st = os.stat(self.real)
            self.size = st.st_size
            self.key = hashlib.sha1(f"{self.real}|{st.st_size}|{st.st_mtime_ns}|{RECIPE_VERSION}".encode()).hexdigest()[:16]
        except OSError as e:
            self.real, self.size, self.key = None, 0, None
            self.error = f"dangling link ({e.strerror})"

    @property
    def iso_url(self):
        return f"{BASE_URL}/iso/{q(self.rel)}"

    @property
    def cache_dir(self):
        return os.path.join(CACHE, self.key) if self.key else None

    @property
    def cache_url(self):
        return f"{BASE_URL}/cache/{self.key}"

    def meta(self):
        if not self.key:
            return None
        try:
            with open(os.path.join(self.cache_dir, "meta.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


GENERIC_NAME = re.compile(r"(disk|disc|cd|dvd|boot|setup|install\w*|cd-rom|bootdisk)[\s_\-]*\d*", re.I)
SET_RX = re.compile(r"^(?P<stem>.*?)[\s_\-\(\[]*(?:cd|disc|disk|dvd)[\s_\-]*0*(?P<n>\d{1,2})(?!\d)", re.I)


def scan_library():
    """Walk the library. Directory symlinks are followed, so a whole existing folder
    (e.g. the NAS 'Operating Systems' tree) can be linked in once."""
    entries = []
    if not os.path.isdir(LIBRARY):
        return entries
    seen = set()
    for root, dirs, files in os.walk(LIBRARY, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:  # symlink loop or the same folder linked twice
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        lower = {n.lower() for n in files}
        for name in sorted(files):
            n = name.lower()
            if name.startswith(".") or not n.endswith(ISO_EXT + DISK_EXT):
                continue
            if n.endswith(".flp") and n[:-4] in lower:  # "x.img.flp" duplicates "x.img"
                continue
            path = os.path.join(root, name)
            entries.append(Entry(os.path.relpath(path, LIBRARY), path))
    group_sets(entries)
    return entries


def group_sets(entries):
    """'Disk 1..22.img', 'cd1/cd2/cd3.iso' in one folder -> one menu entry (the lowest
    number); the rest are kept as set members, e.g. for a merged install tree."""
    groups = {}
    for e in entries:
        m = SET_RX.match(os.path.basename(e.rel))
        if m:
            kind = "iso" if e.rel.lower().endswith(ISO_EXT) else "disk"
            key = (os.path.dirname(e.rel), m.group("stem").strip(" _-([").lower(), kind)
            groups.setdefault(key, []).append((int(m.group("n")), e))
    # a "set.yaml" in a folder makes all images in it one set (e.g. bootable CD2/CD3)
    forced = {}
    for e in entries:
        folder = os.path.dirname(e.path)
        if os.path.isfile(os.path.join(folder, "set.yaml")):
            forced.setdefault(folder, []).append(e)
    for folder, members in forced.items():
        members.sort(key=lambda e: e.rel.lower())
        head = members[0]
        head.set_members = members
        side = read_sidecar(os.path.join(folder, "set"))
        head.label = side.get("label", head.label) + f"  [set of {len(members)}]"
        for e in members[1:]:
            e.hidden, e.set_head = True, head
        for e in members:
            e.forced_set = True
    for members in groups.values():
        members = [t for t in members if not getattr(t[1], "forced_set", False)]
        if len(members) < 2:
            continue
        members.sort(key=lambda t: t[0])
        head = members[0][1]
        head.set_members = [e for _, e in members]
        folded = 0
        for _, e in members[1:]:
            # a bootable disc 2 (e.g. a second standalone DVD) stays in the menu
            if e.real and e.rel.lower().endswith(ISO_EXT) and eltorito(e.real):
                continue
            e.hidden = True
            e.set_head = head
            folded += 1
        if folded:
            head.label += f"  [set of {len(members)}]"


# --------------------------------------------------------------------------------------
# Looking inside images
# --------------------------------------------------------------------------------------

def run(cmd, timeout=3600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


READERS = {
    "bsdtar": (["bsdtar", "-tf"], None),
    "7z-iso": (["7z", "l", "-ba", "-slt", "-tiso"], "-tiso"),
    "7z-udf": (["7z", "l", "-ba", "-slt", "-tudf"], "-tudf"),
}


def list_iso(path):
    """Return (reader, [lowercased paths], {lower: original}). bsdtar reads ISO9660 with
    Rock Ridge/Joliet names but chokes on some images (Vista x86); 7z covers those, and
    UDF-only images (Windows 7+) need 7z in UDF mode. The reader seeing most files wins."""
    best = ("bsdtar", [])
    for reader, (cmd, _) in READERS.items():
        r = run(cmd + [path], timeout=300)
        if r.returncode != 0:
            continue
        if reader == "bsdtar":
            names = [n.rstrip("/") for n in r.stdout.splitlines() if n.strip()]
            names = [n[2:] if n.startswith("./") else n for n in names]
        else:
            names = [l[7:].replace("\\", "/") for l in r.stdout.splitlines() if l.startswith("Path = ")]
        if len(names) > len(best[1]):
            best = (reader, names)
        if reader == "bsdtar" and len(names) >= 5 and any("/" in n for n in names):
            break  # good enough, skip the slower 7z passes
    reader, names = best
    return reader, [n.lower() for n in names], {n.lower(): n for n in names}


def extract(entry, tool, members, dest, image=None):
    """Extract the given members (original case) from the image into dest."""
    os.makedirs(dest, exist_ok=True)
    if not members:
        return
    image = image or entry.real
    if tool == "bsdtar":
        r = run(["bsdtar", "-xf", image, "-C", dest, *members])
    else:
        r = run(["7z", "x", "-y", READERS[tool][1], f"-o{dest}", image, *members])
    # ISO permissions come out read-only: later extractions into the same tree (CD2 of a
    # set) and cache cleanup both need write access
    run(["chmod", "-R", "u+w", dest])
    if r.returncode != 0:
        raise RuntimeError(f"{tool} extract failed: {(r.stderr or r.stdout).strip()[-400:]}")


def read_member(entry, tool, member):
    if tool == "bsdtar":
        r = subprocess.run(["bsdtar", "-xOf", entry.real, member], capture_output=True, timeout=120)
    else:
        r = subprocess.run(["7z", "e", "-so", READERS[tool][1], entry.real, member], capture_output=True, timeout=120)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


def download(url, dest):
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    os.replace(tmp, dest)


def under(names, prefix):
    return [n for n in names if n.startswith(prefix)]


def first(names, pattern):
    rx = re.compile(pattern)
    hits = sorted(n for n in names if rx.fullmatch(n))
    return hits[0] if hits else None


def eltorito(path):
    """Boot entries of an ISO's El Torito catalog as [(platform, media)], where platform
    is "pcbios" or "efi" and media is "none" (no emulation), "floppy" or "hdd"."""
    import struct
    entries = []
    try:
        with open(path, "rb") as f:
            f.seek(17 * 2048)
            brvd = f.read(2048)
            if brvd[0:6] != b"\x00CD001" or not brvd[7:30].startswith(b"EL TORITO"):
                return entries
            f.seek(struct.unpack_from("<I", brvd, 0x47)[0] * 2048)
            cat = f.read(2048)
    except OSError:
        return entries
    plat = lambda b: {0: "pcbios", 0xEF: "efi"}.get(b, f"other{b}")
    media = lambda b: "none" if (b & 0x0F) == 0 else ("hdd" if (b & 0x0F) == 4 else "floppy")
    if cat[0] != 1:
        return entries
    if cat[32] == 0x88:
        entries.append((plat(cat[1]), media(cat[33])))
    off = 64
    while off + 32 <= len(cat) and cat[off] in (0x90, 0x91):
        platform, count = plat(cat[off + 1]), struct.unpack_from("<H", cat, off + 2)[0]
        last = cat[off] == 0x91
        off += 32
        for _ in range(count):
            if off + 32 > len(cat):
                break
            if cat[off] == 0x88:
                entries.append((platform, media(cat[off + 1])))
            off += 32
            while off + 32 <= len(cat) and cat[off] == 0x44:  # extension entries
                off += 32
        if last:
            break
    return entries


def anaconda_release(entry, tool, has, O):
    """(family, major) of an Anaconda ISO, e.g. ("AlmaLinux", "10"), or (None, None)."""
    if ".treeinfo" in has:
        ti = read_member(entry, tool, O(".treeinfo"))
        fam = re.search(r"^\s*(?:family|name)\s*=\s*([A-Za-z]+)", ti, re.M | re.I)
        ver = re.search(r"^\s*version\s*=\s*(\d+)", ti, re.M)
        if fam and ver:
            return fam.group(1), ver.group(1)
    # Alma 10's boot ISO has no .treeinfo; the volume label in its boot config says it,
    # e.g. "inst.stage2=hd:LABEL=AlmaLinux-10-2-x86_64-dvd"
    for cfg in ("efi/boot/grub.cfg", "boot/grub2/grub.cfg", "isolinux/isolinux.cfg"):
        if cfg in has:
            m = re.search(r"LABEL=([A-Za-z]+)[-_ ](\d+)", read_member(entry, tool, O(cfg)))
            if m:
                return m.group(1), m.group(2)
    return None, None


def prepare(entry):
    """Detect the recipe and extract what it needs. Returns meta dict (written to meta.json)."""
    low = entry.rel.lower()
    meta = {"rel": entry.rel, "real": entry.real, "recipe": None, "platforms": ["pcbios", "efi"],
            "files": {}, "args": "", "notes": ""}
    dest = entry.cache_dir

    forced = entry.side.get("recipe")

    if low.endswith(DISK_EXT) or forced == "memdisk":
        meta.update(recipe="memdisk", platforms=["pcbios"],
                    args="floppy" if entry.size <= FLOPPY_MAX else "harddisk")
        return meta

    tool, names, orig = list_iso(entry.real)
    meta["tool"] = tool
    has = set(names)
    O = lambda n: orig[n]

    def pick(recipe):
        return forced in (None, "", "auto", recipe)

    # --- Windows PE / Windows setup: wimboot ---------------------------------------
    if pick("wimboot") and "sources/boot.wim" in has and ("boot/bcd" in has or "efi/microsoft/boot/bcd" in has):
        members = [m for m in ("bootmgr", "boot/bcd", "boot/boot.sdi", "sources/boot.wim",
                               "efi/boot/bootx64.efi") if m in has]
        extract(entry, tool, [O(m) for m in members], dest)
        files = {m: O(m) for m in members}
        meta.update(recipe="wimboot", files=files)
        wim = os.path.getsize(os.path.join(dest, O("sources/boot.wim")))
        if wim > 600 * 2**20:  # WinPE needs roughly twice the WIM in RAM for its ramdisk
            meta["label_hint"] = f"RAM {max(4, round(wim * 2 / 2**30 + 1))}GB+"
        if "sources/install.wim" in has or "sources/install.esd" in has:
            meta["notes"] = ("Windows setup: needs the install media on an SMB share "
                             "(Windows phase, not set up yet); WinPE itself boots.")
        return meta

    # --- Ubuntu / Mint (casper) -----------------------------------------------------
    if pick("casper") and "casper/vmlinuz" in has:
        initrd = first(names, r"casper/initrd(\.lz|\.gz|\.img)?")
        extract(entry, tool, [O("casper/vmlinuz"), O(initrd)], dest)
        # casper downloads the ISO into the initramfs tmpfs; kernels 6.17+ cap that low
        # unless initramfs_options raises it (older kernels ignore the option)
        ram_gb = entry.size / 2**30 * 1.5 + 1.5
        meta.update(recipe="casper", files={"kernel": O("casper/vmlinuz"), "initrd": O(initrd)},
                    args=(f"boot=casper ip=dhcp url={entry.iso_url} cloud-config-url=/dev/null "
                          "initramfs_options=size=90%"),
                    notes=f"downloads the whole ISO into RAM: needs about {ram_gb:.0f}GB RAM")
        meta["label_hint"] = f"RAM {ram_gb:.0f}GB+"
        info = read_member(entry, tool, O(".disk/info")) if ".disk/info" in has else ""
        m = re.search(r"\b(\d{1,2})\.(\d{2})", info)
        if m and int(m.group(1)) < 18:  # casper learned url= around 18.04
            meta["label_hint"] = "casper too old for HTTP, won't find its media"
            meta["notes"] = f"{info.strip()[:60]}: casper before 18.04 has no url= support"
        return meta

    # --- Debian installer ISO: use the matching netboot kernel+initrd ----------------
    if pick("debian-installer") and ".disk/info" in has and "install.amd/vmlinuz" in has and not under(names, "live/"):
        info = read_member(entry, tool, O(".disk/info"))
        m = re.search(r'"([A-Za-z]+)"', info)
        if not m:
            raise RuntimeError(f"can't find the release codename in .disk/info: {info!r}")
        codename = m.group(1).lower()
        base = DEBIAN_NETBOOT.format(codename=codename)
        os.makedirs(dest, exist_ok=True)
        download(base + "linux", os.path.join(dest, "linux"))
        download(base + "initrd.gz", os.path.join(dest, "initrd.gz"))
        meta.update(recipe="debian-installer", files={"kernel": "linux", "initrd": "initrd.gz"},
                    args="", notes=f"netboot installer for '{codename}' from deb.debian.org "
                                   "(packages come from the internet mirror, like netinst)")
        return meta

    # --- Debian live family: GParted, Clonezilla, Debian live, Kali ------------------
    if pick("debian-live") and "live/filesystem.squashfs" in has:
        kernel = first(names, r"live/vmlinuz[^/]*")
        initrd = first(names, r"live/initrd[^/]*")
        if not kernel or not initrd:
            raise RuntimeError("live/ found but no vmlinuz/initrd in it")
        extract(entry, tool, [O(kernel), O(initrd), O("live/filesystem.squashfs")], dest)
        meta.update(recipe="debian-live", files={"kernel": O(kernel), "initrd": O(initrd)},
                    args=(f"boot=live components union=overlay noswap "
                          f"fetch={entry.cache_url}/{q(O('live/filesystem.squashfs'))}"),
                    notes="squashfs is fetched into RAM")
        return meta

    # --- archiso: Arch Linux, SystemRescue ---------------------------------------------
    arch_kernel = first(names, r"([^/]+)/boot/x86_64/vmlinuz[^/]*")
    if pick("archiso") and arch_kernel and first(names, r"[^/]+/x86_64/airootfs\.(sfs|erofs)"):
        basedir = arch_kernel.split("/")[0]
        bootdir = f"{basedir}/boot/x86_64/"
        main_initrd = first(names, re.escape(bootdir) + r"(initramfs-linux|sysresccd)[^/]*\.img")
        ucode = sorted(n for n in names if re.fullmatch(re.escape(basedir) + r"/boot/(intel|amd)[-_]ucode\.img", n))
        if not main_initrd:
            raise RuntimeError(f"no initramfs under {bootdir}")
        extract(entry, tool, [O(basedir)], dest)
        meta.update(recipe="archiso", files={"kernel": O(arch_kernel),
                                             "initrds": [O(u) for u in ucode] + [O(main_initrd)]},
                    args=f"archisobasedir={O(basedir)} archiso_http_srv={entry.cache_url}/ ip=dhcp",
                    notes="root filesystem is fetched into RAM, then copied once more (archiso forces copytoram over HTTP)")
        sfs = first(names, r"[^/]+/x86_64/airootfs\.(sfs|erofs)")
        size = os.path.getsize(os.path.join(dest, O(sfs))) if sfs else 0
        meta["label_hint"] = f"RAM {max(2, round(size * 2 / 2**30 + 1))}GB+"
        return meta

    # --- SystemRescueCd 5.x and older (Gentoo based, sysrcd.dat) ---------------------------
    if pick("sysrcd-legacy") and "sysrcd.dat" in has and "isolinux/initram.igz" in has:
        k64, k32 = "isolinux/rescue64", "isolinux/rescue32"
        members = [m for m in (k64, k32, "isolinux/initram.igz", "sysrcd.dat", "sysrcd.md5") if m in has]
        extract(entry, tool, [O(m) for m in members], dest)
        files = {"kernel": O(k64) if k64 in has else O(k32), "initrd": O("isolinux/initram.igz")}
        if k64 in has and k32 in has:
            files["kernel32"] = O(k32)
        meta.update(recipe="sysrcd-legacy", files=files,
                    args=f"netboot={entry.cache_url}/{q(O('sysrcd.dat'))} dodhcp setkmap=us",
                    notes="sysrcd.dat is fetched into RAM; 32-bit kernel on non-64-bit CPUs")
        return meta

    # --- Fedora-style live ISO (dracut dmsquash-live), old and new layouts ---------------
    if pick("dracut-live") and ("liveos/squashfs.img" in has or first(names, r"liveos/[^/]+\.(img|sfs)")):
        kernel = ("images/pxeboot/vmlinuz" if "images/pxeboot/vmlinuz" in has
                  else first(names, r"(isolinux|syslinux)/vmlinuz[^/]*")
                  or first(names, r"boot/x86_64/loader/linux|boot/vmlinuz[^/]*"))
        initrd = ("images/pxeboot/initrd.img" if "images/pxeboot/initrd.img" in has
                  else first(names, r"(isolinux|syslinux)/initrd[^/]*")
                  or first(names, r"boot/x86_64/loader/initrd|boot/initr[^/]*"))
        if kernel and initrd:
            extract(entry, tool, [O(kernel), O(initrd)], dest)
            ram_gb = entry.size / 2**30 + 1.5
            meta.update(recipe="dracut-live", files={"kernel": O(kernel), "initrd": O(initrd)},
                        args=f"root=live:{entry.iso_url} rd.live.image rd.neednet=1 ip=dhcp",
                        notes=f"downloads the whole ISO into RAM: needs about {ram_gb:.0f}GB RAM")
            meta["label_hint"] = f"RAM {ram_gb:.0f}GB+"
            return meta

    # --- Old Red Hat / Fedora Core installers (RH 7-9, FC1-6): */base/stage2.img ------------
    stage2 = first(names, r"(redhat|fedora|centos)/base/stage2\.img")
    if pick("anaconda-old") and stage2 and "images/pxeboot/vmlinuz" in has:
        tree = os.path.join(dest, "tree")
        extract(entry, tool, sorted({O(n).split("/")[0] for n in names}), tree)
        meta.update(recipe="anaconda-old", platforms=["pcbios"],
                    files={"kernel": "tree/" + O("images/pxeboot/vmlinuz"),
                           "initrd": "tree/" + O("images/pxeboot/initrd.img")},
                    args=f"method={entry.cache_url}/tree/",
                    notes="old Anaconda: HTTP install from the extracted tree (it asks for network settings)")
        return meta

    # --- Mandrake / Mandriva: stage1 installs over HTTP from the merged CD set -------------
    if pick("mandrake") and "isolinux/alt0/vmlinuz" in has and first(names, r"(mandrake|mandriva|media)/base/[^/]+"):
        tree = os.path.join(dest, "tree")
        for i, member in enumerate(entry.set_members):
            if member.real is None:
                raise RuntimeError(f"set member missing: {member.rel}")
            mtool, mnames, morig = (tool, names, orig) if member is entry else list_iso(member.real)
            top = sorted({morig[n].split("/")[0] for n in mnames})
            extract(member, mtool, top, tree, image=member.real)
        cfg = read_member(entry, tool, O("isolinux/isolinux.cfg")) if "isolinux/isolinux.cfg" in has else ""
        m = re.search(r"append\s+initrd=\S+\s+(.*?automatic=)method:cdrom", cfg, re.I)
        base_args = m.group(1).replace("automatic=", "").strip() if m else "ramdisk_size=128000 root=/dev/ram3"
        # stage1 speaks HTTP on port 80 only: the server must answer on :80 (docs/SETUP.md)
        host = urllib.parse.urlsplit(BASE_URL).hostname
        meta.update(recipe="mandrake", platforms=["pcbios"],
                    files={"kernel": "tree/" + O("isolinux/alt0/vmlinuz"),
                           "initrd": "tree/" + O("isolinux/alt0/all.rdz")},
                    args=(f"{base_args} automatic=method:http,network:dhcp,"
                          f"server:{host},directory:/cache/{entry.key}/tree"),
                    notes=(f"HTTP install from the merged tree of {len(entry.set_members)} CD(s), no disc "
                           "swapping; needs the server on port 80 (see docs/SETUP.md)"))
        return meta

    # --- Anaconda installers: AlmaLinux, Rocky, Fedora netinst, RHEL ----------------------
    if pick("anaconda") and "images/pxeboot/vmlinuz" in has and ("images/install.img" in has or ".treeinfo" in has):
        # stage2 needs .treeinfo + images/, an offline install needs the packages too:
        # simplest is the whole tree (a boot ISO is ~1GB, a DVD ~10GB)
        tree = os.path.join(dest, "tree")
        extract(entry, tool, sorted({O(n).split("/")[0] for n in names}), tree)
        tree_url = f"{entry.cache_url}/tree/"
        args = f"inst.stage2={tree_url} ip=dhcp"
        notes = "installer tree extracted to cache"
        if "baseos/repodata/repomd.xml" in has or "packages" in has:
            args += f" inst.repo={tree_url}"
            notes += "; packages from the ISO (offline install)"
        else:
            family, major = anaconda_release(entry, tool, has, O)
            if family and family.lower() in ANACONDA_REPOS:
                repo = ANACONDA_REPOS[family.lower()].format(major=major)
                args += f" inst.repo={repo}"
                notes += f"; packages from {repo}"
            else:
                notes += "; package source: the installer's default"
        img = os.path.join(tree, O("images/install.img")) if "images/install.img" in has else None
        if img and os.path.exists(img):  # stage2 lives in RAM, plus the installer itself
            meta["label_hint"] = f"RAM {max(4, round(os.path.getsize(img) * 2 / 2**30 + 1.5))}GB+"  # Fedora 44 UEFI died at 3GB
        meta.update(recipe="anaconda", files={"kernel": "tree/" + O("images/pxeboot/vmlinuz"),
                                              "initrd": "tree/" + O("images/pxeboot/initrd.img")},
                    args=args, notes=notes)
        return meta

    # --- Anything else: iPXE emulates a CD drive over HTTP --------------------------------
    # iPXE's sanboot only does "no emulation" El Torito; BIOS images that emulate a floppy
    # or hard disk (memtest, many DOS/utility CDs) go through memdisk instead, which loads
    # the ISO into RAM. Platforms without a boot entry are hidden from the menu.
    boot = eltorito(entry.real)
    bios = [m for p, m in boot if p == "pcbios"]
    platforms = []
    if bios:
        platforms.append("pcbios")
    if any(p == "efi" for p, _ in boot):
        platforms.append("efi")
    bios_method = "sanboot"
    if bios and "none" not in bios:
        bios_method = "memdisk-iso"
        if entry.size > 64 * 2**20:  # memdisk holds the whole ISO in RAM
            meta["label_hint"] = f"BIOS: RAM {entry.size / 2**30 + 0.5:.1f}GB+"
    meta.update(recipe="sanboot", platforms=platforms or ["pcbios", "efi"], bios_method=bios_method,
                eltorito=[f"{p}:{m}" for p, m in boot],
                notes="generic CD emulation: works until an OS kernel loads its own disk drivers"
                      + ("; BIOS uses memdisk (floppy-emulation boot image), ISO loaded into RAM"
                         if bios_method == "memdisk-iso" else ""))
    return meta


# --------------------------------------------------------------------------------------
# Background preparation worker
# --------------------------------------------------------------------------------------

_work = queue.Queue()
_queued = set()
_busy = {}
_lock = threading.Lock()


def enqueue(entry):
    if not entry.key or entry.set_head or entry.meta() is not None:
        return
    with _lock:
        if entry.key in _queued:
            return
        _queued.add(entry.key)
    _work.put(entry)


def worker():
    while True:
        entry = _work.get()
        started = time.time()
        with _lock:
            _busy[entry.key] = entry.rel
        log("preparing", entry.rel)
        try:
            meta = prepare(entry)
            meta["state"] = "ready"
        except Exception as e:  # keep going; the error shows up in the menu and /status
            traceback.print_exc()
            meta = {"rel": entry.rel, "state": "error", "error": str(e)[:500]}
        meta["prepared_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        meta["seconds"] = round(time.time() - started, 1)
        os.makedirs(entry.cache_dir, exist_ok=True)
        tmp = os.path.join(entry.cache_dir, "meta.json.tmp")
        with open(tmp, "w") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp, os.path.join(entry.cache_dir, "meta.json"))
        log("done", entry.rel, meta.get("recipe") or meta.get("error"), f"{meta['seconds']}s")
        with _lock:
            _busy.pop(entry.key, None)
            _queued.discard(entry.key)


def scanner():
    while True:
        try:
            for e in scan_library():
                enqueue(e)
        except Exception:
            traceback.print_exc()
        time.sleep(SCAN_INTERVAL)


# --------------------------------------------------------------------------------------
# iPXE scripts
# --------------------------------------------------------------------------------------

def load_hosts():
    """hosts.conf: '<mac> <library path, entry id or "writer"> [timeout seconds]' ->
    preselect an entry for a machine. The path may contain spaces; lines starting with # are comments."""
    hosts = {}
    try:
        with open(HOSTS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                target, timeout = parts[1], 5
                m = re.fullmatch(r"(.*?)\s+(\d+)", target)
                if m:
                    target, timeout = m.group(1), int(m.group(2))
                hosts[parts[0].lower().replace(":", "-")] = (target, timeout)
    except OSError:
        pass
    return hosts


def section_key(name):
    return (SECTION_ORDER.index(name) if name in SECTION_ORDER else len(SECTION_ORDER), name)


def render_menu(platform, mac):
    entries = [e for e in scan_library() if not e.hidden]
    for e in entries:
        enqueue(e)
    host = load_hosts().get((mac or "").lower())

    out = ["#!ipxe", f"set pxebase {BASE_URL}", ":start",
           f"menu Pontifex network boot  [{platform}]"]
    targets = []
    default, timeout = "exit", MENU_TIMEOUT
    current = None
    for e in sorted(entries, key=lambda e: (section_key(e.section), e.subsection, e.label.lower())):
        heading = e.section + (f" / {e.subsection}" if e.subsection else "")
        meta = e.meta()
        if meta and meta.get("state") == "ready" and platform not in meta.get("platforms", []):
            continue  # e.g. memdisk floppies on UEFI
        if heading != current:
            out.append(f"item --gap --  ---- {ipxe_text(heading)} ----")
            current = heading
        label = ipxe_text(e.label)
        if e.error:
            out.append(f"item --gap --    {label}  [BROKEN: {ipxe_text(e.error)}]")
            continue
        if meta is None:
            out.append(f"item --gap --    {label}  [preparing...]")
            continue
        if meta.get("state") == "error":
            out.append(f"item --gap --    {label}  [ERROR, see /status]")
            continue
        hint = f"  ({meta['label_hint']})" if meta.get("label_hint") else ""
        # no recipe recognized the image: it gets generic CD emulation, which may not get far
        unknown = "  [unknown]" if meta.get("recipe") == "sanboot" else ""
        out.append(f"item e{e.id}   {label}{hint}{unknown}")
        targets.append(e)
        if host and host[0] in (e.rel, e.id):
            default, timeout = f"e{e.id}", host[1]

    out += ["item --gap --  ---- other ----"]
    if writer_available(platform):
        out += ["item writer       Write an image to a local disk (brandr: USB/IDE/CF/floppy)"]
    out += ["item netbootxyz   netboot.xyz online installers (internet)",
            "item shell        iPXE shell",
            "item reboot       Reboot",
            "item exit         Exit (continue with the next boot device)"]
    if host and host[0] == "writer" and writer_available(platform):
        default, timeout = "writer", host[1]
    tmo = f" --timeout {timeout * 1000}" if timeout else ""
    out += [f"choose --default {default}{tmo} target && goto ${{target}} || goto exit", ""]
    for e in targets:
        out += [f":e{e.id}",
                f"chain ${{pxebase}}/entry/{e.id}.ipxe?platform=${{platform}} || goto failed",
                "goto start"]
    out += [":writer", "chain ${pxebase}/writer.ipxe?platform=${platform} || goto failed", "goto start",
            ":netbootxyz", "chain --autofree https://boot.netboot.xyz || goto failed", "goto start",
            ":shell", "echo Type 'exit' to get back to the menu", "shell", "goto start",
            ":reboot", "reboot",
            ":failed", "echo", "echo Boot failed (${errno}). Press any key for the menu.", "prompt", "goto start",
            ":exit", "exit", ""]
    return "\n".join(out)


def render_entry(entry, platform):
    meta = entry.meta()
    if not meta or meta.get("state") != "ready":
        return "#!ipxe\necho This entry is not ready yet\nprompt\nexit 1\n"
    c = entry.cache_url
    f = meta.get("files", {})
    extra = entry.side.get("args", "")
    recipe = meta["recipe"]
    lines = ["#!ipxe", f"echo Booting {ipxe_text(entry.label)} ({recipe})"]
    if meta.get("notes"):
        lines.append(f"echo Note: {ipxe_text(meta['notes'])}")

    if recipe == "memdisk":
        lines += [f"kernel {BASE_URL}/files/memdisk {meta['args']} {extra}".rstrip(),
                  f"initrd {entry.iso_url}", "boot"]
    elif recipe == "sanboot" and platform == "pcbios" and meta.get("bios_method") == "memdisk-iso":
        lines += [f"kernel {BASE_URL}/files/memdisk iso raw {extra}".rstrip(),
                  f"initrd {entry.iso_url}", "boot"]
    elif recipe == "sanboot":
        lines += [f"sanboot --no-describe {entry.iso_url}"]
    elif recipe == "wimboot":
        lines += [f"kernel {BASE_URL}/files/wimboot {extra}".rstrip()]
        names = {"bootmgr": "bootmgr", "boot/bcd": "BCD", "boot/boot.sdi": "boot.sdi",
                 "sources/boot.wim": "boot.wim", "efi/boot/bootx64.efi": "bootx64.efi"}
        for low, name in names.items():
            if low in f and not (low == "bootmgr" and platform == "efi"):
                lines.append(f"initrd -n {name} {c}/{q(f[low])} {name}")
        lines.append("boot")
    else:  # kernel + initrd(s) recipes
        initrds = f.get("initrds") or [f["initrd"]]
        names = [f"initrd{i}.img" for i in range(len(initrds))]
        args = " ".join(x for x in [" ".join(f"initrd={n}" for n in names), meta.get("args", ""), extra] if x)
        if f.get("kernel32"):  # old CPUs without long mode get the 32-bit kernel
            lines += [f"set kernel {c}/{q(f['kernel32'])}",
                      f"cpuid --ext 29 && set kernel {c}/{q(f['kernel'])} ||"]
            lines.append(f"kernel ${{kernel}} {args}")
        else:
            lines.append(f"kernel {c}/{q(f['kernel'])} {args}")
        lines += [f"initrd -n {n} {c}/{q(path)}" for n, path in zip(names, initrds)]
        lines.append("boot")
    return "\n".join(lines) + "\n"


FLOPPY_SIZES = {163840, 184320, 327680, 368640, 737280, 1228800, 1474560, 1720320, 1763328, 2949120}

# What writing an image raw to a disk produces, and how to boot the result
DISK_KINDS = {
    "hybrid": "USB-HDD / HDD",
    "disk": "HDD / USB-HDD",
    "floppy": "floppy / USB-FDD",
    "cd-only": "won't boot from a disk (CD-only image)",
    "unknown": "unknown",
}


def disk_kind(path, size):
    """Classify an image by what a raw write to a disk gives you (for the writer)."""
    if size in FLOPPY_SIZES:
        return "floppy"
    try:
        with open(path, "rb") as f:
            mbr = f.read(512)
            f.seek(0x8001)
            iso = f.read(5) == b"CD001"
    except OSError:
        return "unknown"
    has_mbr = len(mbr) == 512 and mbr[510:512] == b"\x55\xaa" and any(
        mbr[446 + 16 * i + 4] != 0 for i in range(4))  # boot signature + a partition type set
    if iso:
        return "hybrid" if has_mbr else "cd-only"
    return "disk" if has_mbr else "unknown"


# --------------------------------------------------------------------------------------
# Disk layouts built from floppy images, for old BIOSes that boot USB sticks as a hard
# disk (USB-HDD) or a ZIP drive (USB-ZIP): MBR + FAT partition with syslinux, memdisk and
# the floppy image, which memdisk then boots as drive A:. The writer writes them raw.
# --------------------------------------------------------------------------------------

VARIANT_SIZE = 8 * 2**20          # 8 cylinders x 64 heads x 32 sectors
VARIANTS = {
    # name: (mkdiskimage flags, boot hint)
    "usb-hdd": ([], "USB-HDD"),
    "usb-zip": (["-z", "-4"], "USB-ZIP"),
}
SYSLINUX_CFG = """DEFAULT floppy
PROMPT 0
TIMEOUT 1
LABEL floppy
  KERNEL memdisk
  APPEND initrd=floppy.img floppy
"""
_variant_lock = threading.Lock()


def build_variant(entry, variant):
    """Build (once) and return the path of a USB-HDD/USB-ZIP layout of a floppy image."""
    flags, _ = VARIANTS[variant]
    out_dir = os.path.join(entry.cache_dir, "variants")
    out = os.path.join(out_dir, f"{variant}.img")
    with _variant_lock:
        if os.path.exists(out):
            return out
        os.makedirs(out_dir, exist_ok=True)
        tmp = out + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)  # half-built leftover from a crash, not a finished artefact
        cfg = os.path.join(out_dir, "syslinux.cfg")
        with open(cfg, "w") as f:
            f.write(SYSLINUX_CFG)
        # mkdiskimage: MBR + one FAT partition in 64-head/32-sector geometry (partition 4 for ZIP)
        r = run(["mkdiskimage", "-o", *flags, tmp, "8", "64", "32"])
        if r.returncode != 0:
            raise RuntimeError(f"mkdiskimage: {r.stderr.strip()}")
        offset = r.stdout.strip()
        with open("/usr/lib/syslinux/mbr/mbr.bin", "rb") as f:
            mbr = f.read(440)
        with open(tmp, "r+b") as f:
            f.write(mbr)
        for cmd in (["syslinux", "--offset", offset, "--install", tmp],
                    ["mcopy", "-i", f"{tmp}@@{offset}", "/usr/lib/syslinux/memdisk", "::/memdisk"],
                    ["mcopy", "-i", f"{tmp}@@{offset}", entry.real, "::/floppy.img"],
                    ["mcopy", "-i", f"{tmp}@@{offset}", cfg, "::/syslinux.cfg"]):
            r = run(cmd)
            if r.returncode != 0:
                raise RuntimeError(f"{cmd[0]}: {(r.stderr or r.stdout).strip()}")
        if os.path.getsize(tmp) != VARIANT_SIZE:
            raise RuntimeError(f"unexpected size {os.path.getsize(tmp)}")
        os.replace(tmp, out)
        log("built", variant, "for", entry.rel)
        return out


# --------------------------------------------------------------------------------------
# UEFI stick from a CD-only ISO (Rufus style): MBR + one active FAT32 partition with the
# ISO's files. Windows: install.wim over 4GB is split into .swm parts, and syslinux
# chainloads bootmgr so the stick boots on BIOS too. Built in the background on request
# (it takes a minute or two and about the ISO's size in cache).
# --------------------------------------------------------------------------------------

FAT32_MAX_FILE = 4 * 2**30 - 1
_uefi_build_lock = threading.Lock()   # one build at a time
_uefi_running = set()


def uefi_candidate(e, kind):
    """CD-only ISOs with an EFI El Torito entry (hybrid ISOs already work written raw)."""
    return (kind == "cd-only" and e.rel.lower().endswith(".iso") and e.key
            and any(p == "efi" for p, _ in eltorito(e.real)))


def uefi_paths(e):
    d = os.path.join(e.cache_dir, "variants")
    return d, os.path.join(d, "uefi.img"), os.path.join(d, "uefi.json")


def uefi_status(e):
    _, img, status = uefi_paths(e)
    try:
        with open(status) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {"state": "not-built"}
    if st.get("state") == "ready" and not os.path.exists(img):
        st = {"state": "not-built"}
    return st


def _set_uefi_status(e, **st):
    d, _, status = uefi_paths(e)
    os.makedirs(d, exist_ok=True)
    with open(status + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(status + ".tmp", status)


def start_uefi_build(e):
    with _lock:
        if e.key in _uefi_running:
            return
        _uefi_running.add(e.key)
    _set_uefi_status(e, state="building", step="queued")
    threading.Thread(target=_uefi_build_thread, args=(e,), daemon=True).start()


def _uefi_build_thread(e):
    try:
        with _uefi_build_lock:
            size, boot_hint = build_uefi(e)
        _set_uefi_status(e, state="ready", size=size, boot_hint=boot_hint)
    except Exception as ex:  # shown to the user by the writer
        traceback.print_exc()
        _set_uefi_status(e, state="error", error=str(ex)[:300])
    finally:
        with _lock:
            _uefi_running.discard(e.key)


def iso_volume_label(path):
    """FAT label (11 chars, upper case) from the ISO's volume id."""
    try:
        with open(path, "rb") as f:
            f.seek(0x8028)
            vid = f.read(32).decode("ascii", "replace").strip()
    except OSError:
        vid = ""
    label = re.sub(r"[^A-Z0-9_-]", "_", vid.upper())[:11].rstrip("_")
    return label or "PONTIFEX"


def build_uefi(e):
    d, img, _ = uefi_paths(e)
    tree = os.path.join(d, "uefi-tree")
    tmp = img + ".tmp"
    for leftover in (tree, tmp):   # half-built leftovers from an interrupted build
        if os.path.isdir(leftover):
            shutil.rmtree(leftover)
        elif os.path.exists(leftover):
            os.remove(leftover)

    tool, names, orig = list_iso(e.real)
    if "efi/boot/bootx64.efi" not in names:
        raise RuntimeError("this ISO has no \\EFI\\BOOT\\BOOTX64.EFI to boot on UEFI")
    windows = "bootmgr" in names and "sources/boot.wim" in names

    _set_uefi_status(e, state="building", step="extracting the ISO")
    extract(e, tool, sorted({orig[n].split("/")[0] for n in names}), tree)

    wim = next((os.path.join(root, f) for root, _, files in os.walk(tree) for f in files
                if f.lower() == "install.wim"), None)
    if wim and os.path.getsize(wim) > FAT32_MAX_FILE:
        _set_uefi_status(e, state="building", step="splitting install.wim for FAT32")
        swm = os.path.join(os.path.dirname(wim), "install.swm")
        r = run(["wimlib-imagex", "split", wim, swm, "3800"])
        if r.returncode != 0:
            raise RuntimeError(f"wimlib split: {(r.stderr or r.stdout).strip()[-300:]}")
        os.remove(wim)

    total = sum(os.path.getsize(os.path.join(root, f))
                for root, _, files in os.walk(tree) for f in files)
    mib = 2**20
    part = ((int(total * 1.04) + 96 * mib) // mib + 1) * mib   # FAT overhead + slack
    with open(tmp, "wb") as f:
        f.truncate(mib + part)                                    # sparse
    r = subprocess.run(
        ["sfdisk", "--no-reread", "--no-tell-kernel", tmp],
        input="label: dos\nstart=2048, type=c, bootable\n", capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"sfdisk: {r.stderr.strip()}")

    _set_uefi_status(e, state="building", step="formatting FAT32")
    r = run(["mkfs.fat", "-F", "32", "-n", iso_volume_label(e.real), "--offset=2048",
             tmp, str(part // 1024)])
    if r.returncode != 0:
        raise RuntimeError(f"mkfs.fat: {(r.stderr or r.stdout).strip()}")

    _set_uefi_status(e, state="building", step="copying files")
    fat = f"{tmp}@@{mib}"
    top = [os.path.join(tree, n) for n in sorted(os.listdir(tree))]
    r = run(["mcopy", "-s", "-Q", "-m", "-i", fat, *top, "::/"], timeout=7200)
    if r.returncode != 0:
        raise RuntimeError(f"mcopy: {(r.stderr or r.stdout).strip()[-300:]}")

    boot_hint = "UEFI (USB)"
    if windows:
        # BIOS path: syslinux in the partition chainloads Windows' bootmgr
        with open(os.path.join(d, "syslinux.cfg"), "w") as f:
            f.write("DEFAULT win\nPROMPT 0\nTIMEOUT 1\nLABEL win\n"
                    "  COM32 chain.c32\n  APPEND fs ntldr=/bootmgr\n")
        mods = "/usr/lib/syslinux/modules/bios"
        cmds = [["syslinux", "--offset", str(mib), "--install", tmp],
                ["mcopy", "-i", fat, f"{mods}/chain.c32", f"{mods}/libcom32.c32",
                 f"{mods}/libutil.c32", os.path.join(d, "syslinux.cfg"), "::/"]]
        for cmd in cmds:
            r = run(cmd)
            if r.returncode != 0:
                raise RuntimeError(f"{cmd[0]}: {(r.stderr or r.stdout).strip()}")
        with open("/usr/lib/syslinux/mbr/mbr.bin", "rb") as f:
            mbr = f.read(440)
        with open(tmp, "r+b") as f:
            f.write(mbr)
        boot_hint = "UEFI (USB), or USB-HDD on BIOS"

    shutil.rmtree(tree)
    os.replace(tmp, img)
    log("built uefi stick for", e.rel, f"{os.path.getsize(img) / 2**30:.1f} GB")
    return os.path.getsize(img), boot_hint


def variants_of(e, kind):
    """Extra write variants the catalog offers for an image."""
    if kind == "floppy" and e.size <= FLOPPY_MAX:
        return [{"method": name, "url": f"{BASE_URL}/variant/{e.id}/{name}.img",
                 "size": VARIANT_SIZE, "boot_hint": hint, "state": "ready"}
                for name, (_, hint) in VARIANTS.items()]
    if uefi_candidate(e, kind):
        st = uefi_status(e)
        return [{"method": "uefi", "url": f"{BASE_URL}/variant/{e.id}/uefi.img",
                 "status_url": f"{BASE_URL}/variant/{e.id}/uefi.json",
                 "size": st.get("size"), "state": st["state"],
                 "boot_hint": st.get("boot_hint", "UEFI (USB)")}]
    return []


def render_images_json():
    """Catalog for the writer: every library image (set members too), streamable URLs."""
    images = []
    for e in scan_library():
        if e.error or not e.real:
            continue
        kind = disk_kind(e.real, e.size)
        heading = e.section + (f" / {e.subsection}" if e.subsection else "")
        in_set = e.set_head is not None or len(e.set_members) > 1
        item = {"name": os.path.basename(e.rel) if in_set else e.label,
                "section": heading, "path": e.rel, "url": e.iso_url,
                "size": e.size, "kind": kind, "boot_hint": DISK_KINDS[kind]}
        item["variants"] = variants_of(e, kind)
        head = e.set_head or (e if len(e.set_members) > 1 else None)
        if head:
            item["set"] = head.id
            item["set_index"] = head.set_members.index(e) + 1
            item["set_size"] = len(head.set_members)
        images.append(item)
    images.sort(key=lambda i: (section_key(i["section"].split(" / ")[0]), i["section"], i["name"].lower()))
    return json.dumps({"server": BASE_URL, "images": images}, indent=1)


# The brandr disk writer (Tiny Core + brandr), built by writer/build.sh into
# tftp/brandr/<arch>/: vmlinuz, core.gz (Tiny Core's own) and writer.gz (our overlay)
WRITER_DIR = os.environ.get("PXE_WRITER", "/srv/pontifex/tftp/brandr")
WRITER_ARCH = {"pcbios": "x86", "efi": "x86_64"}
WRITER_FILES = ("vmlinuz", "core.gz", "writer.gz")


def writer_available(platform):
    d = os.path.join(WRITER_DIR, WRITER_ARCH[platform])
    return all(os.path.isfile(os.path.join(d, f)) for f in WRITER_FILES)


def render_writer(platform):
    if not writer_available(platform):
        return "#!ipxe\necho The disk writer is not installed for this platform\nprompt\nexit 1\n"
    base = f"{BASE_URL}/files/brandr/{WRITER_ARCH[platform]}"
    # Tiny Core boot codes: superuser = root autologin on tty1 (the writer takes it over),
    # base/norestore = no extensions or backups from local disks, noswap = never touch them
    args = (f"initrd=core.gz initrd=writer.gz quiet superuser base norestore noswap nozswap "
            f"pontifex.server={BASE_URL}")
    return "\n".join(["#!ipxe", "echo Booting the disk writer (Tiny Core + brandr)...",
                      f"kernel {base}/vmlinuz {args}",
                      f"initrd -n core.gz {base}/core.gz",
                      f"initrd -n writer.gz {base}/writer.gz",
                      "boot", ""])


def render_status():
    entries = scan_library()
    rows = [f"Pontifex  library={LIBRARY}  base={BASE_URL}", ""]
    with _lock:
        busy = dict(_busy)
        queued = len(_queued)
    for e in entries:
        meta = e.meta() or {}
        state = ("BROKEN: " + e.error) if e.error else ("preparing" if e.key in busy else meta.get("state", "queued"))
        if e.set_head:
            state = f"in-set-of:{e.set_head.id}"
        rows.append(f"{e.id}  {e.rel}")
        rows.append(f"      -> {e.real}  ({e.size / 2**20:.0f} MB)")
        rows.append(f"      state={state}  recipe={meta.get('recipe')}  platforms={','.join(meta.get('platforms', []))}")
        if meta.get("error"):
            rows.append(f"      error: {meta['error']}")
        if meta.get("notes"):
            rows.append(f"      notes: {meta['notes']}")
        if meta.get("args"):
            rows.append(f"      args:  {meta['args']}")
    rows += ["", f"worker: {len(busy)} busy, {queued} queued"]
    known = {e.key for e in entries if e.key}
    try:
        orphans = [d for d in os.listdir(CACHE) if d not in known and not d.startswith(".")]
    except OSError:
        orphans = []
    if orphans:
        rows += [f"orphaned cache dirs (safe to remove after a backup): {' '.join(sorted(orphans))}"]
    return "\n".join(rows) + "\n"


class Handler(BaseHTTPRequestHandler):
    server_version = "pxe-menu/1"

    def _send(self, body, ctype="text/plain; charset=utf-8", code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path):
        """Static file with single-range support (the writer resumes with Range)."""
        size = os.path.getsize(path)
        start, end = 0, size - 1
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if m:
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        qs = dict(urllib.parse.parse_qsl(url.query))
        platform = qs.get("platform", "pcbios")
        platform = platform if platform in ("pcbios", "efi") else "pcbios"
        try:
            if url.path == "/menu.ipxe":
                return self._send(render_menu(platform, qs.get("mac", "")))
            m = re.fullmatch(r"/entry/([0-9a-f]{12})\.ipxe", url.path)
            if m:
                for e in scan_library():
                    if e.id == m.group(1):
                        return self._send(render_entry(e, platform))
                return self._send("#!ipxe\necho Unknown entry\nprompt\n", code=404)
            m = re.fullmatch(r"/variant/([0-9a-f]{12})/uefi\.(img|json)", url.path)
            if m:
                for e in scan_library():
                    if e.id == m.group(1) and e.key and not e.error:
                        st = uefi_status(e)
                        if st["state"] in ("not-built", "error") and qs.get("build", "1") == "1" \
                                and uefi_candidate(e, disk_kind(e.real, e.size)):
                            start_uefi_build(e)
                            st = uefi_status(e)
                        if m.group(2) == "json":
                            return self._send(json.dumps(st), ctype="application/json")
                        if st["state"] == "ready":
                            return self._send_file(uefi_paths(e)[1])
                        return self._send(json.dumps(st), ctype="application/json", code=503)
                return self._send("unknown image\n", code=404)
            m = re.fullmatch(r"/variant/([0-9a-f]{12})/(usb-hdd|usb-zip)\.img", url.path)
            if m:
                for e in scan_library():
                    if e.id == m.group(1) and e.key and not e.error:
                        return self._send_file(build_variant(e, m.group(2)))
                return self._send("unknown image\n", code=404)
            if url.path == "/writer.ipxe":
                return self._send(render_writer(platform))
            if url.path == "/images.json":
                return self._send(render_images_json(), ctype="application/json")
            if url.path in ("/", "/status"):
                return self._send(render_status())
            if url.path == "/reprepare":
                # move one entry's cache aside (never deleted here) so it is prepared again
                done = []
                for e in scan_library():
                    if e.id in qs.get("id", "").split(",") and e.key and os.path.isdir(e.cache_dir):
                        os.makedirs(os.path.join(CACHE, ".old"), exist_ok=True)
                        os.rename(e.cache_dir, os.path.join(CACHE, ".old", f"{e.key}-{time.strftime('%Y%m%d-%H%M%S')}"))
                        enqueue(e)
                        done.append(e.rel)
                return self._send("".join(f"re-preparing {r}\n" for r in done) or "nothing matched\n")
            if url.path == "/rescan":
                for e in scan_library():
                    enqueue(e)
                return self._send("rescan queued\n")
            self._send("not found\n", code=404)
        except Exception:
            traceback.print_exc()
            self._send("#!ipxe\necho pxe-menu internal error, see container log\nprompt\n", code=500)

    do_HEAD = do_GET

    def log_message(self, fmt, *args):
        log(self.address_string(), fmt % args)


def main():
    os.makedirs(CACHE, exist_ok=True)
    for tool in ("bsdtar", "7z"):
        if not shutil.which(tool):
            log(f"WARNING: {tool} not found on PATH")
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    log(f"listening on :{LISTEN_PORT}, library {LIBRARY}")
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
