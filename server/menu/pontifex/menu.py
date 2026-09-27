"""iPXE scripts (the menu and per-entry boot scripts) and the /status page."""

import os
import re

from . import config, library, recipes, worker, writer
from .recipes.base import ram_text
from .util import ipxe_text

LEGACY_HINT = re.compile(r"(?:BIOS: )?RAM (\d+(?:\.\d+)?)GB\+")


def ram_need(meta, platform):
    """MB of RAM an entry needs on this platform, or None. Caches prepared before ram_mb
    existed only have the menu hint, so fall back to reading that."""
    if "ram_mb" in meta:
        applies = meta.get("ram_platform") in (None, platform)
        return meta["ram_mb"] if applies else None
    m = LEGACY_HINT.fullmatch(meta.get("label_hint", ""))
    if not m or (meta["label_hint"].startswith("BIOS") and platform != "pcbios"):
        return None
    return int(float(m.group(1)) * 1024)


def too_big(need, mem):
    """True if an entry needing `need` MB won't fit in `mem` MB. The needs are estimates
    rounded up, and a machine reports a bit less than its installed RAM: allow some slack."""
    return bool(mem and need and need > mem * 1.03 + 64)


def parse_mem(value):
    """iPXE's ${memsize} (MB; empty where iPXE can't tell, e.g. on UEFI)."""
    try:
        return int(value) or None
    except (TypeError, ValueError):
        return None


def load_hosts():
    """hosts.conf: '<mac> <library path, entry id or "writer"> [timeout seconds]' ->
    preselect an entry for a machine. The path may contain spaces; # starts a comment line."""
    hosts = {}
    try:
        with open(config.HOSTS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                target, timeout = parts[1], 5
                m = re.fullmatch(r"(.*?)\s+(\d+)", target)
                if m:
                    target, timeout = m.group(1), int(m.group(2))
                hosts[parts[0].lower().replace(":", "-")] = (target, timeout)
    except OSError:
        pass
    return hosts


def menu_item(e, meta, platform="pcbios", mem=None):
    """The menu line for one entry, and whether it's bootable."""
    label = ipxe_text(e.label)
    if e.error:
        return f"item --gap --    {label}  [BROKEN: {ipxe_text(e.error)}]", False
    if meta is None:
        return f"item --gap --    {label}  [preparing...]", False
    if meta.get("state") == "error":
        return f"item --gap --    {label}  [ERROR, see /status]", False
    hint = f"  ({meta['label_hint']})" if meta.get("label_hint") else ""
    need = ram_need(meta, platform)
    if too_big(need, mem):
        hint = f"  (needs {ram_text(need)} RAM, has {ram_text(mem)})"
    # no recipe recognized the image: it gets generic CD emulation, which may not get far
    unknown = ""
    if meta.get("unsupported"):
        unknown = f"  [{meta['unsupported']}]"
    elif meta.get("recipe") == "sanboot":
        unknown = "  [unknown]"
    return f"item e{e.id}   {label}{hint}{unknown}", True


def render_menu(platform, mac, mem=None):
    entries = [e for e in library.scan() if not e.hidden]
    worker.enqueue_all(entries)
    host = load_hosts().get((mac or "").lower())

    out = ["#!ipxe", f"set pxebase {config.BASE_URL}", ":start",
           f"menu Pontifex network boot  [{platform}]"]
    targets = []
    default, timeout = "exit", config.MENU_TIMEOUT
    current = None
    for e in sorted(entries, key=lambda e: (library.section_key(e.section), e.subsection, e.label.lower())):
        meta = e.meta()
        if meta and meta.get("state") == "ready" and platform not in meta.get("platforms", []):
            continue  # e.g. memdisk floppies on UEFI
        if e.heading != current:
            out.append(f"item --gap --  ---- {ipxe_text(e.heading)} ----")
            current = e.heading
        line, bootable = menu_item(e, meta, platform, mem)
        out.append(line)
        if bootable:
            targets.append(e)
            if host and host[0] in (e.rel, e.id):
                default, timeout = f"e{e.id}", host[1]

    has_writer = writer.available(platform)
    out += ["item --gap --  ---- other ----"]
    if has_writer:
        out += ["item writer       Write an image to a local disk (brandr: USB/IDE/CF/floppy)"]
    out += ["item netbootxyz   netboot.xyz online installers (internet)",
            "item shell        iPXE shell",
            "item reboot       Reboot",
            "item exit         Exit (continue with the next boot device)"]
    if host and host[0] == "writer" and has_writer:
        default, timeout = "writer", host[1]
    tmo = f" --timeout {timeout * 1000}" if timeout else ""
    out += [f"choose --default {default}{tmo} target && goto ${{target}} || goto exit", ""]
    for e in targets:
        out += [f":e{e.id}",
                f"chain ${{pxebase}}/entry/{e.id}.ipxe?platform=${{platform}}&mem=${{memsize}} || goto failed",
                "goto start"]
    out += [":writer", "chain ${pxebase}/writer.ipxe?platform=${platform} || goto failed", "goto start",
            ":netbootxyz", "chain --autofree https://boot.netboot.xyz || goto failed", "goto start",
            ":shell", "echo Type 'exit' to get back to the menu", "shell", "goto start",
            ":reboot", "reboot",
            ":failed", "echo", "echo Boot failed (${errno}). Press any key for the menu.", "prompt", "goto start",
            ":exit", "exit", ""]
    return "\n".join(out)


def render_entry(entry, platform, mem=None):
    meta = entry.meta()
    if not meta or meta.get("state") != "ready":
        return "#!ipxe\necho This entry is not ready yet\nprompt\nexit 1\n"
    lines = ["#!ipxe"]
    need = ram_need(meta, platform)
    if too_big(need, mem):
        # loading it anyway would end in a kernel panic halfway through the boot
        lines += [f"echo {ipxe_text(entry.label)} needs about {ram_text(need)} of RAM,",
                  f"echo but this machine reports {ram_text(mem)}.",
                  "prompt --key y Press y to try anyway, any other key for the menu && goto go || exit 0",
                  ":go"]
    lines += [f"echo Booting {ipxe_text(entry.label)} ({meta['recipe']})"]
    if meta.get("notes"):
        lines.append(f"echo Note: {ipxe_text(meta['notes'])}")
    lines += recipes.render(entry, meta, platform, entry.side.get("args", ""))
    return "\n".join(lines) + "\n"


def render_status():
    entries = library.scan()
    rows = [f"Pontifex  library={config.LIBRARY}  base={config.BASE_URL}", ""]
    busy, queued = worker.busy_and_queued()
    for e in entries:
        meta = e.meta() or {}
        state = ("BROKEN: " + e.error) if e.error else ("preparing" if e.key in busy else meta.get("state", "queued"))
        if e.set_head:
            state = f"in-set-of:{e.set_head.id}"
        rows.append(f"{e.id}  {e.rel}")
        rows.append(f"      -> {e.real}  ({e.size / 2**20:.0f} MB)")
        rows.append(f"      state={state}  recipe={meta.get('recipe')}  platforms={','.join(meta.get('platforms', []))}")
        for key, label in (("error", "error: "), ("notes", "notes: "), ("args", "args:  ")):
            if meta.get(key):
                rows.append(f"      {label}{meta[key]}")
    rows += ["", f"worker: {len(busy)} busy, {queued} queued"]
    known = {e.key for e in entries if e.key}
    try:
        orphans = [d for d in os.listdir(config.CACHE) if d not in known and not d.startswith(".")]
    except OSError:
        orphans = []
    if orphans:
        rows += [f"orphaned cache dirs (safe to remove after a backup): {' '.join(sorted(orphans))}"]
    return "\n".join(rows) + "\n"
