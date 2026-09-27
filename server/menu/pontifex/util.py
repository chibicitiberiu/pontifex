"""Small helpers shared across the service."""

import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, file=sys.stderr, flush=True)


def q(path):
    """Percent-encode a path for use in an iPXE/HTTP URL (keeps '/')."""
    return urllib.parse.quote(path, safe="/-_.~")


def ipxe_text(s):
    """Menu labels must not contain iPXE expansions or line breaks."""
    return re.sub(r"[\r\n\t]", " ", s).replace("${", "$ {").strip()


def run(cmd, timeout=3600, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kwargs)


def check(r, what):
    """Raise with the tail of the tool's output if a run() failed."""
    if r.returncode != 0:
        raise RuntimeError(f"{what}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r


def download(url, dest):
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    shutil.move(tmp, dest)
