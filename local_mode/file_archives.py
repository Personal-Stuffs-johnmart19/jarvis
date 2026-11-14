"""Bounded ZIP operations for the canonical Python file handler.

Complete metadata/path preflight and CRC-checked delete-on-close spooling precede
publication. Existing files are never overwritten. Stop can interrupt publication;
already published files remain observable and must not be blindly replayed.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import zipfile
from local_mode.file_policy import check_readable, checked_open

MAX_ENTRIES = 10000
MAX_TOTAL = 512 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
_PROTECTED = {".git", ".ssh", ".aws", ".azure", ".kube", ".gnupg", ".codex",
    ".config", ".docker", "credentials", ".credentials", "api_keys.json",
    "bootstrap-key.json", "google_token.json", ".npmrc", ".pypirc", ".netrc",
    "_netrc", "secrets.json", "secrets.yaml", "secrets.yml"}


def _allowed(path):
    try:
        check_readable(path)
    except ValueError as error:
        raise ValueError("ZIP contains a protected credential or metadata path") from error
    for part in Path(path).parts:
        name = part.casefold()
        if (name in _PROTECTED or name.startswith(("credentials", "service-account", "service_account",
                ".env", "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa"))
                or name.endswith((".dpapi", ".pem", ".key", ".p12", ".pfx", ".keystore"))):
            raise ValueError("ZIP contains a protected credential or metadata path")


def _member(name):
    if (not name or len(name.encode("utf-8")) > 1024 or any(char in name for char in '\\:*?"<>|')
            or name.startswith("/") or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise ValueError("Unsafe ZIP member name")
    parts = name.removesuffix("/").split("/")
    for part in parts:
        stem = part.casefold().split(".")[0].rstrip(" ")
        if (part in {"", ".", ".."} or part.endswith((".", " "))
                or stem in {"con", "prn", "aux", "nul"}
                or len(stem) == 4 and stem[:3] in {"com", "lpt"} and stem[3].isdigit()):
            raise ValueError("ZIP member aliases, traversal or reserved names are not allowed")
    path = Path(*parts)
    _allowed(path)
    return path


def _checked(path):
    from local_mode.tools import validate_paths

    path = Path(path).expanduser().absolute()
    for part in path.parts:
        if part != path.anchor:
            _member(part)
    _allowed(path)
    validate_paths({"path": str(path)})
    for item in (path, *path.parents):
        if os.path.lexists(item):
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ValueError("ZIP links and reparse points are unsupported")
    return path.resolve()


def _destinations(root, entries):
    """Check explicit members, implied directories and case aliases before writes."""
    root = _checked(root)
    if root.exists() and not root.is_dir():
        raise ValueError("Extraction destination is not a directory")
    kinds = {}
    for relative, directory, _ in entries:
        paths = [(parent, True) for parent in reversed(relative.parents) if parent != Path('.')]
        for path, is_dir in paths + [(relative, directory)]:
            key = path.as_posix().casefold()
            previous = kinds.get(key)
            if previous is not None and previous != (path.as_posix(), is_dir):
                raise ValueError("ZIP contains conflicting or case-alias paths")
            kinds[key] = (path.as_posix(), is_dir)
            destination = _checked(root / path)
            if destination.exists() and (not is_dir or not destination.is_dir()):
                raise ValueError("ZIP destination already exists; choose a new path")
    return root


def _copy_exact(source, target, expected, *, verify_end=True):
    written = 0
    while written < expected:
        block = source.read(min(CHUNK_SIZE, expected - written))
        if not block:
            raise ValueError("ZIP member size does not match its metadata")
        written += len(block)
        target.write(block)
    if verify_end and source.read(1):
        raise ValueError("ZIP member size changed or exceeds its metadata")


def extract_zip(archive, destination):
    archive = _checked(archive)
    if archive.stat().st_size > MAX_TOTAL + 8 * 1024 * 1024:
        raise ValueError("ZIP input exceeds the size limit")
    with checked_open(archive) as pinned_archive, zipfile.ZipFile(pinned_archive) as bundle:
        members = bundle.infolist()
        if len(members) > MAX_ENTRIES:
            raise ValueError("ZIP exceeds the entry limit")
        names, entries, total = set(), [], 0
        for member in members:
            relative = _member(member.orig_filename)
            kind = stat.S_IFMT(member.external_attr >> 16)
            if member.flag_bits & 1 or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError("ZIP links, special files and encryption are unsupported")
            if member.is_dir() and member.file_size:
                raise ValueError("ZIP directory contains unexpected data")
            key = relative.as_posix().casefold()
            if key in names:
                raise ValueError("Duplicate or case-alias ZIP members are not allowed")
            names.add(key)
            total += member.file_size
            if total > MAX_TOTAL:
                raise ValueError("ZIP exceeds the expanded size limit")
            entries.append((relative, member.is_dir(), member.file_size))
        destination = _destinations(destination, entries)
        # TemporaryFile uses OS delete-on-close on Windows, including a killed worker.
        with tempfile.TemporaryFile() as spool:
            offsets = []
            for member, (_, directory, size) in zip(members, entries):
                offsets.append(spool.tell())
                if not directory:
                    with bundle.open(member) as source:
                        _copy_exact(source, spool, size)
            _destinations(destination, entries)
            destination.mkdir(parents=True, exist_ok=True)
            for offset, (relative, directory, size) in zip(offsets, entries):
                output = _checked(destination / relative)
                if directory:
                    output.mkdir(parents=True, exist_ok=True)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    _checked(output)
                    spool.seek(offset)
                    with output.open("xb") as target:
                        _copy_exact(spool, target, size, verify_end=False)
    return f"Extracted {len(entries)} item(s) → {destination}"


def create_zip(source, destination):
    source, destination = _checked(source), _checked(destination)
    if not source.exists():
        raise ValueError("ZIP source not found")
    if destination.exists():
        raise ValueError("ZIP destination already exists; choose a new path")
    if destination == source or source.is_dir() and destination.is_relative_to(source):
        raise ValueError("ZIP destination must be outside its source tree")
    entries, names, total = [], set(), 0
    pending = [source]
    while pending:
        path = _checked(pending.pop())
        metadata = path.stat()
        directory = stat.S_ISDIR(metadata.st_mode)
        if not directory and not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Special files cannot be archived")
        relative = path.relative_to(source.parent)
        _member(relative.as_posix())
        key = relative.as_posix().casefold()
        if key in names:
            raise ValueError("Duplicate archive entry")
        names.add(key)
        size = 0 if directory else metadata.st_size
        total += size
        if len(entries) >= MAX_ENTRIES or total > MAX_TOTAL:
            raise ValueError("ZIP exceeds the entry or size limit")
        entries.append((path, relative, directory, size))
        if directory:
            with os.scandir(path) as children:
                for child in children:
                    if len(pending) + len(entries) >= MAX_ENTRIES:
                        raise ValueError("ZIP exceeds the entry limit")
                    pending.append(Path(child.path))
    with tempfile.TemporaryFile() as spool:
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path, relative, directory, size in entries:
                _checked(path)
                name = relative.as_posix()
                if directory:
                    bundle.writestr(name + "/", b"")
                else:
                    with checked_open(path) as content, bundle.open(name, "w") as target:
                        _copy_exact(content, target, size)
        size = spool.tell()
        if size > MAX_TOTAL + 8 * 1024 * 1024:
            raise ValueError("ZIP output exceeds the size limit")
        spool.seek(0)
        _checked(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as target:
            _copy_exact(spool, target, size)
    return f"Zipped {sum(not entry[2] for entry in entries)} file(s) → {destination}"
