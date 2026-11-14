"""Pinned local llama.cpp embeddings with explicit Vulkan or CPU execution."""
from __future__ import annotations

import atexit
import base64
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import stat
import tempfile
import threading
import time
import zipfile

import requests
from local_mode.config import ROOT
from local_mode.downloads import download_file, check_cancelled, cancellable_lock
from local_mode.processes import run
from local_mode import embedding_jobs
from local_mode.messages import OwnedValueError, OwnedRuntimeError

REVISION = "58faf90940886df2d62be555c8765d00dac362c5"
BUILD = "b11778"
MODEL = "embeddinggemma-2-Q8_0.gguf"
PROJECTOR = "mmproj-embeddinggemma-2-Q8_0.gguf"
ASSETS = {
    MODEL: (309855456, "2188ac1deca4b77dffefd603c2776a9d76d9d74ec01841392982ebb840b09135"),
    PROJECTOR: (554821024, "c4a8a52691ecef40618438928bdf9e68379b854e24166f292592353db0aab64f"),
}
CACHE = ROOT / "models" / "embeddinggemma-2"
RUNTIME_SHA = "5ba5a036ad1716300f1958c7a22803f8cd361fa18f2decf8c5955dbc5ee5958a"
_RUNTIME_INSTALL_LOCK = threading.Lock()


def preferences_path() -> Path:
    folder = ROOT / (".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST") == "1" else "data")
    return folder / "embedding-settings.json"


def chunk_configuration(size=1500, overlap=200) -> dict:
    if type(size) is not int or not 256 <= size <= 8192:
        raise OwnedValueError("Chunk size must be an integer from 256 to 8192 characters.")
    if type(overlap) is not int or not 0 <= overlap < size:
        raise OwnedValueError("Chunk overlap must be a non-negative integer smaller than chunk size.")
    return {"chunk_size": size, "chunk_overlap": overlap}


def preferences(backend: str | None = None, reset_model: bool = False, *, chunk_size=None, chunk_overlap=None) -> dict:
    path = preferences_path()
    if backend is None and not reset_model and chunk_size is None and chunk_overlap is None:
        try:
            value = json.loads(path.read_text(encoding="utf8"))
            if isinstance(value, dict):
                b = value.get("backend")
                result = {"backend": b if b in {"auto", "cloud", "local", "cpu"} else "auto"}
                if "selected_model" in value and isinstance(value["selected_model"], dict):
                    result["selected_model"] = value["selected_model"]
                try:
                    result.update(chunk_configuration(value.get("chunk_size", 1500), value.get("chunk_overlap", 200)))
                except OwnedValueError:
                    result.update(chunk_configuration())
                return result
        except (OSError, ValueError):
            pass
        return {"backend": "auto", **chunk_configuration()}
    if backend is not None and backend not in {"auto", "cloud", "local", "cpu"}:
        raise OwnedValueError("Unknown embedding mode.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with _runner.lock:
        data = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf8"))
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
        if chunk_size is not None or chunk_overlap is not None:
            current = preferences()
            data.update(chunk_configuration(
                current["chunk_size"] if chunk_size is None else chunk_size,
                current["chunk_overlap"] if chunk_overlap is None else chunk_overlap,
            ))
        if reset_model:
            _runner.close()
            data.pop("selected_model", None)
        if backend is not None:
            data["backend"] = backend
        temporary.write_text(json.dumps(data), encoding="utf8")
        temporary.replace(path)
    return preferences()


def _runtime_ready(runtime: Path) -> bool:
    for path in (runtime, runtime / ".installed", runtime / "llama-server.exe"):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise OwnedValueError("Runtime symlinks and reparse points are refused")
    marker = runtime / ".installed"
    if not marker.is_file() or not (runtime / "llama-server.exe").is_file():
        return False
    with marker.open(encoding="ascii") as stream:
        return stream.read(128).strip() == RUNTIME_SHA


def ensure_runtime(cancel_event=None) -> Path:
    """Ensure pinned llama.cpp runtime executable is installed without downloading default weights."""
    if os.name != "nt":
        raise OwnedRuntimeError("Automatic local runtime installation currently supports Windows x64.")
    import platform
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise OwnedRuntimeError("Windows x64 is required; ARM64 runtime is not yet supported.")
    with cancellable_lock(_RUNTIME_INSTALL_LOCK, cancel_event):
        runtime = CACHE / BUILD
        verified = CACHE / f"{BUILD}-verified-{RUNTIME_SHA[:12]}"
        for installed in (runtime, verified):
            check_cancelled(cancel_event)
            if _runtime_ready(installed):
                return installed / "llama-server.exe"
        # Preserve an older incomplete installation; never recursively delete a
        # computed runtime directory or replace a running installation in place.
        if runtime.exists():
            runtime = verified
        if runtime.exists():
            raise OwnedRuntimeError("Incomplete pinned runtime exists; inspect it before reinstalling")
        archive = CACHE / f"{BUILD}.zip"
        download_file(
            f"https://github.com/ggml-org/llama.cpp/releases/download/{BUILD}/llama-{BUILD}-bin-win-vulkan-x64.zip",
            archive, max_bytes=100_000_000, sha256=RUNTIME_SHA, cancel_event=cancel_event,
        )
        check_cancelled(cancel_event)
        # Only this freshly created staging directory is cleaned on failure.
        # Publishing a complete directory is one rename; no partial marker leaks.
        with tempfile.TemporaryDirectory(prefix=f".{BUILD}-", dir=CACHE) as staging:
            staged = Path(staging) / "runtime"
            staged.mkdir()
            with zipfile.ZipFile(archive) as source:
                members = source.infolist()
                names = set()
                total = 0
                if len(members) > 128:
                    raise OwnedValueError("Too many files in llama.cpp runtime archive")
                for member in members:
                    check_cancelled(cancel_event)
                    name = member.filename
                    total += member.file_size
                    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
                    if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,240}", name) or name in {".", "..", ".installed"}
                            or name.endswith(".") or name.split(".", 1)[0].upper() in reserved
                            or name.casefold() in names or member.file_size > 150_000_000
                            or total > 500_000_000 or member.is_dir()
                            or stat.S_ISLNK(member.external_attr >> 16)):
                        raise OwnedValueError("Invalid llama.cpp archive.")
                    names.add(name.casefold())
                if "llama-server.exe" not in names:
                    raise OwnedValueError("llama-server.exe missing from pinned runtime archive")
                for member in members:
                    with source.open(member) as incoming, (staged / member.filename).open("xb") as output:
                        while chunk := incoming.read(64 * 1024):
                            check_cancelled(cancel_event)
                            output.write(chunk)
            check_cancelled(cancel_event)
            (staged / ".installed").write_text(RUNTIME_SHA, encoding="ascii")
            check_cancelled(cancel_event)
            staged.rename(runtime)
        return runtime / "llama-server.exe"


def assets() -> tuple[Path, Path, Path]:
    executable = ensure_runtime(cancel_event=embedding_jobs.event())
    for name, (size, checksum) in ASSETS.items():
        download_file(
            f"https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/resolve/{REVISION}/{name}",
            CACHE / name, max_bytes=size, sha256=checksum, cancel_event=embedding_jobs.event(),
        )
    return executable, CACHE / MODEL, CACHE / PROJECTOR



def media_parts(text: str, path: str, *, file_part=None) -> list[dict]:
    from local_mode.embeddings import attachment
    parts = [{"type": "text", "text": text.strip()}] if text.strip() else []
    if not path.strip():
        return parts
    item = (file_part if file_part is not None else attachment(path.strip())).inline_data
    data, mime = item.data, item.mime_type
    def image_part(raw: bytes, kind: str = "image/jpeg") -> dict:
        return {"type": "image_url", "image_url": {"url": f"data:{kind};base64," + base64.b64encode(raw).decode("ascii")}}
    if mime.startswith("image/"):
        parts.append(image_part(data, mime))
    elif mime.startswith("audio/"):
        # WAV PCM — поддерживаемый mtmd формат, MP3 декодируем локально через PyAV.
        import av
        import wave
        output = io.BytesIO()
        with av.open(io.BytesIO(data)) as source, wave.open(output, "wb") as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
            converter = av.AudioResampler(format="s16", layout="mono", rate=16000)
            for frame in source.decode(audio=0):
                for converted in converter.resample(frame):
                    target.writeframes(converted.to_ndarray().tobytes())
            for converted in converter.resample(None):
                target.writeframes(converted.to_ndarray().tobytes())
        parts.append({"type": "input_audio", "input_audio": {"data": base64.b64encode(output.getvalue()).decode("ascii"), "format": "wav"}})
    elif mime.startswith("video/"):
        import av
        # До 16 равномерных кадров, без внешнего ffmpeg и без аудиодорожки.
        with av.open(io.BytesIO(data)) as source:
            duration = source.duration / av.time_base
            next_time = 0.0
            interval = max(duration / 16, 0.1)
            count = 0
            for frame in source.decode(video=0):
                stamp = frame.time
                if stamp is not None and stamp >= next_time:
                    image = frame.to_image(); image.thumbnail((768, 768))
                    output = io.BytesIO(); image.save(output, format="JPEG")
                    parts.append(image_part(output.getvalue()))
                    count += 1; next_time += interval
                    if count == 16:
                        break
            if not count:
                raise OwnedValueError("Could not read video frames.")
    else:
        from local_mode.documents import pdf_text
        extracted = pdf_text(data)
        if len(extracted) + len(text) > 24000:
            raise OwnedValueError("PDF text is too large; split the document.")
        parts.append({"type": "text", "text": extracted})
    return parts


def _probe(executable):
    if embedding_jobs.event() is None:
        return run([str(executable), "--list-devices"], capture_output=True, text=True, encoding="utf8", errors="replace", timeout=15)
    process = subprocess.Popen([str(executable), "--list-devices"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 15
    try:
        while True:
            embedding_jobs.check()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, 15)
            try:
                stdout, stderr = process.communicate(timeout=.05)
                return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdout: process.stdout.close()
        if process.stderr: process.stderr.close()


class LocalEmbedding:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.process: subprocess.Popen | None = None
        self.session = requests.Session()
        self.session.trust_env = False  # localhost не должен уходить в системный прокси.
        self.url = ""
        self.device = ""
        self.mode = ""
        self.active_model_path = ""
        self.active_projector_path = ""
        self.active_identity = ""
        self.active_dimension: int | None = None
        self.log = None

    def close(self) -> None:
        with self.lock:
            process, self.process = self.process, None
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            if self.log:
                self.log.close(); self.log = None
            self.mode = ""
            self.active_model_path = ""
            self.active_projector_path = ""
            self.active_identity = ""
            self.active_dimension = None

    def _start(self, executable: Path, model: Path, projector: Path | None, device: str) -> None:
        self.close()
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0)); port = reserve.getsockname()[1]
        key = secrets.token_urlsafe(32)
        self.url = f"http://127.0.0.1:{port}"
        self.session.headers["Authorization"] = f"Bearer {key}"
        flags = ["--device", device, "--n-gpu-layers", "99"] if device != "none" else [
            "--device", "none", "--n-gpu-layers", "0", "--no-mmproj-offload", "--no-op-offload", "--no-kv-offload"]
        CACHE.mkdir(parents=True, exist_ok=True)
        self.log = (CACHE / "server.log").open("wb")
        cmd = [
            str(executable), "--model", str(model), "--alias", "jarvis-embedding",
            "--embeddings", "--ctx-size", "8192", "--batch-size", "8192", "--ubatch-size", "8192",
            "--parallel", "1", "--host", "127.0.0.1", "--port", str(port),
            "--api-key", key, "--no-webui", "--no-warmup", *flags,
        ]
        if projector and str(projector):
            cmd.extend(["--mmproj", str(projector)])
        self.process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            embedding_jobs.check()
            if self.process.poll() is not None:
                raise OwnedRuntimeError("llama.cpp exited during startup; see models/embeddinggemma-2/server.log.")
            try:
                if self.session.get(self.url + "/health", timeout=1).status_code == 200:
                    self.device = device
                    return
            except requests.RequestException:
                pass
            time.sleep(0.2)
        raise OwnedRuntimeError("llama.cpp did not start within 60 seconds.")

    def _request(self, parts: list[dict], model_name: str = "embeddinggemma-2:Q8_0", expected_dim: int | None = 768) -> dict:
        payload = {"model": "jarvis-embedding", "input": [{"content": parts}], "encoding_format": "float"}
        if embedding_jobs.event() is None:
            response = self.session.post(self.url + "/v1/embeddings", json=payload, timeout=(3, 90))
        else:
            async def call():
                import httpx
                async with httpx.AsyncClient(trust_env=False, headers=dict(self.session.headers), timeout=httpx.Timeout(90, connect=3)) as client:
                    try:
                        return await client.post(self.url + "/v1/embeddings", json=payload)
                    except httpx.HTTPError as error:
                        raise requests.RequestException(str(error)) from error
            response = embedding_jobs.run_async(call)
        if response.status_code != 200:
            raise OwnedRuntimeError("Local embeddings returned HTTP {0}. Split input above 8192 tokens; see server.log.", response.status_code)
        body = response.json()
        values = body["data"][0]["embedding"]
        dim = len(values)
        if (expected_dim is not None and dim != expected_dim) or not 1 <= dim <= 65536 or any(type(v) not in (float, int) or not math.isfinite(v) for v in values):
            raise OwnedValueError("Invalid local embedding vector.")
        norm = math.hypot(*values)
        if not math.isfinite(norm) or norm <= 0:
            raise OwnedValueError("Local embedding returned a zero or invalid vector.")
        return {"model": model_name, "dimensions": dim,
                "device": self.device, "input_tokens": body.get("usage", {}).get("prompt_tokens"),
                "vector": [v / norm for v in values]}

    def embed(self, text: str, path: str, mode: str, *, file_part=None) -> dict:
        if mode not in {"local", "cpu"} or not isinstance(text, str) or len(text) > 12000 or not isinstance(path, str) or len(path) > 2048:
            raise OwnedValueError("Invalid local embedding request.")

        # Check current preferences to see if a custom verified model was selected
        prefs = preferences()
        selected = prefs.get("selected_model")

        # If media is provided (non-empty path) and model has no projector, reject clearly
        if path.strip() and selected and not selected.get("projector_path"):
            raise OwnedValueError("Selected embedding model is text-only; media requires an embedding model with vision/projector support.")

        parts = media_parts(text, path, **({"file_part": file_part} if file_part is not None else {}))
        if not parts:
            raise OwnedValueError("Enter text or select a file.")

        # Determine target model files and identity
        if selected:
            from local_mode.model_library import validate_selected_model
            validate_selected_model(selected)
            target_model = Path(selected["model_path"])
            target_proj = Path(selected["projector_path"]) if selected.get("projector_path") else None
            repo = selected.get("repo_id", "")
            rev = selected.get("revision", "")
            sha = selected.get("sha256", "")[:12]
            target_identity = f"{repo}@{rev}:{sha}" if repo else str(target_model)
            expected_dim = selected.get("dimensions")
        else:
            target_identity = "embeddinggemma-2:Q8_0"
            expected_dim = 768

        with cancellable_lock(self.lock, embedding_jobs.event()):
            embedding_jobs.check()
            # If model selection changed while process is running, close existing process
            if self.process is not None and self.process.poll() is None:
                if self.active_identity != target_identity:
                    self.close()

            if self.mode == mode and self.process is not None and self.process.poll() is None and self.active_identity == target_identity:
                try:
                    return self._request(parts, model_name=target_identity, expected_dim=expected_dim)
                except Exception:
                    self.close()
                    embedding_jobs.check()
                    raise

            if selected:
                executable = ensure_runtime(cancel_event=embedding_jobs.event())
                model = target_model
                projector = target_proj
            else:
                executable, model, projector = assets()

            devices = ["none"]
            if mode == "local":
                try:
                    probe = _probe(executable)
                    devices = re.findall(r"^\s*(Vulkan\d+):", probe.stdout + probe.stderr, re.MULTILINE)
                except (OSError, subprocess.SubprocessError) as error:
                    embedding_jobs.check()
                    raise OwnedRuntimeError("Could not discover Vulkan embedding devices: {0}", str(error)) from error
                embedding_jobs.check()
                if not devices:
                    raise OwnedRuntimeError("No Vulkan embedding device is available. Select CPU explicitly to use CPU embeddings.")
            for index, device in enumerate(devices):
                embedding_jobs.check()
                try:
                    self._start(executable, model, projector, device)
                except Exception:
                    self.close()
                    embedding_jobs.check()
                    if index == len(devices) - 1:
                        raise
                    continue
                try:
                    result = self._request(parts, model_name=target_identity, expected_dim=expected_dim)
                    self.mode = mode
                    self.active_identity = target_identity
                    self.active_dimension = result["dimensions"]
                    self.active_model_path = str(model)
                    self.active_projector_path = str(projector) if projector else ""
                    return result
                except Exception:
                    self.close()
                    embedding_jobs.check()
                    raise
            raise OwnedRuntimeError("Could not start local embeddings.")


_runner = LocalEmbedding()
atexit.register(_runner.close)


def embed(text: str = "", path: str = "", backend: str = "local", *, file_part=None) -> dict:
    return _runner.embed(text, path, backend, **({"file_part": file_part} if file_part is not None else {}))
