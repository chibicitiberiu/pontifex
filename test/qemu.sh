#!/bin/bash
# Boot the disk writer (writer/out/<arch>) in QEMU with test media attached, without PXE.
#   test/qemu.sh <bios|efi> [nic] [mem MB] [cpu]
#   nic: rtl8139 (default) | ne2k_pci | ne2k_isa
# Media (created empty in .cache/test/ if missing): IDE disk, USB stick, floppy.
# Boots writer/out/<arch> directly (-kernel/-initrd) with user-mode networking, which
# reaches your real Pontifex server: SERVER=http://<ip>:<port> test/qemu.sh bios
# Monitor socket: $MON (default $XDG_RUNTIME_DIR/pontifex-mon) for screendump/sendkey;
# DISPLAY_OPT=-display gtk to watch it in a window.
# shellcheck disable=SC2054,SC2046  # commas are QEMU option syntax; the NVMe option is split on purpose
set -eu
HERE=$(cd "$(dirname "$0")/.." && pwd)
MODE=${1:-bios} NIC=${2:-rtl8139} MEM=${3:-256} CPU=${4:-pentium}
SERVER=${SERVER:?set SERVER=http://<pontifex ip>:<port>}
T=$HERE/.cache/test; mkdir -p "$T"
[ -f "$T/ide.img" ] || truncate -s 2G "$T/ide.img"
[ -f "$T/usb.img" ] || truncate -s 2G "$T/usb.img"
[ -f "$T/floppy.img" ] || truncate -s 1474560 "$T/floppy.img"
[ -z "${NVME:-}" ] || [ -f "$T/nvme.img" ] || truncate -s "$NVME" "$T/nvme.img"   # NVME=8G to add one
MON=${MON:-${XDG_RUNTIME_DIR:-/tmp}/pontifex-mon}

if [ "$MODE" = efi ]; then
    arch=x86_64; CPU=${4:-qemu64}
    cp /usr/share/edk2/ovmf/OVMF_VARS.fd "$T/vars.fd"
    FW=(-drive if=pflash,format=raw,readonly=on,file=/usr/share/edk2/ovmf/OVMF_CODE.fd
        -drive if=pflash,format=raw,file="$T/vars.fd")
else
    arch=x86; FW=()
fi
cat "$HERE/writer/out/$arch/core.gz" "$HERE/writer/out/$arch/writer.gz" > "$T/initrd-$arch.gz"

# USB=uhci for a USB 1.1-only machine (~1MB/s), default USB 2.0 (EHCI)
case ${USB:-ehci} in
    uhci) USBDEV=(-device piix3-usb-uhci,id=usb) ;;
    *)    USBDEV=(-device usb-ehci,id=usb) ;;
esac

case $NIC in
    ne2k_isa) NET=(-netdev user,id=n0 -device ne2k_isa,netdev=n0,iobase=0x300,irq=10) ;;
    *)        NET=(-netdev user,id=n0 -device "$NIC",netdev=n0) ;;
esac

# KVM would run SSE etc. natively whatever -cpu says; the BIOS test uses TCG so an
# instruction a Pentium lacks really traps. ACCEL=kvm to go fast anyway.
ACCEL=${ACCEL:-$([ "$MODE" = efi ] && echo kvm || echo tcg)}
[ "$ACCEL" = kvm ] && CPU=${CPU/qemu64/host}
exec qemu-system-x86_64 -accel "$ACCEL" -cpu "$CPU" -m "$MEM" "${FW[@]}" \
    -kernel "$HERE/writer/out/$arch/vmlinuz" -initrd "$T/initrd-$arch.gz" \
    -append "quiet superuser base norestore noswap nozswap pontifex.server=$SERVER ${EXTRA_ARGS:-}" \
    "${NET[@]}" \
    -drive file="$T/ide.img",if=ide,format=raw,index=0 \
    -drive file="$T/floppy.img",if=floppy,format=raw \
    "${USBDEV[@]}" -drive id=usbstick,if=none,file="$T/usb.img",format=raw \
    -device usb-storage,drive=usbstick,bus=usb.0 \
    $([ -n "${NVME:-}" ] && echo "-drive id=nvme0,if=none,file=$T/nvme.img,format=raw -device nvme,drive=nvme0,serial=pontifex") \
    ${DISPLAY_OPT:--display none} -vga std -monitor unix:"$MON",server,nowait
