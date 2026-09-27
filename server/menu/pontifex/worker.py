"""Background preparation: each image is detected and extracted once, then cached."""

import json
import os
import queue
import threading
import time
import traceback

from . import config, library, recipes
from .util import log

_work = queue.Queue()
_queued = set()
_busy = {}
lock = threading.Lock()   # guards the queue state (and the UEFI build set in variants)


def enqueue(entry):
    """Queue an entry for preparation unless it's prepared, queued or a set member."""
    if not entry.key or entry.set_head or entry.meta() is not None:
        return
    with lock:
        if entry.key in _queued:
            return
        _queued.add(entry.key)
    _work.put(entry)


def enqueue_all(entries):
    for e in entries:
        enqueue(e)


def busy_and_queued():
    with lock:
        return dict(_busy), len(_queued)


def write_meta(entry, meta):
    os.makedirs(entry.cache_dir, exist_ok=True)
    tmp = os.path.join(entry.cache_dir, "meta.json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, os.path.join(entry.cache_dir, "meta.json"))


def prepare_one(entry):
    """Prepare an entry and write its meta.json (errors are recorded, not raised)."""
    started = time.time()
    log("preparing", entry.rel)
    try:
        meta = recipes.prepare(entry)
        meta["state"] = "ready"
    except Exception as e:  # keep going; the error shows up in the menu and /status
        traceback.print_exc()
        meta = {"rel": entry.rel, "state": "error", "error": str(e)[:500]}
    meta["prepared_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    meta["seconds"] = round(time.time() - started, 1)
    write_meta(entry, meta)
    log("done", entry.rel, meta.get("recipe") or meta.get("error"), f"{meta['seconds']}s")
    return meta


def worker():
    while True:
        entry = _work.get()
        with lock:
            _busy[entry.key] = entry.rel
        prepare_one(entry)
        with lock:
            _busy.pop(entry.key, None)
            _queued.discard(entry.key)


def scanner():
    while True:
        try:
            enqueue_all(library.scan())
        except Exception:
            traceback.print_exc()
        time.sleep(config.SCAN_INTERVAL)


def reprepare(ids):
    """Move entries' cache dirs to cache/.old/ (never deleted here) and prepare again."""
    done = []
    for e in library.scan():
        if e.id in ids and e.key and os.path.isdir(e.cache_dir):
            os.makedirs(os.path.join(config.CACHE, ".old"), exist_ok=True)
            os.rename(e.cache_dir, os.path.join(config.CACHE, ".old", f"{e.key}-{time.strftime('%Y%m%d-%H%M%S')}"))
            enqueue(e)
            done.append(e.rel)
    return done


def start():
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
