"""Package companions and the embedded workspace's matching control CLI."""

from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def _checked_path(relative: str) -> Path:
    """Reject aliases and links before reading or replacing owned package files."""
    if not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", relative):
        raise ValueError("Unsafe companion path.")
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        raise ValueError("Unsafe companion path.")
    for part in relative.split("/"):
        if part.endswith(".") or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.IGNORECASE):
            raise ValueError("Unsafe Windows companion path.")
    path = ROOT
    for part in relative.split("/"):
        path = path / part
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("Companion paths must not contain links.")
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("Companion path escapes the installation.")
    return path


def _embedded_cli() -> tuple[Path, str]:
    source = _checked_path("apps/desktop/src-tauri/workbench-core/target/release/terax-cli.exe")
    if not source.is_file() or not 0 < source.stat().st_size <= 32 * 1024 * 1024:
        raise ValueError("Build the embedded workspace CLI before packaging.")
    revision = json.loads(_checked_path("apps/desktop/workbench/SOURCE.json").read_text(encoding="utf-8"))["revision"]
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Invalid embedded workspace revision.")
    return source, revision


def _stage(path: Path, contents: bytes) -> Path:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".pending", delete=False) as output:
            temporary = Path(output.name)
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _manifest_bytes(catalog: dict) -> bytes:
    return (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _publish_directory(destination: Path, contents: dict[str, bytes]) -> None:
    if destination.exists():
        if not destination.is_dir() or {item.name for item in destination.iterdir()} != set(contents):
            raise ValueError("An immutable CLI package already exists with unexpected files.")
        for name, data in contents.items():
            path = _checked_path(str((destination / name).relative_to(ROOT).as_posix()))
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError("An immutable CLI package already exists with different contents.")
        return
    staged = Path(tempfile.mkdtemp(dir=destination.parent, prefix=destination.name + ".pending-"))
    try:
        for name, data in contents.items():
            with (staged / name).open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        staged.rename(destination)
    finally:
        # Remove only our unpublished staging files; published packages are immutable.
        if staged.exists():
            for name in contents:
                (staged / name).unlink(missing_ok=True)
            staged.rmdir()


def package() -> None:
    source, revision = _embedded_cli()
    manifest = _checked_path("integrations/companions.json")
    license_path = _checked_path("third_party/terax/LICENSE")
    if not license_path.is_file() or not 0 < license_path.stat().st_size <= 256 * 1024:
        raise ValueError("Missing Terax Apache license.")
    license_bytes = license_path.read_bytes()
    if b"Apache License" not in license_bytes:
        raise ValueError("Invalid Terax Apache license.")
    cli_bytes = source.read_bytes()
    digest = hashlib.sha256(cli_bytes).hexdigest()
    prefix = "terax/" + digest[:16] + "/"
    contents = {"terax-cli.exe": cli_bytes, "LICENSE": license_bytes}
    entry = {"name": "Jarvis terminal CLI", "revision": revision, "cli_revision": revision,
             "executable": prefix + "terax-cli.exe",
             "files": {prefix + name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}}
    component_root = _checked_path("desktop-bin/companions/terax")
    destination = _checked_path("desktop-bin/companions/" + prefix.rstrip("/"))
    component_root.mkdir(parents=True, exist_ok=True)
    _publish_directory(destination, contents)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    staged_manifest = _stage(manifest, _manifest_bytes({"schema": 1, "platform": "windows-x86_64", "components": {"terax": entry}}))
    try:
        staged_manifest.replace(manifest)
    finally:
        staged_manifest.unlink(missing_ok=True)
    print("Embedded terminal CLI packaged; manifest atomically published.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-only", action="store_true", help="Compatibility flag; packaging always contains only the CLI.")
    parser.parse_args()
    package()
