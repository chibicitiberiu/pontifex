#!/bin/sh
# Build the Pontifex disk writer boot sets:
#   out/<arch>/vmlinuz, core.gz   Tiny Core's own kernel + base initrd (downloaded, unmodified)
#   out/<arch>/writer.gz          our overlay: brandr, cfdisk & co, autostart
# iPXE loads all three and the kernel merges the two initrds, so there's no custom ISO.
#
#   x86     -> Tiny Core 32-bit + i586 brandr   (BIOS machines, Pentium and up)
#   x86_64  -> CorePure64 + x86_64 brandr       (UEFI machines)
#
# brandr comes from, in order: $BRANDR_DIR/<i586|x86_64>/brandr if set; a GitHub release if
# $BRANDR_VERSION is set (e.g. v0.1.0); otherwise it's built from the brandr/ submodule
# (needs docker).
#
# usage: writer/build.sh [--install <PONTIFEX_DATA>] [x86] [x86_64]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
TC_VERSION=${TC_VERSION:-17.x}
TC_MIRROR=${TC_MIRROR:-http://repo.tinycorelinux.net}
BRANDR_REPO=${BRANDR_REPO:-chibicitiberiu/brandr}
EXTENSIONS="util-linux"      # cfdisk, fdisk, sfdisk, lsblk, wipefs (+ deps, resolved below)

INSTALL=""
if [ "${1:-}" = --install ]; then INSTALL=$2; shift 2; fi
ARCHES=${*:-x86 x86_64}
CACHE=$HERE/.cache
mkdir -p "$CACHE"

fetch() { # url dest
    [ -s "$2" ] || { echo "  fetch $1"; curl -fsSL -o "$2.part" "$1" && mv "$2.part" "$2"; }
}

brandr_bin() { # i586|x86_64 -> path of the static binary
    b=$1
    if [ -n "${BRANDR_DIR:-}" ]; then
        echo "$BRANDR_DIR/$b/brandr"
    elif [ -n "${BRANDR_VERSION:-}" ]; then
        name=brandr-$BRANDR_VERSION-$b-linux-musl
        d=$CACHE/brandr-release
        mkdir -p "$d"
        fetch "https://github.com/$BRANDR_REPO/releases/download/$BRANDR_VERSION/$name.tar.gz" "$d/$name.tar.gz"
        tar -C "$d" -xzf "$d/$name.tar.gz"
        echo "$d/$name/brandr"
    else
        [ -x "$HERE/brandr/dist/$b/brandr" ] || "$HERE/brandr/scripts/build-static.sh" "$b" >&2
        echo "$HERE/brandr/dist/$b/brandr"
    fi
}

for arch in $ARCHES; do
    case $arch in
        x86)    kernel=vmlinuz;   core=core.gz;       bin=i586 ;;
        x86_64) kernel=vmlinuz64; core=corepure64.gz; bin=x86_64 ;;
        *) echo "unknown arch $arch"; exit 1 ;;
    esac
    echo "== $arch"
    c=$CACHE/tc/$arch
    out=$HERE/out/$arch
    mkdir -p "$c/tcz" "$out"
    dist=$TC_MIRROR/$TC_VERSION/$arch/release/distribution_files
    tcz=$TC_MIRROR/$TC_VERSION/$arch/tcz

    fetch "$dist/$kernel" "$c/$kernel"
    fetch "$dist/$core" "$c/$core"

    # resolve extension dependencies (.dep files list one extension per line)
    todo=$EXTENSIONS; done_list=""
    while [ -n "$todo" ]; do
        set -- $todo; ext=$1; shift; todo="$*"
        case " $done_list " in *" $ext "*) continue ;; esac
        done_list="$done_list $ext"
        fetch "$tcz/$ext.tcz" "$c/tcz/$ext.tcz"
        if curl -fsSL -o "$c/tcz/$ext.tcz.dep" "$tcz/$ext.tcz.dep" 2>/dev/null; then
            for d in $(sed 's/\.tcz$//' "$c/tcz/$ext.tcz.dep"); do
                case "$d" in *KERNEL*) continue ;; esac   # kernel modules come with core.gz
                todo="$todo $d"
            done
        fi
    done
    echo "  extensions:$done_list"

    root=$c/overlay-root
    rm -rf "$root"; mkdir -p "$root"
    for ext in $done_list; do
        unsquashfs -q -f -d "$root" "$c/tcz/$ext.tcz" >/dev/null
    done
    rm -rf "$root/usr/local/tce.installed"   # install hooks are for tce-load, not needed here
    cp -a "$HERE/overlay/." "$root/"
    install -D -m 755 "$(brandr_bin $bin)" "$root/usr/local/bin/brandr"

    (cd "$root" && find . | cpio -o -H newc --owner=0:0 --quiet | gzip -9) > "$out/writer.gz"
    cp "$c/$kernel" "$out/vmlinuz"
    cp "$c/$core" "$out/core.gz"
    ls -l "$out"
    if [ -n "$INSTALL" ]; then
        mkdir -p "$INSTALL/tftp/brandr/$arch"
        cp "$out/vmlinuz" "$out/core.gz" "$out/writer.gz" "$INSTALL/tftp/brandr/$arch/"
        echo "  installed into $INSTALL/tftp/brandr/$arch"
    fi
done
