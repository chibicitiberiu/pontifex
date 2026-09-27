"""The brandr disk writer entry: Tiny Core + brandr, built by writer/build.sh into
tftp/brandr/<arch>/ (vmlinuz and core.gz are Tiny Core's own, writer.gz is our overlay)."""

import os

from . import config

WRITER_ARCH = {"pcbios": "x86", "efi": "x86_64"}
WRITER_FILES = ("vmlinuz", "core.gz", "writer.gz")


def available(platform):
    d = os.path.join(config.WRITER_DIR, WRITER_ARCH[platform])
    return all(os.path.isfile(os.path.join(d, f)) for f in WRITER_FILES)


def render(platform):
    if not available(platform):
        return "#!ipxe\necho The disk writer is not installed for this platform\nprompt\nexit 1\n"
    base = f"{config.BASE_URL}/files/brandr/{WRITER_ARCH[platform]}"
    # Tiny Core boot codes: superuser = root autologin on tty1 (the writer takes it over),
    # base/norestore = no extensions or backups from local disks, noswap = never touch them
    args = (f"initrd=core.gz initrd=writer.gz quiet superuser base norestore noswap nozswap "
            f"pontifex.server={config.BASE_URL}")
    return "\n".join(["#!ipxe", "echo Booting the disk writer (Tiny Core + brandr)...",
                      f"kernel {base}/vmlinuz {args}",
                      f"initrd -n core.gz {base}/core.gz",
                      f"initrd -n writer.gz {base}/writer.gz",
                      "boot", ""])
