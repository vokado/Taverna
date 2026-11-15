"""Collect installed third-party notices for every internal portable archive."""

import hashlib
import json
import re
import shutil
import sys
import sysconfig
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "dist/licenses"


def copy_notice(source: Path, folder: Path, index: int):
    raw = source.read_bytes()
    name = re.sub(r"[^\w.\-]", "_", source.name)
    destination = folder / f"{index:03}-{name}"
    folder.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return {
        "path": str(destination.relative_to(TARGET)).replace("\\", "/"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def main():
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    manifest = []
    missing = []
    for package in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = package.metadata["Name"]
        if name.lower() == "taverna":
            continue
        files = [
            p
            for p in package.files or []
            if any(part.lower().startswith(("license", "copying", "notice")) for part in Path(str(p)).parts)
        ]
        records = []
        for index, path in enumerate(files):
            source = Path(package.locate_file(path))
            if source.is_file():
                records.append(copy_notice(source, TARGET / "python" / re.sub(r"[^\w.\-]", "_", name), index))
        if not records:
            missing.append(name)
        manifest.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": package.version,
                "license_expression": package.metadata.get(
                    "License-Expression", package.metadata.get("License", "")
                ),
                "notices": records,
            }
        )
    candidates = [Path(sys.base_prefix) / "LICENSE.txt", Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"]
    python_license = next((p for p in candidates if p.is_file()), None)
    if python_license:
        manifest.append(
            {
                "ecosystem": "runtime",
                "name": "CPython",
                "version": sys.version.split()[0],
                "notices": [copy_notice(python_license, TARGET / "CPython", 0)],
            }
        )
    else:
        missing.append("CPython LICENSE.txt")
    web = ROOT / "app/web"
    lock = json.loads((web / "package-lock.json").read_text(encoding="utf-8"))
    for path, package in lock["packages"].items():
        build_notice_packages = {
            "node_modules/vite",
            "node_modules/rollup",
            "node_modules/esbuild",
            "node_modules/typescript",
            "node_modules/@vitejs/plugin-react",
            "node_modules/@babel/core",
        }
        if not path or (package.get("dev") and path not in build_notice_packages):
            continue
        directory = web / path
        if not directory.is_dir():
            continue
        metadata = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        name = metadata["name"]
        records = []
        for index, source in enumerate(sorted(directory.iterdir())):
            if source.is_file() and source.name.lower().startswith(("license", "copying", "notice")):
                records.append(copy_notice(source, TARGET / "node" / re.sub(r"[^\w.\-]", "_", name), index))
        if not records:
            missing.append(name)
        manifest.append(
            {
                "ecosystem": "node",
                "name": name,
                "version": metadata["version"],
                "license_expression": metadata.get("license", ""),
                "notices": records,
            }
        )
    (TARGET / "manifest.json").write_text(
        json.dumps({"packages": manifest, "missing": missing}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if missing:
        raise RuntimeError("Не найдены обязательные тексты лицензий: " + ", ".join(missing))
    print(f"Collected license notices for {len(manifest)} packages/runtime entries")


if __name__ == "__main__":
    main()
