# The image library

## Adding images
```sh
ln -s /srv/isos/debian-13-netinst.iso /srv/pontifex/library/linux/
```
That's all. Within a minute (or at once with `curl http://<server>:<port>/rescan`) the menu
service looks inside the image, picks a recipe and extracts what it needs into `cache/`. Until
then the menu shows it as `[preparing...]`. `http://<server>:<port>/status` lists every entry
with its recipe, platforms and any error.

- **Folders are menu sections.** `linux`, `windows`, `tools` and `retro` come first, others
  alphabetically. Subfolders become "section / sub" headings. A `10-foo` style prefix only
  sets the order.
- **Folder symlinks are followed**: `library/windows/nas -> /srv/isos/Windows` brings in a whole
  tree, including anything added to it later.
- **The link targets must be under `PONTIFEX_SOURCES`**, which is what the containers can see.
  A link pointing elsewhere shows as `[BROKEN: dangling link]`.
- Supported files: `.iso`, and floppy/disk images `.img .ima .vfd .flp .dsk`.

### Sets
Numbered files in one folder (`Disk 1.img` ... `Disk 22.img`, `cd1/cd2/cd3.iso`) show as **one
entry** labelled "set of N". Later discs that are bootable on their own stay visible. A
`set.yaml` in a folder makes every image in it one set, optionally with `label: Nicer name`.
Sets matter for installs that need all their discs: the Mandrake recipe merges its 3 CDs into one
HTTP install tree (no disc swapping), and the disk writer writes floppy sets disk by disk.

### Per-image options
Put a sidecar `<image>.yaml` next to the link (flat `key: value` lines):
```yaml
label: Nicer name in the menu
recipe: sanboot           # override detection (see the recipe names below)
args: nomodeset           # extra kernel arguments
hide: yes                 # keep it out of the menu
```
After changing a sidecar or an image, `curl "http://<server>:<port>/reprepare?id=<id>"` (the id
is on `/status`) re-detects it. The old cache folder is moved to `cache/.old/`, never deleted.

## How images are booted
The menu service reads each image's file list and El Torito boot catalog, then picks the first
recipe that matches:

| Detected by | Recipe | How it boots | Firmware |
|---|---|---|---|
| `.img/.ima/.vfd/.flp/.dsk` | memdisk | the whole image in RAM as a floppy (up to 2.88 MB) or hard disk | BIOS |
| `sources/boot.wim` + `boot/bcd` | wimboot | WinPE / Windows setup from RAM | both |
| `casper/vmlinuz` | casper | Ubuntu/Mint: kernel + initrd, `url=` loads the ISO into RAM | both |
| `.disk/info` + `install.amd/` | debian-installer | the matching netboot kernel + initrd from deb.debian.org (the CD initrd can't netboot) | both |
| `live/filesystem.squashfs` | debian-live | GParted, Clonezilla, Debian live: `fetch=` squashfs | both |
| `<base>/boot/x86_64/vmlinuz*` + airootfs | archiso | Arch, EndeavourOS, SystemRescue 6+: `archiso_http_srv=` | both |
| `sysrcd.dat` | sysrcd-legacy | SystemRescueCd 5 and older: `netboot=`; 32-bit kernel on CPUs without long mode | both |
| `LiveOS/squashfs.img` | dracut-live | Fedora and Nobara live: `root=live:` (whole ISO into RAM) | both |
| `*/base/stage2.img` + pxeboot | anaconda-old | Red Hat 7-9, Fedora Core 1-6: `method=` HTTP tree | BIOS |
| `isolinux/alt0` + `Mandrake/base` | mandrake | stage1 HTTP install from the merged CD set ([needs port 80](SETUP.md#old-installers-that-need-port-80)) | BIOS |
| `images/pxeboot/` (+ install.img / .treeinfo) | anaconda | Alma, Rocky, Fedora netinst and DVD: `inst.stage2=`, packages from the ISO or the distro mirror | both |
| anything else | sanboot | iPXE emulates a CD drive over HTTP (floppy-emulation boot images use memdisk instead) | per El Torito |

Entries that fell back to generic CD emulation are tagged **`[unknown]`** in the menu. Their
boot loader starts, but whatever runs after it may not find its media (see [limits](#limits)).
Big images show how much **RAM** they need, e.g. `(RAM 6GB+)`, since live systems and some
installers load everything into memory.

Entries that can't work on a firmware are left out of that menu: floppy images and
memdisk-only ISOs don't appear on UEFI, and ISOs without an EFI boot entry don't either.

## The cache
`cache/<key>/` holds what each image needed: kernels, initrds, a squashfs, a full install tree
(Anaconda, Mandrake) or a Windows stick layout. The key includes the target file's size and
modification time, so repointing a symlink at a new file never serves stale files. Sizes range
from a few MB to the ISO's size (install trees, UEFI sticks). Everything in it can be rebuilt,
and superseded folders go to `cache/.old/` for you to delete.

### Layouts the server builds for the disk writer
- **Floppy as USB-HDD / USB-ZIP**: an 8 MB disk (MBR + FAT with syslinux, memdisk and the
  floppy image), built on first request.
- **UEFI stick** from a CD-only ISO with an EFI boot entry: FAT32 + the ISO's files; Windows'
  `install.wim` is split with wimlib if it's over 4 GB, and syslinux chainloads `bootmgr` for
  BIOS. Built in the background on first request (a minute or two); it takes about the ISO's
  size in `cache/`.

## Limits
- **Old live CDs** (Knoppix, MX/antiX, Puppy, older PCLinuxOS, Ubuntu before 18.04, Corel) boot
  their loader through CD emulation, but their kernel then looks for a real CD and gives up.
  Recipes for these families are welcome (see [DEVELOPING.md](DEVELOPING.md)).
- **Windows 9x / NT / XP setup CDs** boot (their floppy-emulation boot images work), but setup
  can't see the CD afterwards. Write their boot floppies with the disk writer instead, or use a
  CD drive.
- **Multi-floppy installers** boot disk 1 from the menu. memdisk can't swap disks, so write the
  set to real floppies with the disk writer.
- **Windows setup from the menu** boots WinPE, but installing needs the install media on an
  SMB share, which Pontifex doesn't set up. A UEFI stick from the disk writer works instead.
- **Secure Boot** has to be off to netboot.
