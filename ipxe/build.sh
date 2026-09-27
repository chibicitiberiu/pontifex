#!/bin/sh
# Build Pontifex's iPXE binaries with the menu URL compiled in, in a container.
#   usage: ipxe/build.sh <server url> [--install <PONTIFEX_DATA>]
#   e.g.   ipxe/build.sh http://192.168.1.10:8069 --install /srv/pontifex
# Output in ipxe/out/:
#   undionly.kpxe  BIOS PXE (uses the NIC's own PXE ROM)   ipxe.efi / snponly.efi  UEFI x64
#   ipxe.dsk / ipxe.iso / ipxe.lkrn  boot iPXE from a floppy/CD (PCI NIC drivers built in)
#   ne.dsk / 3c509.dsk               floppies for ISA NE2000 / 3Com 3c509 cards
# IPXE_REF picks the iPXE commit (default: master).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
URL=${1:?usage: build.sh <server url> [--install <data dir>]}
URL=${URL%/}
INSTALL=""
[ "${2:-}" = --install ] && INSTALL=${3:?--install needs a directory}
DOCKER=${DOCKER:-docker}
REF=${IPXE_REF:-master}
OUT=$HERE/out
mkdir -p "$OUT" "$HERE/.cache"
sed "s|@SERVER_URL@|$URL|g" "$HERE/embed.ipxe.in" > "$HERE/.cache/embed.ipxe"

# the toolchain container needs root to apt-get; the build itself runs as the caller
"$DOCKER" run --rm -e DEBIAN_FRONTEND=noninteractive -v "$HERE:/w" -e REF="$REF" -e UID_GID="$(id -u):$(id -g)" debian:stable sh -euc '
    apt-get update -qq >/dev/null
    apt-get install -y -qq --no-install-recommends git ca-certificates gcc make perl \
        liblzma-dev libc6-dev gcc-multilib mtools xorriso syslinux-common isolinux binutils >/dev/null
    git config --global --add safe.directory "*"   # the clone is owned by the caller, not root
    src=/w/.cache/ipxe
    [ -d "$src/.git" ] || git clone -q https://github.com/ipxe/ipxe.git "$src"
    git -C "$src" fetch -q origin && git -C "$src" checkout -q "$REF"
    [ "$REF" = master ] && git -C "$src" reset -q --hard origin/master
    cp /w/config-local/general.h "$src/src/config/local/general.h"
    cd "$src/src"
    make -s clean >/dev/null
    E=/w/.cache/embed.ipxe; J=$(nproc)
    make -s -j$J EMBED=$E bin/undionly.kpxe bin/ipxe.dsk bin/ipxe.iso bin/ipxe.lkrn bin/ne.dsk bin/3c509.dsk
    make -s -j$J EMBED=$E bin-x86_64-efi/ipxe.efi bin-x86_64-efi/snponly.efi
    cp bin/undionly.kpxe bin/ipxe.dsk bin/ipxe.iso bin/ipxe.lkrn bin/ne.dsk bin/3c509.dsk \
       bin-x86_64-efi/ipxe.efi bin-x86_64-efi/snponly.efi /w/out/
    git -C "$src" log -1 --format="%H %cd" > /w/out/VERSION
    chown -R "$UID_GID" /w/out /w/.cache
'
ls -l "$OUT"
if [ -n "$INSTALL" ]; then
    mkdir -p "$INSTALL/tftp"
    cp "$OUT"/* "$INSTALL/tftp/"
    echo "installed into $INSTALL/tftp"
fi
