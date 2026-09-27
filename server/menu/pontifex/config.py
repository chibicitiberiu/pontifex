"""Settings (from the environment, see docker-compose.yml) and shared constants."""

import os

LIBRARY = os.environ.get("PXE_LIBRARY", "/srv/pontifex/library")
CACHE = os.environ.get("PXE_CACHE", "/srv/pontifex/cache")
HOSTS_FILE = os.environ.get("PXE_HOSTS", "/srv/pontifex/hosts.conf")
WRITER_DIR = os.environ.get("PXE_WRITER", "/srv/pontifex/tftp/brandr")
BASE_URL = os.environ.get("PXE_BASE_URL", "http://pontifex.invalid").rstrip("/")  # e.g. http://192.168.1.10:8069
LISTEN_PORT = int(os.environ.get("PXE_LISTEN_PORT", "8000"))
SCAN_INTERVAL = int(os.environ.get("PXE_SCAN_INTERVAL", "60"))
MENU_TIMEOUT = int(os.environ.get("PXE_MENU_TIMEOUT", "0"))  # seconds, 0 = wait forever

# Bump to force re-detection of every cached entry (part of each cache key)
RECIPE_VERSION = 5

ISO_EXT = (".iso",)
DISK_EXT = (".img", ".ima", ".vfd", ".flp", ".dsk")
FLOPPY_MAX = 2949120  # 2.88MB; larger raw images are booted by memdisk as hard disks
FLOPPY_SIZES = {163840, 184320, 327680, 368640, 737280, 1228800, 1474560, 1720320, 1763328, 2949120}

# Menu sections shown first, in this order; anything else follows alphabetically
SECTION_ORDER = ["linux", "windows", "tools", "retro"]
