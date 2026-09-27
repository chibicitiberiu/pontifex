"""Pontifex menu service: turns a folder of (symlinked) boot images into iPXE menus.

The HTTP routes are listed in pontifex.http. nginx serves the bytes: /iso/ (the library),
/cache/ (extracted files), /files/ (memdisk, wimboot, iPXE). Stdlib only; bsdtar and 7z
must be on PATH, plus syslinux/mtools/dosfstools/wimlib for the variant builders.
"""
