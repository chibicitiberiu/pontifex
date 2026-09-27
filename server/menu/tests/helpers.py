"""Test helpers: a temporary library + cache, configured before importing the service."""

import os
import tempfile

os.environ.setdefault("PXE_BASE_URL", "http://pxe.test:8069")

from pontifex import config  # noqa: E402


class TempSite:
    """A throwaway library and cache; points pontifex.config at them."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.library = os.path.join(root, "library")
        self.cache = os.path.join(root, "cache")
        self.images = os.path.join(root, "images")
        for d in (self.library, self.cache, self.images):
            os.makedirs(d)
        config.LIBRARY, config.CACHE = self.library, self.cache
        config.HOSTS_FILE = os.path.join(root, "hosts.conf")
        config.WRITER_DIR = os.path.join(root, "tftp", "brandr")

    def image(self, name, size=4096, data=b""):
        """Create a fake image file under images/."""
        path = os.path.join(self.images, name)
        with open(path, "wb") as f:
            f.write(data.ljust(size, b"\0"))
        return path

    def link(self, rel, target):
        path = os.path.join(self.library, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)
        return path

    def write(self, rel, text):
        path = os.path.join(self.library, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def close(self):
        self.tmp.cleanup()
