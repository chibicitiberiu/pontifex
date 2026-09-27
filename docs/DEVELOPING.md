# Developing Pontifex

## Repository layout
```
setup.sh                  install/upgrade on a server (writes .env, builds, starts)
docker-compose.yml        the stack; site settings in .env, source mounts in the override
server/
  dnsmasq/                proxyDHCP + TFTP (config rendered from the environment)
  nginx/                  static files: /iso (library), /cache, /files (TFTP root)
  menu/pontifex/          the menu service (stdlib Python package, no framework)
  menu/tests/             its unit tests
ipxe/                     iPXE build: config, embedded script template, containerized build
writer/
  build.sh                Tiny Core + brandr -> writer boot sets (vmlinuz, core.gz, writer.gz)
  overlay/                files added to Tiny Core: the writer loop, tty1 autostart
  brandr/                 submodule: the brandr disk writer (a caligula fork)
test/                     QEMU helpers (see below)
```

## The menu service
`server/menu/pontifex/` is a stdlib-only Python package (run with `python3 -m pontifex`).
nginx serves the bytes and proxies everything else to it.

| Module | |
|---|---|
| `config.py` | settings from the environment, shared constants |
| `library.py` | scanning the library: `Entry`, sidecars, sets, sections |
| `image.py` | reading images: `IsoListing` (list/extract/read), El Torito, disk kind |
| `recipes/` | one module per family (`debian`, `redhat`, `arch`, `windows`, `mandrake`, `generic`) plus the registry in `__init__.py` |
| `worker.py` | background preparation, rescan, reprepare |
| `menu.py` | the iPXE menu and entry scripts, `/status` |
| `catalog.py`, `variants/` | `/images.json`, and the layouts built for the disk writer (floppy USB-HDD/ZIP, UEFI sticks) |
| `writer.py` | the disk writer's boot script |
| `http.py` | routes and file serving (the route table lists every endpoint) |

A background worker prepares each image once: `recipes.prepare()` lists the image (bsdtar,
or 7z for UDF), picks the first recipe whose `detect()` matches, and its `prepare()` extracts
what it needs into `cache/<key>/` and fills `meta.json`. At boot, `render()` turns that into
iPXE lines.

**Adding a recipe:** subclass `Recipe` (`recipes/base.py`):
```python
class Knoppix(Recipe):
    name = "knoppix"
    def detect(self, ctx):                       # cheap: look at the file list
        return ctx.iso.has("knoppix/knoppix")
    def prepare(self, ctx):                      # extract, fill ctx.meta; False = let others try
        ctx.iso.extract(["boot/isolinux/linux", "boot/isolinux/minirt.gz"], ctx.dest)
        ctx.meta.update(recipe=self.name, files={...}, args="...", notes="...")
        return True
```
Kernel+initrd recipes inherit `render()`. List the class in `ISO_RECIPES` before any family
it must win against, add its file list to `tests/test_recipes.py`, and bump `RECIPE_VERSION`
if existing cache entries must be redone.

**Tests:** `cd server/menu && python3 -m unittest discover -s tests -t .` (CI runs them).

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
