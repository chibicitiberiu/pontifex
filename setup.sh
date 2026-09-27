#!/bin/sh
# Pontifex setup: run on the server, from the repository root.
#
#   ./setup.sh           ask for the settings (defaults detected), then build and start
#   ./setup.sh --yes     reuse an existing .env without asking (e.g. after git pull)
#
# It writes .env and docker-compose.override.yml, builds iPXE with your server's URL,
# fetches memdisk and wimboot, builds the disk writer boot sets, and starts the stack.
# It never runs sudo: things that need root (firewall, creating the data dir in a
# root-owned place) are printed for you to run.
set -eu
cd "$(dirname "$0")"
ROOT=$(pwd)
ASK=1
[ "${1:-}" = --yes ] && ASK=0

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }
# shellcheck disable=SC2034  # "ans" is read through eval below
ask()  { # var prompt default
    if [ "$ASK" = 1 ]; then
        printf '%s [%s]: ' "$2" "$3"; read -r ans
        eval "$1=\${ans:-\$3}"
    else
        eval "$1=\$3"
    fi
}

# ---------------------------------------------------------------------------------------
say "Checking requirements"
for c in docker curl ip; do command -v $c >/dev/null || die "$c is required"; done
docker compose version >/dev/null 2>&1 || die "docker compose (v2) is required"
docker info >/dev/null 2>&1 || die "can't talk to docker: is it running, and are you in the docker group?"
echo "ok"

# ---------------------------------------------------------------------------------------
say "Settings"
[ -f .env ] && . ./.env
route=$(ip -4 route get 1.1.1.1 2>/dev/null || true)
det_iface=$(echo "$route" | sed -n 's/.* dev \([^ ]*\).*/\1/p')
det_ip=$(echo "$route" | sed -n 's/.* src \([^ ]*\).*/\1/p')
ask PONTIFEX_IP    "Server LAN IP"               "${PONTIFEX_IP:-$det_ip}"
ask PONTIFEX_IFACE "LAN interface (proxyDHCP)"   "${PONTIFEX_IFACE:-$det_iface}"
cidr=$(ip -o -4 addr show dev "$PONTIFEX_IFACE" 2>/dev/null | awk '{print $4}' | head -1)
det_subnet=""
if [ -n "$cidr" ] && command -v python3 >/dev/null; then
    det_subnet=$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network.network_address)" "$cidr")
fi
ask PONTIFEX_SUBNET "LAN network address"        "${PONTIFEX_SUBNET:-$det_subnet}"
ask PONTIFEX_PORT  "HTTP port"                   "${PONTIFEX_PORT:-8069}"
ask PONTIFEX_DATA  "Data directory"              "${PONTIFEX_DATA:-/srv/pontifex}"
ask PONTIFEX_SOURCES "Folders your ISOs live in (space separated)" "${PONTIFEX_SOURCES:-/srv/isos}"
PONTIFEX_UID=${PONTIFEX_UID:-$(id -u)}
PONTIFEX_GID=${PONTIFEX_GID:-$(id -g)}
for v in PONTIFEX_IP PONTIFEX_IFACE PONTIFEX_SUBNET PONTIFEX_PORT PONTIFEX_DATA; do
    eval "[ -n \"\${$v}\" ]" || die "$v is empty"
done
URL=http://$PONTIFEX_IP:$PONTIFEX_PORT

if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PONTIFEX_PORT\$" \
        && ! docker compose ps --format '{{.Service}}' 2>/dev/null | grep -q nginx; then
    die "port $PONTIFEX_PORT is already in use on this machine; pick another"
fi

cat > .env <<EOF
# Written by setup.sh $(date +%F). Re-run ./setup.sh to change, or edit and run ./setup.sh --yes
PONTIFEX_IP=$PONTIFEX_IP
PONTIFEX_PORT=$PONTIFEX_PORT
PONTIFEX_IFACE=$PONTIFEX_IFACE
PONTIFEX_SUBNET=$PONTIFEX_SUBNET
PONTIFEX_DATA=$PONTIFEX_DATA
PONTIFEX_SOURCES="$PONTIFEX_SOURCES"
PONTIFEX_UID=$PONTIFEX_UID
PONTIFEX_GID=$PONTIFEX_GID
EOF
{
    echo "# Written by setup.sh from PONTIFEX_SOURCES: the folders library links point into,"
    echo "# mounted read-only at the same path so absolute symlinks resolve."
    echo "services:"
    for svc in menu nginx; do
        echo "  $svc:"
        echo "    volumes:"
        for src in $PONTIFEX_SOURCES; do echo "      - $src:$src:ro"; done
    done
} > docker-compose.override.yml
echo "wrote .env and docker-compose.override.yml (server URL: $URL)"

# ---------------------------------------------------------------------------------------
say "Data directory"
if ! mkdir -p "$PONTIFEX_DATA/library" "$PONTIFEX_DATA/cache" "$PONTIFEX_DATA/tftp" 2>/dev/null; then
    die "can't create $PONTIFEX_DATA. Run: sudo mkdir -p $PONTIFEX_DATA && sudo chown $(id -un): $PONTIFEX_DATA"
fi
[ -f "$PONTIFEX_DATA/hosts.conf" ] || cat > "$PONTIFEX_DATA/hosts.conf" <<'EOF'
# Per-machine preselect: <mac with - or :> <library path, entry id or "writer"> [timeout seconds]
# e.g.  00-11-22-33-44-55 tools/memtest86plus.iso 3
#       00-11-22-33-44-66 writer 3
EOF
for s in linux windows tools retro; do mkdir -p "$PONTIFEX_DATA/library/$s"; done
echo "$PONTIFEX_DATA ready (put symlinks to your ISOs in $PONTIFEX_DATA/library/<section>/)"

# ---------------------------------------------------------------------------------------
say "Building iPXE for $URL (in a container, a few minutes)"
ipxe/build.sh "$URL" --install "$PONTIFEX_DATA" >/dev/null
ls "$PONTIFEX_DATA/tftp" | tr '\n' ' '; echo

say "Fetching memdisk and wimboot"
docker run --rm -e DEBIAN_FRONTEND=noninteractive -v "$PONTIFEX_DATA/tftp:/out" debian:stable sh -euc '
    apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends syslinux-common >/dev/null
    cp /usr/lib/syslinux/memdisk /out/memdisk && chown '"$(id -u):$(id -g)"' /out/memdisk'
curl -fsSL -o "$PONTIFEX_DATA/tftp/wimboot" https://github.com/ipxe/wimboot/releases/latest/download/wimboot
echo "ok"

# ---------------------------------------------------------------------------------------
say "Building the disk writer (Tiny Core + brandr)"
tag=$(curl -fsSL https://api.github.com/repos/chibicitiberiu/brandr/releases/latest 2>/dev/null \
      | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
brandr_env=""
if [ -n "${BRANDR_VERSION:-$tag}" ]; then
    echo "using brandr release ${BRANDR_VERSION:-$tag}"
    brandr_env="-e BRANDR_VERSION=${BRANDR_VERSION:-$tag}"
else
    echo "no brandr release found: building it from the submodule"
    [ -x writer/brandr/scripts/build-static.sh ] || git submodule update --init writer/brandr
    writer/brandr/scripts/build-static.sh >/dev/null
    brandr_env="-e BRANDR_DIR=/w/writer/brandr/dist"
fi
# shellcheck disable=SC2086
docker run --rm -e DEBIAN_FRONTEND=noninteractive -v "$ROOT:/w" -v "$PONTIFEX_DATA:/data" $brandr_env debian:stable sh -euc '
    apt-get update -qq >/dev/null
    apt-get install -y -qq --no-install-recommends curl ca-certificates squashfs-tools cpio gzip >/dev/null
    /w/writer/build.sh --install /data >/dev/null
    chown -R '"$(id -u):$(id -g)"' /w/writer/out /w/writer/.cache /data/tftp/brandr'
ls -l "$PONTIFEX_DATA/tftp/brandr"/*/writer.gz

# ---------------------------------------------------------------------------------------
say "Starting the stack"
docker compose up -d --build
sleep 3
curl -fsS "$URL/status" >/dev/null && echo "menu service answers at $URL/status" \
    || echo "warning: $URL/status didn't answer yet; check: docker compose logs"

# ---------------------------------------------------------------------------------------
say "Almost done. Things only you can do"
cat <<EOF
1. Firewall: let PXE clients reach this machine (UDP 67, 69, 4011 and TCP $PONTIFEX_PORT):
     firewalld:  sudo firewall-cmd --permanent --add-service=dhcp --add-service=proxy-dhcp --add-service=tftp
                 sudo firewall-cmd --permanent --add-port=$PONTIFEX_PORT/tcp && sudo firewall-cmd --reload
     ufw:        sudo ufw allow 67,69,4011/udp && sudo ufw allow $PONTIFEX_PORT/tcp
2. DHCP: nothing to change. Pontifex answers PXE clients itself (proxyDHCP) and your router
   keeps handing out addresses. Just make sure the router's own PXE / "next server" /
   "boot file" settings are EMPTY.
3. Add images: ln -s /path/to/some.iso $PONTIFEX_DATA/library/linux/
   Watch them get detected at $URL/status
4. PXE boot a machine (Secure Boot off on UEFI). See docs/SETUP.md for retro machines.
EOF
