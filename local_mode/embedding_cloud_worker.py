"""One owned cloud SDK call, preserving Jarvis routing; stdout is bounded JSON only."""
from __future__ import annotations
import contextlib
import json
import os
import sys

MAX_INPUT = 24 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024

def execute(payload):
    from google.genai import types
    from local_mode import gemini
    method = payload["method"]
    if method not in {"count_tokens", "embed_content"}:
        raise ValueError("Unsupported embedding worker method")
    params = dict(payload["params"])
    contents = params["contents"]
    params["contents"] = [types.Content.model_validate(item) for item in contents] if isinstance(contents, list) else types.Content.model_validate(contents)
    config = types.CountTokensConfig if method == "count_tokens" else types.EmbedContentConfig
    params["config"] = config.model_validate(params["config"])
    with gemini.create_client(retry_attempts=0) as client:
        return getattr(client.models, method)(**params).model_dump(mode="json", exclude_none=True)

def main():
    output = sys.stdout.buffer
    with contextlib.redirect_stdout(sys.stderr):
        try:
            data = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(data) > MAX_INPUT:
                raise ValueError("Embedding worker input exceeds 24 MiB")
            reply = {"result": execute(json.loads(data))}
        except Exception as error:
            import httpx
            from google.genai.errors import APIError
            from local_mode import gemini
            detail = {"message": gemini.safe_error(error), "kind": "error"}
            if isinstance(error, APIError):
                detail.update(kind="api", code=error.code, details=error.details)
            elif isinstance(error, httpx.TransportError):
                detail["kind"] = "transport"
            elif isinstance(error, gemini.NetworkRouteError):
                detail["kind"] = "route"
            elif isinstance(error, ValueError):
                detail["kind"] = "value"
            reply = {"error": detail}
        encoded = json.dumps(reply, ensure_ascii=True)
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "JARVIS_GEMINI_API_KEY"):
            secret = os.environ.get(name)
            if secret:
                encoded = encoded.replace(secret, "[redacted]")
        data = encoded.encode("utf-8")
        if len(data) > MAX_OUTPUT:
            data = b'{"error":{"kind":"error","message":"Embedding worker response exceeds 8 MiB"}}'
        output.write(data)
        output.flush()

if __name__ == "__main__":
    main()
