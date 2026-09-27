#!/bin/sh
# PXE-boot a throwaway VM against your Pontifex server, in a window, to try the menu
# without spare hardware. QEMU's own DHCP/TFTP hands the VM Pontifex's iPXE (ipxe/out),
# which then chains the menu from the URL compiled into it, so the VM needs no LAN bridge.
#   test/qemu-pxe.sh bios        (SeaBIOS)
#   test/qemu-pxe.sh efi         (OVMF, Secure Boot off)
# MEM=4096 for big live ISOs; DISK=path to attach a disk to install onto.
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
MODE=${1:-bios}
TFTP=$HERE/ipxe/out
[ -f "$TFTP/undionly.kpxe" ] || { echo "build iPXE first: ipxe/build.sh http://<ip>:<port>"; exit 1; }
set --
if [ "$MODE" = efi ]; then
    code=$(ls /usr/share/edk2/ovmf/OVMF_CODE.fd /usr/share/OVMF/OVMF_CODE.fd /usr/share/ovmf/OVMF.fd 2>/dev/null | head -1)
    vars=$(ls /usr/share/edk2/ovmf/OVMF_VARS.fd /usr/share/OVMF/OVMF_VARS.fd 2>/dev/null | head -1)
    [ -n "$code" ] || { echo "OVMF not found (install edk2-ovmf / ovmf)"; exit 1; }
    cp "$vars" "${TMPDIR:-/tmp}/pontifex-vars.fd"
    set -- -M q35 -drive if=pflash,format=raw,readonly=on,file="$code" \
           -drive if=pflash,format=raw,file="${TMPDIR:-/tmp}/pontifex-vars.fd"
    boot=ipxe.efi
else
    boot=undionly.kpxe
fi
[ -n "${DISK:-}" ] && set -- "$@" -drive file="$DISK",format=raw
exec qemu-system-x86_64 -accel kvm -cpu host -m "${MEM:-2048}" "$@" \
    -netdev user,id=n0,tftp="$TFTP",bootfile=$boot -device e1000,netdev=n0,bootindex=0
