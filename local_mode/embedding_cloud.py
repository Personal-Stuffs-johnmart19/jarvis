"""Cancellable SDK calls in a hidden short-lived worker; no detached HTTP threads."""
from __future__ import annotations
import json
import subprocess
import sys
import time
from local_mode.config import ROOT
from local_mode import embedding_jobs

def call(method, params):
    from google.genai import types
    from google.genai.errors import APIError
    import httpx
    from local_mode.gemini import NetworkRouteError
    if method not in {"count_tokens", "embed_content"}:
        raise ValueError("Unsupported embedding worker method")
    embedding_jobs.check()
    payload = json.dumps({"method":method,"params":params}, default=lambda value: value.model_dump(mode="json", exclude_none=True)).encode("utf-8")
    if len(payload) > 24 * 1024 * 1024:
        raise ValueError("Embedding worker input exceeds 24 MiB")
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "local_mode.embedding_cloud_worker"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 240
    try:
        while True:
            embedding_jobs.check()
            if time.monotonic() >= deadline:
                raise TimeoutError("Embedding cloud request timed out")
            try:
                output, _ = process.communicate(input=payload, timeout=.05)
                break
            except subprocess.TimeoutExpired:
                payload = None
        embedding_jobs.check()
        if process.returncode or len(output) > 8 * 1024 * 1024:
            raise RuntimeError("Embedding cloud worker failed or returned too much data")
        reply = json.loads(output)
        error = reply.get("error")
        if error:
            kind, message = error.get("kind"), str(error.get("message", "Embedding cloud request failed"))
            if kind == "api":
                raise APIError(error["code"], error["details"])
            if kind == "transport":
                raise httpx.TransportError(message)
            if kind == "route":
                raise NetworkRouteError(message)
            if kind == "value":
                raise ValueError(message)
            raise RuntimeError(message)
        response = types.CountTokensResponse if method == "count_tokens" else types.EmbedContentResponse
        return response.model_validate(reply["result"])
    finally:
        if process.poll() is None:
            process.kill()
        # communicate reaps the process and joins Windows pipe threads even on Cancel.
        process.communicate(timeout=5)
