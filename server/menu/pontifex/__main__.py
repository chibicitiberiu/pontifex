"""Run the menu service: python3 -m pontifex"""

import os
import shutil
import sys
from http.server import ThreadingHTTPServer

from . import config, worker
from .http import Handler
from .util import log


def main():
    if "PXE_BASE_URL" not in os.environ:
        sys.exit("PXE_BASE_URL must be set (e.g. http://192.168.1.10:8069)")
    os.makedirs(config.CACHE, exist_ok=True)
    for tool in ("bsdtar", "7z"):
        if not shutil.which(tool):
            log(f"WARNING: {tool} not found on PATH")
    worker.start()
    log(f"listening on :{config.LISTEN_PORT}, library {config.LIBRARY}")
    ThreadingHTTPServer(("0.0.0.0", config.LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
