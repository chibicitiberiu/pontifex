#!/bin/sh
# Screenshot the VM: test/shot.sh <out.png> [monitor-socket]
MON=${2:-${XDG_RUNTIME_DIR:-/tmp}/pontifex-mon}
printf 'screendump %s.ppm\n' "$1" | socat - UNIX-CONNECT:"$MON" >/dev/null
sleep 1; magick "$1.ppm" "$1" && rm -f "$1.ppm"
