"""Pinned portable audio.cpp and Supertonic 3 without local pip/build steps."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Lock
import zipfile
from local_mode.config import ROOT
from local_mode.messages import OwnedValueError
from local_mode.downloads import download_file, cancellable_lock, check_cancelled

ASSETS = json.loads(Path(__file__).with_name("audio_cpp_assets.json").read_text(encoding="utf-8"))
DIRECTORY = ROOT / "models" / "audio_cpp"
RUNTIME = DIRECTORY / "runtime"
MODEL = DIRECTORY / "supertonic-3-orig.gguf"
INSTALL_LOCK = Lock()


def matches(path: Path, digest: str, cancel_event=None) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        checksum = hashlib.sha256()
        while chunk := stream.read(64 * 1024):
            check_cancelled(cancel_event)
            checksum.update(chunk)
        return checksum.hexdigest() == digest


def verify_runtime(cancel_event=None) -> bool:
    return all(matches(RUNTIME / name, item["sha256"], cancel_event) for name, item in ASSETS["runtime_files"].items())


def ensure_assets(*, include_default_model: bool = True, cancel_event=None) -> None:
    """Publish only verified files with manifest-allowlisted names."""
    with cancellable_lock(INSTALL_LOCK, cancel_event):
        if not (verify_runtime() if cancel_event is None else verify_runtime(cancel_event)):
            archive = ROOT / ".cache" / "audio-vulkan-portable.zip"
            download_file(ASSETS["archive_url"], archive, max_bytes=80_000_000,
                          sha256=ASSETS["archive_sha256"], cancel_event=cancel_event)
            RUNTIME.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as package:
                for name, item in ASSETS["runtime_files"].items():
                    check_cancelled(cancel_event)
                    if Path(name).name != name or ":" in name or "\\" in name:
                        raise OwnedValueError("Invalid audio.cpp filename.")
                    info = package.getinfo(name)
                    if info.file_size != item["size"]:
                        raise OwnedValueError("audio.cpp file size does not match.")
                    data = package.read(info)
                    if hashlib.sha256(data).hexdigest() != item["sha256"]:
                        raise OwnedValueError("audio.cpp file SHA256 does not match.")
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(dir=RUNTIME, delete=False) as stream:
                            temporary = Path(stream.name)
                            stream.write(data)
                        temporary.replace(RUNTIME / name)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
        if not include_default_model:
            return
        download_file(ASSETS["model_url"], MODEL, max_bytes=ASSETS["model_size"],
                      sha256=ASSETS["model_sha256"], cancel_event=cancel_event)
        download_file(ASSETS["model_license_url"], DIRECTORY / "LICENSE-Supertonic-3.txt",
                      max_bytes=100_000, sha256=ASSETS["model_license_sha256"], cancel_event=cancel_event)
