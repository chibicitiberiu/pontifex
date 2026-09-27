"""iPXE scripts (the menu and per-entry boot scripts) and the /status page."""

import os
import re

from . import config, library, recipes, worker, writer
from .util import ipxe_text


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


def menu_item(e, meta):
    """The menu line for one entry, and whether it's bootable."""
    label = ipxe_text(e.label)
    if e.error:
        return f"item --gap --    {label}  [BROKEN: {ipxe_text(e.error)}]", False
    if meta is None:
        return f"item --gap --    {label}  [preparing...]", False
    if meta.get("state") == "error":
        return f"item --gap --    {label}  [ERROR, see /status]", False
    hint = f"  ({meta['label_hint']})" if meta.get("label_hint") else ""
    # no recipe recognized the image: it gets generic CD emulation, which may not get far
    unknown = ""
    if meta.get("unsupported"):
        unknown = f"  [{meta['unsupported']}]"
    elif meta.get("recipe") == "sanboot":
        unknown = "  [unknown]"
    return f"item e{e.id}   {label}{hint}{unknown}", True


def render_menu(platform, mac):
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
        line, bootable = menu_item(e, meta)
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
                f"chain ${{pxebase}}/entry/{e.id}.ipxe?platform=${{platform}} || goto failed",
                "goto start"]
    out += [":writer", "chain ${pxebase}/writer.ipxe?platform=${platform} || goto failed", "goto start",
            ":netbootxyz", "chain --autofree https://boot.netboot.xyz || goto failed", "goto start",
            ":shell", "echo Type 'exit' to get back to the menu", "shell", "goto start",
            ":reboot", "reboot",
            ":failed", "echo", "echo Boot failed (${errno}). Press any key for the menu.", "prompt", "goto start",
            ":exit", "exit", ""]
    return "\n".join(out)


def render_entry(entry, platform):
    meta = entry.meta()
    if not meta or meta.get("state") != "ready":
        return "#!ipxe\necho This entry is not ready yet\nprompt\nexit 1\n"
    lines = ["#!ipxe", f"echo Booting {ipxe_text(entry.label)} ({meta['recipe']})"]
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
