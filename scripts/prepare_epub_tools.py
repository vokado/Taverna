"""Optional development-only EPUB validator and real browser reader, pinned by SHA256.

Never called by the application or portable build. Runtime has no Java/cloud dependency.
"""

import hashlib
import io
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1] / ".test-tools"
SOURCES = [
    (
        "https://github.com/w3c/epubcheck/releases/download/v5.4.0/epubcheck-5.4.0.zip",
        "33350c61038e71dfb3d45a76aed04bf5481e6d5500cb780f6e98db8bbd15a28c",
        "epubcheck",
    ),
    (
        "https://registry.npmjs.org/epubjs/-/epubjs-0.3.93.tgz",
        "180b33059ca15e2b4f129eb7fef6cae0bf6005c02af5194d13e5cb48378d538f",
        "epubjs",
    ),
    (
        "https://registry.npmjs.org/jszip/-/jszip-3.10.1.tgz",
        "5117f4a2a645aeb307bf3b829c575ad58135cc97e75291e594532ab5b5b21b23",
        "jszip",
    ),
]


def main():
    ROOT.mkdir(exist_ok=True)
    for url, checksum, kind in SOURCES:
        cached = ROOT / (kind + ".archive")
        if not cached.exists() or hashlib.sha256(cached.read_bytes()).hexdigest() != checksum:
            with urllib.request.urlopen(url, timeout=90) as response:
                raw = response.read(80 * 1024 * 1024 + 1)
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise RuntimeError("Development tool checksum mismatch: " + kind)
            cached.write_bytes(raw)
        raw = cached.read_bytes()
        if kind == "epubcheck":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for entry in z.infolist():
                    parts = PurePosixPath(entry.filename).parts
                    if entry.is_dir():
                        continue
                    if (
                        not parts
                        or parts[0] != "epubcheck-5.4.0"
                        or ".." in parts
                        or PurePosixPath(entry.filename).is_absolute()
                    ):
                        raise RuntimeError("Unexpected tool archive path")
                    path = ROOT.joinpath(*parts)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(z.read(entry))
        else:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as t:
                for entry in t.getmembers():
                    name = entry.name
                    if name in [
                        "package/dist/" + ("epub" if kind == "epubjs" else "jszip") + ".min.js",
                        "package/LICENSE",
                        "package/LICENSE.markdown",
                    ]:
                        stream = t.extractfile(entry)
                        if stream:
                            path = ROOT / kind / Path(name).name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(stream.read())
        print(kind, checksum)


if __name__ == "__main__":
    main()
