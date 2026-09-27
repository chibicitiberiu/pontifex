# Developing Pontifex

## Repository layout
```
setup.sh                  install/upgrade on a server (writes .env, builds, starts)
docker-compose.yml        the stack; site settings in .env, source mounts in the override
server/
  dnsmasq/                proxyDHCP + TFTP (config rendered from the environment)
  nginx/                  static files: /iso (library), /cache, /files (TFTP root)
  menu/app.py             the menu service (stdlib Python, no framework)
ipxe/                     iPXE build: config, embedded script template, containerized build
writer/
  build.sh                Tiny Core + brandr -> writer boot sets (vmlinuz, core.gz, writer.gz)
  overlay/                files added to Tiny Core: the writer loop, tty1 autostart
  brandr/                 submodule: the brandr disk writer (a caligula fork)
test/                     QEMU helpers (see below)
```

## The menu service
`server/menu/app.py` is one stdlib-only Python file. nginx serves the bytes and proxies
everything else to it:

| Endpoint | |
|---|---|
| `/menu.ipxe?platform=pcbios\|efi&mac=..` | the boot menu, built from `library/` on every request |
| `/entry/<id>.ipxe?platform=..` | the boot script for one entry |
| `/status`, `/rescan`, `/reprepare?id=..` | plain-text overview; force a scan; redo one entry |
| `/images.json` | the image catalog brandr reads |
| `/variant/<id>/usb-hdd.img`, `usb-zip.img` | a floppy image as a USB-HDD/ZIP disk (built once) |
| `/variant/<id>/uefi.json`, `uefi.img` | a CD-only ISO as a FAT32 UEFI stick (built in the background) |
| `/writer.ipxe?platform=..` | boots the disk writer |

A background worker prepares each image once: `prepare()` lists the image (bsdtar, or 7z for
UDF), picks a recipe, extracts what it needs into `cache/<key>/` and writes `meta.json`.
`render_entry()` turns that into an iPXE script at boot time.

**Adding a recipe:** add a detection block to `prepare()` before the generic `sanboot` fallback.
Match on files in the image (`has`, `names`, `first()`), `extract()` what the kernel needs,
and set `recipe`, `files`, `args` and `platforms` in `meta`. Kernel/initrd recipes need no
changes in `render_entry()`. Bump `RECIPE_VERSION` if existing cache entries must be redone.

## Building the pieces by hand
```sh
ipxe/build.sh http://192.168.1.10:8069             # -> ipxe/out/
writer/brandr/scripts/build-static.sh              # brandr i586 + x86_64 -> writer/brandr/dist/
BRANDR_DIR=writer/brandr/dist writer/build.sh      # -> writer/out/<arch>/ (needs unsquashfs, cpio)
docker compose up -d --build
```

## Testing with QEMU
No spare hardware needed:
- `test/qemu-pxe.sh bios|efi` PXE-boots a VM in a window against your server. QEMU's built-in
  DHCP/TFTP hands out `ipxe/out/`, and everything after that is the real server.
- `SERVER=http://<ip>:<port> test/qemu.sh bios|efi [nic] [mem] [cpu]` boots the writer directly
  with an IDE disk, a USB stick, a floppy and optionally NVMe (`NVME=8G`). `nic` can be
  `rtl8139`, `ne2k_pci` or `ne2k_isa`. The BIOS mode uses QEMU's emulator (not KVM) with
  `-cpu pentium`, so any instruction a Pentium lacks traps. `USB=uhci` gives USB 1.1 speeds.
  `test/sendkeys.py` and `test/shot.sh` drive the VM through its monitor socket and take screenshots.
- `test/flaky_http.py <file> <port>` serves a file while dropping the connection, stalling
  and answering 503, to exercise brandr's retries.

## brandr
The writer lives in its own repository, [chibicitiberiu/brandr](https://github.com/chibicitiberiu/brandr),
a fork of [caligula](https://github.com/ifd3f/caligula). Work on it in `writer/brandr`, push
it there, and bump the submodule here. Tagging `v*` in brandr publishes static binaries, and
`setup.sh` uses the latest release when there is one (else it builds from the submodule).
