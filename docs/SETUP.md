# Setting up Pontifex

## Requirements
- **An x86_64 Linux server on the LAN** with Docker and the compose plugin (v2), plus `git`,
  `curl` and `iproute2`: a NAS, a mini PC, any always-on box. (ARM servers aren't supported
  yet: the iPXE and brandr builds assume an x86 host.)
- **Disk space:** a few GB for the Docker images and builds, plus the cache (see
  [IMAGES.md](IMAGES.md#the-cache)). Your ISOs themselves stay where they are.
- **The clients** must be on the same network segment as the server, because PXE discovery
  uses broadcasts. For other VLANs, see [DHCP](#dhcp).

## Install
```sh
git clone --recursive https://github.com/chibicitiberiu/pontifex.git
cd pontifex
./setup.sh
```
`setup.sh` asks for the settings below, detecting defaults from the default route, and
writes them to `.env`:

| Setting | Meaning |
|---|---|
| `PONTIFEX_IP` | the server's LAN address; it's compiled into the iPXE binaries |
| `PONTIFEX_IFACE` | the LAN interface dnsmasq listens on (only this one, never Docker bridges or VPNs) |
| `PONTIFEX_SUBNET` | the LAN's network address, e.g. `192.168.1.0` |
| `PONTIFEX_PORT` | HTTP port for menus and images, default `8069` (see [port 80](#old-installers-that-need-port-80)) |
| `PONTIFEX_DATA` | where Pontifex keeps `library/`, `cache/`, `tftp/` and `hosts.conf`, default `/srv/pontifex` |
| `PONTIFEX_SOURCES` | the folders your ISOs are in, space separated. They're mounted read-only at the same path in the containers, so absolute symlinks in the library resolve |

Then it builds iPXE with your server's URL (in a Debian container, a few minutes). It fetches
memdisk and wimboot, builds the disk writer boot files (Tiny Core Linux + brandr, from a brandr
release or from source), and starts the three containers: `dnsmasq`, `menu`, `nginx`.

It never runs `sudo`. If the data directory is somewhere you can't write, it tells you the
command to create it.

**Re-running:** `./setup.sh --yes` rebuilds and restarts with the existing `.env`, without
asking. Edit `.env` first to change something. Your library, cache and `hosts.conf` are never
touched.

## Firewall
PXE clients need to reach the server on UDP 67 (proxyDHCP), UDP 4011 (PXE), UDP 69 (TFTP) and
TCP `PONTIFEX_PORT`:
```sh
# firewalld
sudo firewall-cmd --permanent --add-service=dhcp --add-service=proxy-dhcp --add-service=tftp
sudo firewall-cmd --permanent --add-port=8069/tcp
sudo firewall-cmd --reload
# ufw
sudo ufw allow 67,69,4011/udp && sudo ufw allow 8069/tcp
```
`firewall-cmd` doesn't accept `--add-service` and `--add-port` in one command, hence two
lines. dnsmasq uses the host network, so these rules apply to it. The HTTP port is published by
Docker, which usually opens it on its own.

## DHCP
**Default: nothing to configure.** dnsmasq runs as a *proxyDHCP*. It never hands out addresses,
it only adds "boot iPXE from me" to the PXE client's DHCP exchange. It picks `undionly.kpxe`
for BIOS and `ipxe.efi` for UEFI. Your router keeps doing DHCP as before.

**Make sure your router's own PXE settings are empty** ("next server", "TFTP server", "boot
file name", option 66/67). If both answer, some firmware picks the router's answer.

**If proxyDHCP isn't possible** (clients on another VLAN, or a site policy), set the boot
options on your DHCP server instead: next-server = `PONTIFEX_IP`, and the boot file by client
architecture (DHCP option 93):

| Client | option 93 | boot file |
|---|---|---|
| BIOS | 0 | `undionly.kpxe` |
| UEFI x64 | 7 or 9 | `ipxe.efi` (or `snponly.efi` for picky firmware) |

Example for dnsmasq/OpenWrt:
```
dhcp-match=set:bios,option:client-arch,0
dhcp-boot=tag:bios,undionly.kpxe,,192.168.1.10
dhcp-boot=tag:!bios,ipxe.efi,,192.168.1.10
```
Some router UIs (e.g. OPNsense's Kea page) have only a single boot-file field. Use proxyDHCP
there, or a per-MAC reservation for the odd machine. For other VLANs, a DHCP relay
(ip-helper) that forwards to both the DHCP server and Pontifex also works.

## First boot
1. Link at least one image: `ln -s /srv/isos/memtest86plus.iso /srv/pontifex/library/tools/`
2. Check it was picked up: `http://<server>:<port>/status`
3. PXE boot a machine: pick "network boot" or "PXE" in its boot menu (often F12). The menu shows
   your sections, then *Write an image to a local disk (brandr)*, *netboot.xyz* (online
   installers) and the iPXE shell.
4. **UEFI:** Secure Boot must be off. Pontifex's iPXE and Tiny Core aren't signed, and the
   firmware refuses them with "Access Denied". Media the writer *creates* can still boot with
   Secure Boot on (e.g. a Windows stick).

**Preselecting per machine:** `hosts.conf` in the data dir maps a MAC address to an entry
(library path, entry id from `/status`, or `writer`) with a timeout:
```
00-11-22-33-44-55 tools/memtest86plus.iso 3
00-11-22-33-44-66 writer 3
```

## Retro machines
- **NIC with a PXE ROM** (many late-90s cards: Intel PRO/100, 3Com 905, RTL8139 with a ROM):
  pick network boot in the BIOS. They get `undionly.kpxe`, which drives the card through the
  ROM's own UNDI driver, so it works even where iPXE has no native driver.
- **No PXE ROM:** boot iPXE from a floppy. `setup.sh` builds them into `<data>/tftp/`:

  | File | For |
  |---|---|
  | `ipxe.dsk` | PCI cards iPXE has a driver for (RTL8139, NE2000-PCI, Intel, 3Com, ...) |
  | `ne.dsk` | ISA NE2000 and clones (probes 0x300, 0x280, 0x320, 0x340, 0x380, 0x220) |
  | `3c509.dsk` | ISA 3Com EtherLink III |

  Write one to a floppy with `dd if=ne.dsk of=/dev/fd0`, or from another machine with the
  disk writer. `ipxe.iso` and `ipxe.lkrn` are there for CD or bootloader use.
- **iPXE needs a 386** and a few MB of RAM. The disk writer needs a **Pentium** (i586); it
  was tested with 256 MB, and Tiny Core itself needs about 64 MB. What an installer needs is
  shown in the menu where it's large (live systems load into RAM).
- **ISA NE2000 in the writer:** the writer probes common ports. If yours is elsewhere, add
  `pontifex.ne=<io>[,<irq>]` (e.g. `pontifex.ne=0x300,10`) to its kernel line in the iPXE shell.

## Old installers that need port 80
Some 2000s-era installers fetch over HTTP but ignore any port you give them, e.g. Mandrake's
stage1. They only work if the server answers on port 80. Either set `PONTIFEX_PORT=80` (if
nothing else uses it), or forward requests for the bare IP from an existing reverse proxy:
```nginx
server {
    listen 80;
    server_name 192.168.1.10;          # only requests addressed to the IP itself
    location / { proxy_pass http://192.168.1.10:8069; proxy_buffering off; }
}
```

## Updating
```sh
git pull --recurse-submodules
./setup.sh --yes
```

## Where things are
```
<PONTIFEX_DATA>/
  library/       your symlinks: the menu (folders = sections)
  cache/         what the menu service extracted/built per image (safe to delete, rebuilt on demand)
  tftp/          iPXE binaries, memdisk, wimboot, brandr/<arch>/ (the disk writer)
  hosts.conf     per-machine preselect
```

## Troubleshooting
| Symptom | Check |
|---|---|
| PXE: "no offer" / PXE-E53 / straight to the next boot device | firewall (UDP 67/4011), router boot options empty, `PONTIFEX_IFACE` right. `docker compose logs dnsmasq` shows every PXE request it sees |
| TFTP timeout after the offer | UDP 69 in the firewall |
| "Could not load .../menu.ipxe", then the iPXE shell | HTTP port reachable? `curl http://<server>:<port>/status` from another machine. At the iPXE shell, `ifstat` and `route` show whether the NIC got an address |
| UEFI "Access Denied" | Secure Boot is on: turn it off |
| An entry shows `[preparing...]` | it's being extracted the first time. Wait a moment, or see `/status` |
| An entry shows `[ERROR]` | `/status` has the message; `docker compose logs menu` the details |
| Installer starts, then can't find its media | the image fell back to generic CD emulation (`[unknown]` in the menu). See [IMAGES.md](IMAGES.md#limits) |
| Live system stops with "no space left" | not enough RAM: the menu shows how much big images need |
