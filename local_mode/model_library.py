"""Managed model activation, verification, and manifest reading.

Strictly adheres to the Rust model_downloads contract:
- ROOT/data/models/{kind}/{repo.replace('/','__')}/{revision}/manifest.json
- Managed root respects JARVIS_DESKTOP_TEST (data vs .cache/desktop-test).
- Verifies owner, completeness, pinned revision, containment, no symlinks/reparse points,
  file sizes, and SHA256 checksums at activation time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from local_mode.config import ROOT
from local_mode.messages import OwnedValueError

OWNER = "jarvis-model-downloads/1"
MANIFEST_NAME = "manifest.json"
SUPPORTED_KINDS = ("llm", "vision", "embedding", "tts")
ModelKind = Literal["llm", "vision", "embedding", "tts"]

RESERVED_NAMES = frozenset([
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
])


def get_models_root() -> Path:
    """Follow desktop-test root matching Rust and local_mode storage conventions."""
    if os.getenv("JARVIS_DESKTOP_TEST") == "1":
        return ROOT / ".cache" / "desktop-test" / "models"
    return ROOT / "data" / "models"


def repo_dir_name(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def refuse_symlink(path: Path) -> None:
    """Refuse symlinks and (on Windows) reparse points/junctions."""
    if not path.exists() and not path.is_symlink():
        return
    try:
        st = os.lstat(path)
    except OSError as err:
        raise ValueError(f"Stat failed for {path}: {err}") from err

    # Check symlink
    import stat
    if stat.S_ISLNK(st.st_mode):
        raise ValueError(f"Symlink refused: {path}")

    # Check Windows reparse point
    if os.name == "nt":
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        attrs = getattr(st, "st_file_attributes", 0)
        if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(f"Reparse point/junction refused: {path}")


def verify_ancestors_no_symlinks(root: Path, target: Path) -> None:
    """Verify that neither target nor any intermediate directory down from root is a symlink/reparse point."""
    refuse_symlink(root)
    try:
        canon_root = root.resolve(strict=True)
    except OSError as err:
        raise ValueError(f"Managed root cannot be resolved: {root}") from err

    # Walk components relative to root
    try:
        rel = target.relative_to(root)
    except ValueError as err:
        raise ValueError(f"Target is not under managed root: {target}") from err

    cur = root
    for part in rel.parts:
        cur = cur / part
        refuse_symlink(cur)

    try:
        canon_target = target.resolve(strict=True)
    except OSError as err:
        raise ValueError(f"Target path cannot be resolved: {target}") from err

    if not (canon_target == canon_root or canon_root in canon_target.parents):
        raise ValueError(f"Target path escapes managed root: {target}")


def validate_rel_file_path(rel_path: str) -> list[str]:
    """Validate relative manifest file path against native Rust contract."""
    if not isinstance(rel_path, str) or not rel_path or len(rel_path) > 512:
        raise ValueError(f"Invalid file path length: {rel_path!r}")
    comps = rel_path.split("/")
    for i, c in enumerate(comps):
        if not c or c == "." or c == ".." or len(c) > 255:
            raise ValueError(f"Unsafe component in path: {c!r}")
        if any(ord(ch) < 32 or ch in '\\:*?"<>|' for ch in c):
            raise ValueError(f"Unsafe characters in component: {c!r}")
        if c.endswith(".") or c.endswith(" "):
            raise ValueError(f"Unsafe trailing whitespace or dot in component: {c!r}")
        if c.lower().endswith(".part"):
            raise ValueError(f"Part files not permitted in manifest: {c!r}")
        if i == 0 and (c == MANIFEST_NAME or c.lower() == MANIFEST_NAME.lower()):
            raise ValueError(f"Manifest collision in file path: {c!r}")
        base_name = c.split(".")[0].upper()
        if base_name in RESERVED_NAMES:
            raise ValueError(f"Reserved device name in path: {c!r}")
    return comps


def ensure_safe_path(base_dir: Path, rel_path: str) -> Path:
    """Safely resolve rel_path below base_dir without escaping or crossing symlinks."""
    validate_rel_file_path(rel_path)
    refuse_symlink(base_dir)
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise OwnedValueError("Path escapes managed directory: {0}", rel_path)

    cur = base_dir
    for part in rel.parts:
        cur = cur / part
        refuse_symlink(cur)

    # Containment check
    try:
        canon_base = base_dir.resolve(strict=True)
        canon_cur = cur.resolve(strict=True)
    except OSError as err:
        raise OwnedValueError("Path does not exist or cannot be resolved: {0}", str(cur)) from err

    if not (canon_cur == canon_base or canon_base in canon_cur.parents):
        raise OwnedValueError("Path escapes managed root: {0}", str(cur))

    return cur


def compute_sha256(path: Path, max_bytes: int = 64 * 1024 * 1024 * 1024) -> tuple[int, str]:
    """Compute size and sha256 of a file in chunks."""
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ValueError(f"File exceeds maximum allowed bytes: {path}")
            h.update(chunk)
    return size, h.hexdigest().lower()


def read_manifest_file(dir_path: Path) -> dict[str, Any] | None:
    """Read manifest.json from dir_path if valid and owned by Rust downloader."""
    try:
        refuse_symlink(dir_path)
        p = dir_path / MANIFEST_NAME
        refuse_symlink(p)
        if not p.is_file():
            return None
        st = os.lstat(p)
        if st.st_size > 1024 * 1024:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if data.get("owner") != OWNER:
            return None
        return data
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def validate_manifest_structure(manifest: dict[str, Any], expected_kind: str | None = None, expected_repo: str | None = None, expected_rev: str | None = None) -> None:
    """Validate strict manifest schema matching Rust contract."""
    if not isinstance(manifest, dict):
        raise ValueError("Manifest is not a dictionary")
    if manifest.get("owner") != OWNER:
        raise ValueError(f"Manifest owner mismatch: {manifest.get('owner')}")
    if not isinstance(manifest.get("complete"), bool):
        raise ValueError("Manifest complete must be a boolean")
    repo_id = manifest.get("repo_id")
    if not isinstance(repo_id, str) or not repo_id:
        raise ValueError("Manifest repo_id missing or invalid")
    if expected_repo is not None and repo_id != expected_repo:
        raise ValueError(f"Manifest repo_id mismatch: {repo_id} != {expected_repo}")
    revision = manifest.get("revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("Manifest revision missing or invalid")
    if expected_rev is not None and revision != expected_rev:
        raise ValueError(f"Manifest revision mismatch: {revision} != {expected_rev}")
    kind = manifest.get("kind")
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"Manifest kind invalid: {kind}")
    if expected_kind is not None and kind != expected_kind:
        raise ValueError(f"Manifest kind mismatch: {kind} != {expected_kind}")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) == 0 or len(files) > 1024:
        raise ValueError(f"Manifest files missing, empty, or exceeds limit: {len(files) if isinstance(files, list) else type(files)}")
    seen_paths = set()
    for f in files:
        if not isinstance(f, dict):
            raise ValueError("Corrupt file record in manifest: not a dict")
        path = f.get("path")
        size = f.get("size")
        sha256 = f.get("sha256")
        if not isinstance(path, str) or not isinstance(size, int) or size < 0 or not isinstance(sha256, str):
            raise ValueError(f"Corrupt field types in file record: {f}")
        validate_rel_file_path(path)
        if path.lower() in seen_paths:
            raise ValueError(f"Duplicate file path in manifest: {path}")
        seen_paths.add(path.lower())
        if len(sha256) != 64 or not all(c in "0123456789abcdefABCDEF" for c in sha256):
            raise ValueError(f"Invalid sha256 hex string: {sha256}")


def list_installed_models() -> list[dict[str, Any]]:
    """Enumerate installed models across kinds without loading weights or hashing large files."""
    root = get_models_root()
    refuse_symlink(root)
    if not root.is_dir():
        return []

    installed = []
    for kind in SUPPORTED_KINDS:
        kind_dir = root / kind
        refuse_symlink(kind_dir)
        if not kind_dir.is_dir():
            continue
        try:
            for repo_entry in os.scandir(kind_dir):
                if not repo_entry.is_dir(follow_symlinks=False):
                    continue
                repo_path = Path(repo_entry.path)
                try:
                    refuse_symlink(repo_path)
                    for rev_entry in os.scandir(repo_path):
                        if not rev_entry.is_dir(follow_symlinks=False):
                            continue
                        rev_path = Path(rev_entry.path)
                        try:
                            verify_ancestors_no_symlinks(root, rev_path)
                        except ValueError:
                            continue
                        manifest = read_manifest_file(rev_path)
                        if not manifest or manifest.get("complete") is not True:
                            continue
                        # Validate manifest structure and parent alignment
                        expected_repo_dir = repo_entry.name
                        try:
                            validate_manifest_structure(manifest, expected_kind=kind, expected_rev=rev_entry.name)
                            if repo_dir_name(manifest["repo_id"]) != expected_repo_dir:
                                continue
                            for record in manifest["files"]:
                                weight = ensure_safe_path(rev_path, record["path"])
                                if not weight.is_file() or weight.stat().st_size != record["size"]:
                                    raise ValueError("Incomplete installed model files")
                        except ValueError:
                            continue

                        installed.append({
                            "kind": manifest.get("kind", kind),
                            "repo_id": manifest.get("repo_id"),
                            "revision": manifest.get("revision", rev_entry.name),
                            "bundle_id": manifest.get("bundle_id"),
                            "complete": bool(manifest.get("complete")),
                            "files": [f["path"] for f in manifest.get("files", []) if isinstance(f, dict) and "path" in f],
                            "license": manifest.get("license"),
                        })
                except (OSError, ValueError):
                    continue
        except (OSError, ValueError):
            continue

    return installed


def inspect_gguf_header(path: Path) -> dict[str, Any]:
    """Parse basic GGUF header to extract metadata like embedding dimensions if available.
    
    Safe, read-only parsing of GGUF v2/v3 without loading weights.
    Returns empty dict on parse failure.
    """
    import struct
    try:
        with path.open("rb") as f:
            metadata_end = min(os.fstat(f.fileno()).st_size, 64 * 1024 * 1024)
            magic = f.read(4)
            if magic != b"GGUF":
                return {}
            version_bytes = f.read(4)
            if len(version_bytes) < 4:
                return {}
            (version,) = struct.unpack("<I", version_bytes)
            if version not in (2, 3):
                return {}
            (tensor_count,) = struct.unpack("<Q", f.read(8))
            (kv_count,) = struct.unpack("<Q", f.read(8))
            
            # Read KV pairs (bounded)
            metadata: dict[str, Any] = {}
            for _ in range(min(kv_count, 1024)):
                # read string: uint64 length then bytes
                len_bytes = f.read(8)
                if len(len_bytes) < 8:
                    break
                (k_len,) = struct.unpack("<Q", len_bytes)
                if k_len > 256:
                    break
                k_bytes = f.read(k_len)
                key = k_bytes.decode("utf-8", errors="replace")
                
                # read type: uint32
                t_bytes = f.read(4)
                if len(t_bytes) < 4:
                    break
                (val_type,) = struct.unpack("<I", t_bytes)
                
                # Types in GGUF:
                # 0: UINT8, 1: INT8, 2: UINT16, 3: INT16, 4: UINT32, 5: INT32,
                # 6: FLOAT32, 7: BOOL, 8: STRING, 9: ARRAY, 10: UINT64, 11: INT64, 12: FLOAT64
                if val_type in (0, 1):
                    val = struct.unpack("<B" if val_type == 0 else "<b", f.read(1))[0]
                elif val_type in (2, 3):
                    val = struct.unpack("<H" if val_type == 2 else "<h", f.read(2))[0]
                elif val_type in (4, 5):
                    val = struct.unpack("<I" if val_type == 4 else "<i", f.read(4))[0]
                elif val_type == 6:
                    val = struct.unpack("<f", f.read(4))[0]
                elif val_type == 7:
                    val = struct.unpack("<?", f.read(1))[0]
                elif val_type == 8:
                    s_len = struct.unpack("<Q", f.read(8))[0]
                    if s_len > 4096:
                        break
                    val = f.read(s_len).decode("utf-8", errors="replace")
                elif val_type in (10, 11):
                    val = struct.unpack("<Q" if val_type == 10 else "<q", f.read(8))[0]
                elif val_type == 12:
                    val = struct.unpack("<d", f.read(8))[0]
                elif val_type == 9:
                    element_type, count = struct.unpack("<IQ", f.read(12))
                    if count > 1000000:
                        return {}
                    widths = {0:1, 1:1, 2:2, 3:2, 4:4, 5:4, 6:4, 7:1, 10:8, 11:8, 12:8}
                    if element_type in widths:
                        f.seek(count * widths[element_type], 1)
                    elif element_type == 8:
                        for _ in range(count):
                            length = struct.unpack("<Q", f.read(8))[0]
                            if length > 16 * 1024 * 1024:
                                return {}
                            f.seek(length, 1)
                            if f.tell() > metadata_end:
                                return {}
                    else:
                        return {}
                    if f.tell() > metadata_end:
                        return {}
                    continue
                else:
                    return {}
                
                metadata[key] = val
                if key.endswith(".embedding_length"):
                    metadata["embedding_length"] = val
            return metadata
    except Exception:
        return {}


def derive_model_files(kind: str, manifest: dict[str, Any], dir_path: Path) -> dict[str, Any]:
    """Derive exact GGUF and optional projector from manifest files without guessing."""
    bundle_id = manifest.get("bundle_id", "")
    files = manifest.get("files", [])
    paths = [f["path"] for f in files if isinstance(f, dict) and "path" in f]

    ggufs = [p for p in paths if p.lower().endswith(".gguf") and "mmproj" not in Path(p).name.lower()]
    projectors = [p for p in paths if p.lower().endswith(".gguf") and "mmproj" in Path(p).name.lower()]

    if kind != "llm" and any("-of-" in path.lower() for path in ggufs + projectors):
        raise OwnedValueError("Split GGUF activation currently supports language models only")

    if kind == "llm":
        split = re.compile(r"^(.+)-(\d{5})-of-(\d{5})\.gguf$")
        parsed = [split.fullmatch(path) for path in ggufs]
        if any(parsed):
            if not all(parsed):
                raise OwnedValueError("A split model cannot be mixed with standalone GGUF files")
            group = {(match[1], int(match[3])) for match in parsed}
            if len(group) != 1:
                raise OwnedValueError("Expected one complete GGUF shard group")
            _, count = next(iter(group))
            ordered = sorted(zip(parsed, ggufs), key=lambda row: int(row[0][2]))
            if not 1 <= count <= 1024 or [int(match[2]) for match, _ in ordered] != list(range(1, count + 1)):
                raise OwnedValueError("GGUF shard group is incomplete or contains duplicate parts")
            return {"model_path": str(ensure_safe_path(dir_path, ordered[0][1])), "projector_path": "", "shard_paths": [str(ensure_safe_path(dir_path, name)) for _, name in ordered]}
        if len(ggufs) != 1:
            raise ValueError(f"Expected exactly 1 main GGUF file for llm, found {len(ggufs)}")
        if "-of-" in ggufs[0].lower():
            raise OwnedValueError("Invalid GGUF shard filename")
        main_path = ensure_safe_path(dir_path, ggufs[0])
        return {"model_path": str(main_path), "projector_path": ""}

    if kind == "vision":
        if len(ggufs) != 1:
            raise ValueError(f"Expected exactly 1 main GGUF file for vision, found {len(ggufs)}")
        if len(projectors) != 1:
            raise ValueError(f"Expected exactly 1 mmproj GGUF file for vision, found {len(projectors)}")
        main_path = ensure_safe_path(dir_path, ggufs[0])
        proj_path = ensure_safe_path(dir_path, projectors[0])
        return {"model_path": str(main_path), "projector_path": str(proj_path)}

    if kind == "embedding":
        if len(ggufs) != 1:
            raise ValueError(f"Expected exactly 1 main GGUF file for embedding, found {len(ggufs)}")
        main_path = ensure_safe_path(dir_path, ggufs[0])
        proj_path = str(ensure_safe_path(dir_path, projectors[0])) if projectors else ""
        return {"model_path": str(main_path), "projector_path": proj_path}

    if kind == "tts":
        if bundle_id == "tts-piper-onnx":
            onnx=[name for name in paths if name.lower().endswith(".onnx")]
            if len(onnx)!=1 or sorted(paths)!=sorted([onnx[0],onnx[0]+".json"]):
                raise OwnedValueError("Piper activation requires one ONNX model and its matching JSON configuration.")
            from local_mode.piper_models import inspect_config
            config_path=ensure_safe_path(dir_path,onnx[0]+".json")
            inspect_config(config_path)
            return {"model_path":str(ensure_safe_path(dir_path,onnx[0])),"projector_path":"","config_path":str(config_path),"family":"piper"}
        if manifest.get("bundle_id") != "tts-supertonic-gguf" or len(paths) != 1 or len(ggufs) != 1:
            raise ValueError("Only standalone Supertonic audio.cpp GGUF bundles can be activated")
        main_path = ensure_safe_path(dir_path, ggufs[0])
        header = inspect_gguf_header(main_path)
        if (header.get("general.architecture") != "audiocpp"
                or header.get("audiocpp.model_spec.family") != "supertonic"
                or header.get("audiocpp.model_spec.version") != 1):
            raise ValueError("GGUF is not a supported audio.cpp Supertonic package")
        return {"model_path": str(main_path), "projector_path": ""}

    raise ValueError(f"Unsupported model kind: {kind}")


def validate_shard_metadata(paths: list[str]) -> None:
    """Check split headers only during full verification, never catalogue discovery."""
    for index, name in enumerate(paths):
        header = inspect_gguf_header(Path(name))
        if (type(header.get("split.count")) is not int or type(header.get("split.no")) is not int
                or header["split.count"] != len(paths) or header["split.no"] != index):
            raise OwnedValueError("GGUF split metadata does not match the verified shard group")


def verify_and_get_model_assets(
    kind: str, repo_id: str, revision: str, *, verify_checksums: bool = True
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Validate installed model:
    - Root containment and all ancestor checks (refuse symlinks/reparse points)
    - Pinned revision matches directory & manifest
    - Owner matches
    - Manifest marked complete
    - Strict schema validation
    - File containment, existence, exact sizes, and sha256 checksums
    Returns (manifest, dir_path, derived_files).
    """
    if kind not in SUPPORTED_KINDS:
        raise OwnedValueError("Invalid model kind: {0}", str(kind))

    if not isinstance(repo_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_id):
        raise OwnedValueError("Invalid model repository ID")
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", revision) or revision in {".", ".."}:
        raise OwnedValueError("Invalid model revision")
    root = get_models_root()
    dir_name = repo_dir_name(repo_id)
    dir_path = root / kind / dir_name / revision

    # Containment & symlink checks through all ancestors
    verify_ancestors_no_symlinks(root, dir_path)

    manifest = read_manifest_file(dir_path)
    if not manifest:
        raise OwnedValueError("Manifest missing, unreadable, or invalid for {0}@{1}", repo_id, revision)

    if not manifest.get("complete"):
        raise OwnedValueError("Cannot activate incomplete model download")
    validate_manifest_structure(manifest, expected_kind=kind, expected_repo=repo_id, expected_rev=revision)

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise OwnedValueError("Manifest contains no files")

    # Verify each file's size and checksum
    for f in files:
        fpath = ensure_safe_path(dir_path, f["path"])
        if not fpath.is_file():
            raise OwnedValueError("Model file missing: {0}", f["path"])
        if not verify_checksums:
            if fpath.stat().st_size != f["size"]:
                raise OwnedValueError("Model file size changed since activation")
            continue
        actual_size, actual_sha = compute_sha256(fpath)
        if actual_size != f["size"]:
            raise OwnedValueError("Size mismatch for {0}: expected {1}, got {2}", f["path"], f["size"], actual_size)
        if actual_sha != f["sha256"].lower():
            raise OwnedValueError("SHA256 checksum mismatch for {0}", f["path"])

    derived = derive_model_files(kind, manifest, dir_path)
    if verify_checksums and derived.get("shard_paths"):
        validate_shard_metadata(derived["shard_paths"])
    return manifest, dir_path, derived


def validate_inference_path(
    path_str: str,
    expected_size: int | None = None,
    expected_mtime_ns: int | None = None,
) -> Path:
    """Revalidate path containment within managed models root, lack of symlinks/reparse points,
    and stat identity before server start/inference to prevent path tampering or arbitrary path start.
    """
    if not isinstance(path_str, str) or not path_str or len(path_str) > 2048:
        raise ValueError(f"Invalid path string: {path_str!r}")
    root = get_models_root()
    refuse_symlink(root)
    target = Path(path_str)
    if not target.is_file():
        raise ValueError(f"Target model file not found: {path_str}")

    verify_ancestors_no_symlinks(root, target)

    st = os.lstat(target)
    if expected_size is not None and st.st_size != expected_size:
        raise ValueError(f"Model file size changed since activation: expected {expected_size}, got {st.st_size}")
    if expected_mtime_ns is not None and getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)) != expected_mtime_ns:
        raise ValueError("Model file mtime changed since activation")

    return target


def validate_selected_model(selection: dict, kind: str = "embedding") -> dict:
    """Recheck the owned manifest and file identity without hashing weights per query."""
    if not isinstance(selection, dict):
        raise ValueError("Invalid model selection")
    manifest, directory, derived = verify_and_get_model_assets(
        kind, selection.get("repo_id"), selection.get("revision"), verify_checksums=False
    )
    for field, prefix in (("model_path", "model"), ("projector_path", "projector")):
        if selection.get(field, "") != derived[field]:
            raise ValueError("Selected model path does not match the managed manifest")
        if derived[field]:
            size, mtime = selection.get(prefix + "_size"), selection.get(prefix + "_mtime_ns")
            if type(size) is not int or type(mtime) is not int:
                raise ValueError("Model selection must be activated again")
            validate_inference_path(derived[field], size, mtime)
    if derived.get("config_path"):
        config_path=derived["config_path"]
        if selection.get("config_path")!=config_path:
            raise OwnedValueError("Selected Piper configuration does not match the managed manifest.")
        config=next(item for item in manifest["files"] if str(ensure_safe_path(directory,item["path"]))==config_path)
        if selection.get("config_sha256")!=config["sha256"] or type(selection.get("config_size")) is not int or type(selection.get("config_mtime_ns")) is not int:
            raise OwnedValueError("Piper configuration must be verified again.")
        validate_inference_path(config_path,selection["config_size"],selection["config_mtime_ns"])
    if derived.get("shard_paths"):
        stamps = selection.get("shards", [])
        if not isinstance(stamps, list) or any(not isinstance(item, dict) for item in stamps) or [item.get("path") for item in stamps] != derived["shard_paths"]:
            raise OwnedValueError("Model shards must be verified again")
        records = {str(ensure_safe_path(directory, item["path"])): item for item in manifest["files"]}
        for stamp in stamps:
            if type(stamp.get("size")) is not int or type(stamp.get("mtime_ns")) is not int:
                raise OwnedValueError("Model shards must be verified again")
            if stamp.get("sha256") != records[stamp["path"]]["sha256"]:
                raise OwnedValueError("Model shard checksum identity changed")
            validate_inference_path(stamp["path"], stamp.get("size"), stamp.get("mtime_ns"))
    main = next(f for f in manifest["files"] if str(ensure_safe_path(directory, f["path"])) == derived["model_path"])
    if selection.get("sha256") != main["sha256"]:
        raise ValueError("Selected model checksum identity changed")
    return derived


def activate_model(
    kind: str, repo_id: str, revision: str, mode: str = "vulkan"
) -> dict[str, Any]:
    """Activate a verified model for on-demand execution.
    Does NOT immediately start/load weights, but configures persistent settings.
    Managed chat identities retain independent runtimes and per-model modes.
    """
    if mode not in {"vulkan", "cpu"}:
        raise OwnedValueError("Mode must be vulkan or cpu")

    manifest, dir_path, derived = verify_and_get_model_assets(kind, repo_id, revision)

    if kind in {"llm", "vision"}:
        from local_mode.managed_chat import manager, model_identity
        identity = model_identity(kind, repo_id, revision)
        configured = manager.configure(identity, mode)
        return {
            "kind": kind,
            "configured": True,
            "running": configured["loaded"],
            **({"baseURL": configured["base_url"], "base_url": configured["base_url"]}
               if configured["base_url"] else {}),
            "modelId": identity,
            "model_id": identity,
            "repo_id": repo_id,
            "revision": revision,
            "context": 8192,
        }

    if kind == "tts":
        from local_mode.tts_models import settings_path
        from local_mode.storage import write_json
        family=derived.get("family","supertonic")
        if family=="piper" and mode!="cpu":
            raise OwnedValueError("Managed Piper voices require CPU mode.")
        model_path=Path(derived["model_path"])
        model_stat=model_path.stat()
        main=next(f for f in manifest["files"] if str(ensure_safe_path(dir_path,f["path"]))==str(model_path))
        selection={"repo_id":repo_id,"revision":revision,"kind":"tts","mode":mode,
            "model_path":str(model_path),"projector_path":"","sha256":main["sha256"],
            "model_size":model_stat.st_size,"model_mtime_ns":model_stat.st_mtime_ns}
        target=settings_path()
        if family=="piper":
            from local_mode.piper_models import settings_path as piper_settings_path
            target=piper_settings_path()
            config_path=Path(derived["config_path"]);config_stat=config_path.stat()
            config=next(f for f in manifest["files"] if str(ensure_safe_path(dir_path,f["path"]))==str(config_path))
            selection.update(family="piper",config_path=str(config_path),config_sha256=config["sha256"],config_size=config_stat.st_size,config_mtime_ns=config_stat.st_mtime_ns)
        write_json(target,selection)
        return {"kind":"tts","configured":True,"running":False,
                "repo_id":repo_id,"revision":revision,"family":family}

    if kind == "embedding":
        from local_mode import local_embeddings
        # Read GGUF metadata if available to detect embedding dimensions
        header = inspect_gguf_header(Path(derived["model_path"]))
        dimensions = header.get("embedding_length")
        if not isinstance(dimensions, int) or dimensions <= 0:
            dimensions = None  # No default fallback; infer from response

        # Find main file sha256 from manifest
        main_file = [f for f in manifest.get("files", []) if ensure_safe_path(dir_path, f["path"]) == Path(derived["model_path"])][0]
        model_hash = main_file["sha256"]

        model_st = os.lstat(derived["model_path"])
        path = local_embeddings.preferences_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")

        selection_info = {
            "backend": "local" if mode == "vulkan" else "cpu",
            "repo_id": repo_id,
            "revision": revision,
            "model_path": derived["model_path"],
            "projector_path": derived["projector_path"],
            "dimensions": dimensions,
            "sha256": model_hash,
            "mode": mode,
            "model_size": model_st.st_size,
            "model_mtime_ns": getattr(model_st, "st_mtime_ns", int(model_st.st_mtime * 1e9)),
        }
        if derived["projector_path"]:
            proj_st = os.lstat(derived["projector_path"])
            selection_info["projector_size"] = proj_st.st_size
            selection_info["projector_mtime_ns"] = getattr(proj_st, "st_mtime_ns", int(proj_st.st_mtime * 1e9))

        with local_embeddings._runner.lock:
            current_prefs = {}
            if path.is_file():
                try:
                    current_prefs = json.loads(path.read_text(encoding="utf8"))
                    if not isinstance(current_prefs, dict):
                        current_prefs = {}
                except (OSError, ValueError):
                    current_prefs = {}

            old_sel = current_prefs.get("selected_model")
            changed = (
                not isinstance(old_sel, dict)
                or old_sel.get("model_path") != derived["model_path"]
                or old_sel.get("projector_path") != derived["projector_path"]
                or old_sel.get("mode") != mode
            )
            if changed:
                local_embeddings._runner.close()

            current_prefs.update({
                "backend": "local" if mode == "vulkan" else "cpu",
                "selected_model": selection_info,
            })
            temporary.write_text(json.dumps(current_prefs), encoding="utf8")
            temporary.replace(path)

        running = (
            local_embeddings._runner.process is not None
            and local_embeddings._runner.process.poll() is None
        )
        return {
            "kind": "embedding",
            "configured": True,
            "running": running,
            "modelId": "jarvis-local",
            "repo_id": repo_id,
            "revision": revision,
            "context": 8192,
        }

    raise ValueError(f"Unsupported kind: {kind}")
