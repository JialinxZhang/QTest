"""Bounded, data-only transport. Hashes detect changes; they are not signatures."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from .contracts import canonical, loads, read_json, require, validate_suite

MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 10005


def safe_name(name):
    require(isinstance(name, str) and name and "\\" not in name and ":" not in name,
            "Unsafe archive path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == name, "Unsafe archive path")


def check_files(kind, files):
    require(kind in ("task", "result"), "Unknown bundle kind")
    require(len(files) <= MAX_FILES and sum(len(data) for data in files.values()) <= MAX_BYTES, "Bundle exceeds pilot size limit")
    for name in files:
        safe_name(name)
        if kind == "task":
            require(name == "suite.json", "Task bundles contain only suite.json")
        else:
            require(name in ("suite.json", "run.json") or re.fullmatch(r"observations/[0-9]{6}\.json", name), "Unexpected result file")
    require("suite.json" in files and (kind == "task" or "run.json" in files), "Incomplete bundle")


def write_bundle(output, kind, files):
    check_files(kind, files)
    manifest = {"format": "qtest-bundle-1", "kind": kind,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical(manifest))
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    return manifest


def read_bundle(path, kind):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        require(len(entries) <= MAX_FILES + 1, "Too many archive entries")
        names = [e.filename for e in entries]
        require(len(names) == len(set(names)), "Duplicate archive paths")
        require(sum(e.file_size for e in entries) <= MAX_BYTES, "Archive exceeds uncompressed size limit")
        for entry in entries:
            safe_name(entry.filename)
            require(not entry.is_dir() and not stat.S_ISLNK(entry.external_attr >> 16), "Directories and symlinks are not allowed in bundles")
        require("manifest.json" in names, "Missing manifest")
        manifest = loads(archive.read("manifest.json"))
        require(manifest.get("format") == "qtest-bundle-1" and manifest.get("kind") == kind, "Wrong bundle format or kind")
        require(isinstance(manifest.get("files"), dict), "Invalid manifest file map")
        require(set(manifest["files"]) == set(names) - {"manifest.json"}, "Manifest and archive file sets differ")
        files = {name: archive.read(name) for name in manifest["files"]}
        check_files(kind, files)
        for name, data in files.items():
            require(hashlib.sha256(data).hexdigest() == manifest["files"][name], f"File hash mismatch: {name}")
        return files


def read_suite(path):
    path = Path(path)
    if path.suffix.lower() == ".zip":
        return validate_suite(loads(read_bundle(path, "task")["suite.json"]))
    require(path.stat().st_size <= MAX_BYTES, "Suite too large")
    return validate_suite(read_json(path))


def read_run(path):
    path = Path(path)
    if not path.is_dir():
        return read_bundle(path, "result")
    files, total = {}, 0
    for name in ("suite.json", "run.json"):
        file = path / name
        require(not file.is_symlink(), "Symlink evidence is not supported")
        require(file.stat().st_size <= MAX_BYTES, "Evidence too large")
        files[name] = file.read_bytes()
        total += len(files[name])
    observations = path / "observations"
    require(not observations.is_symlink(), "Symlink evidence directory is not supported")
    if observations.exists():
        for file in sorted(observations.iterdir()):
            require(file.is_file() and not file.is_symlink(), "Unexpected observation entry")
            total += file.stat().st_size
            require(total <= MAX_BYTES and len(files) < MAX_FILES, "Evidence exceeds pilot size limit")
            files["observations/" + file.name] = file.read_bytes()
    check_files("result", files)
    return files
