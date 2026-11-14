"""Explicit text/file embeddings with input validation before provider calls."""

from __future__ import annotations

import io
import math
from pathlib import Path

from google.genai import types
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential
from local_mode import gemini
from local_mode.api_usage import track_call
from local_mode.config import ROOT
from local_mode import embedding_jobs
from local_mode.messages import OwnedValueError
from local_mode.file_policy import checked_open, opened_path

MODEL = "gemini-embedding-2"
MAX_BYTES = 12 * 1024 * 1024
MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".pdf": "application/pdf",
}


def attachment(path: str) -> types.Part:
    with checked_open(path) as source:
        candidate = opened_path(source)
        if (ROOT / "config").resolve() in candidate.parents:
            raise OwnedValueError("Credential and service settings files cannot be sent.")
        data = source.read(MAX_BYTES + 1)
    return attachment_data(candidate, data)


def attachment_data(candidate: Path, data: bytes) -> types.Part:
    """Validate a pinned snapshot supplied by an internal checked reader."""
    if (
        any(item.casefold() in {".git", "credentials"} for item in candidate.parts)
        or candidate == ROOT / ".env"
        or (ROOT / "config").resolve() in candidate.parents
    ):
        raise OwnedValueError("Credential and service settings files cannot be sent.")
    media_type = MIME.get(candidate.suffix.lower())
    if media_type is None:
        raise OwnedValueError("Select PNG/JPEG, WAV/MP3, MP4/MOV or PDF.")
    if not data or len(data) > MAX_BYTES:
        raise OwnedValueError("One file must contain 1 byte to 12 MiB.")
    if media_type.startswith("image/"):
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            if image.format != ("PNG" if media_type == "image/png" else "JPEG"):
                raise OwnedValueError("The image format does not match PNG/JPEG.")
            image.verify()
    elif media_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 6:
            raise OwnedValueError("Select an unencrypted PDF with 1 to 6 pages.")
    else:
        import av

        with av.open(io.BytesIO(data)) as container:
            duration = (
                container.duration / av.time_base
                if container.duration is not None
                else None
            )
            stream_type = "audio" if media_type.startswith("audio/") else "video"
            streams = list(getattr(container.streams, stream_type))
            limit = 180 if stream_type == "audio" else 120
            if not streams or duration is None or not 0 < duration <= limit:
                raise OwnedValueError(
                    "Select {0} with a known duration up to {1} seconds.", stream_type, limit
                )
            if stream_type == "video" and streams[0].codec_context.name not in {
                "h264",
                "hevc",
                "av1",
                "vp9",
            }:
                raise OwnedValueError("Supported video codecs: H264, H265, AV1 or VP9.")
    return types.Part.from_bytes(data=data, mime_type=media_type)


def _cloud_call(client, method, **params):
    embedding_jobs.check()
    if embedding_jobs.event() is None:
        return getattr(client.models, method)(**params)
    from local_mode.embedding_cloud import call
    return call(method, params)


def _retry_wait(state):
    delay = wait_exponential(multiplier=1, min=2, max=10)(state)
    token = embedding_jobs.event()
    if token is None:
        return delay
    token.wait(delay)
    embedding_jobs.check()
    return 0


@retry(
    stop=stop_after_attempt(5),
    wait=_retry_wait,
    retry=retry_if_exception(gemini.is_transient),
    reraise=True,
)
@track_call("count")
def count_input(client, model: str, contents):
    return _cloud_call(client, "count_tokens",
        model=model,
        contents=contents,
        config=types.CountTokensConfig(
            http_options=types.HttpOptions(
                timeout=10000, retry_options=types.HttpRetryOptions(attempts=1)
            )
        ),
    )


@retry(
    stop=stop_after_attempt(5),
    wait=_retry_wait,
    retry=retry_if_exception(gemini.is_transient),
    reraise=True,
)
@track_call("embed")
def embed_input(client, model: str, contents):
    return _cloud_call(client, "embed_content",
        model=model,
        contents=contents,
        config=types.EmbedContentConfig(
            output_dimensionality=768,
            http_options=types.HttpOptions(
                timeout=15000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        ),
    )


def embed(text: str = "", path: str = "", *, file_part=None) -> dict:
    if (
        not isinstance(text, str)
        or len(text) > 12000
        or not isinstance(path, str)
        or len(path) > 2048
    ):
        raise OwnedValueError("Text is limited to 12000 characters and one path to 2048 characters.")
    parts = [types.Part.from_text(text=text.strip())] if text.strip() else []
    if path.strip():
        parts.append(file_part if file_part is not None else attachment(path.strip()))
    if not parts:
        raise OwnedValueError("Enter text or select a file for embedding.")
    contents = types.Content(role="user", parts=parts)
    with gemini.create_client(retry_attempts=0) as client:
        counted = count_input(client, MODEL, contents)
        tokens = counted.total_tokens
        if type(tokens) is not int or not 0 < tokens <= 8192:
            raise OwnedValueError(
                "Embedding input must fit within 8192 tokens; split the content."
            )
        response = embed_input(client, MODEL, contents)
    values = (
        response.embeddings[0].values
        if response.embeddings and len(response.embeddings) == 1
        else None
    )
    if (
        not values
        or len(values) != 768
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
    ):
        raise OwnedValueError("Embedding API did not return a valid 768-dimensional vector.")
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm <= 0:
        raise OwnedValueError("Embedding API returned a zero vector.")
    return {
        "model": MODEL,
        "dimensions": len(values),
        "input_tokens": tokens,
        "vector": [v / norm for v in values],
    }


def selected(text: str = "", path: str = "", backend: str | None = None, *, file_part=None) -> dict:
    from local_mode.local_embeddings import preferences, embed as local_embed
    if not isinstance(text, str) or len(text) > 12000 or not isinstance(path, str) or len(path) > 2048:
        raise OwnedValueError("Text is limited to 12000 characters and one path to 2048 characters.")
    backend = preferences()["backend"] if backend is None else backend
    if backend not in {"auto", "cloud", "local", "cpu"}:
        raise OwnedValueError("Unknown embedding mode.")
    # Cloud retries and local fallback must consume the same approved snapshot.
    if path and file_part is None:
        file_part = attachment(path.strip())
    pinned = {"file_part": file_part} if file_part is not None else {}
    if backend in {"cloud", "auto"}:
        try:
            return embed(text, path, **pinned)
        except Exception as error:
            embedding_jobs.check()
            if backend != "auto" or not gemini.local_fallback_allowed(error):
                raise
            result = local_embed(text, path, "local", **pinned)
            result["fallback_reason"] = gemini.safe_error(error)
            return result
    return local_embed(text, path, backend, **pinned)

CLOUD_BATCH_SIZE = 64


def batch_embed(texts: list[str]) -> list[dict]:
    """Cloud embedding with real batch transport and separate Content objects."""
    if not isinstance(texts, list):
        raise OwnedValueError("Expected a list of texts")
    if not texts:
        return []
    for t in texts:
        if not isinstance(t, str) or len(t) > 12000:
            raise OwnedValueError("Each text must be a string up to 12000 characters")

    results: list[dict] = []
    for i in range(0, len(texts), CLOUD_BATCH_SIZE):
        embedding_jobs.check()
        sub_batch = texts[i : i + CLOUD_BATCH_SIZE]
        contents = [
            types.Content(parts=[types.Part.from_text(text=t)])
            for t in sub_batch
        ]
        with gemini.create_client(retry_attempts=0) as client:
            response = embed_input(client, MODEL, contents)

        embeddings = getattr(response, "embeddings", None)
        if not embeddings or len(embeddings) != len(sub_batch):
            raise OwnedValueError(
                "Embedding API returned {0} vectors, expected {1}", len(embeddings) if embeddings else 0, len(sub_batch)
            )

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        if prompt_tokens is None and usage:
            prompt_tokens = getattr(usage, "total_input_tokens", None)

        base_tokens = (prompt_tokens // len(sub_batch)) if isinstance(prompt_tokens, int) else None
        remainder_tokens = (prompt_tokens % len(sub_batch)) if isinstance(prompt_tokens, int) else 0

        for idx, item in enumerate(embeddings):
            values = getattr(item, "values", None)
            if (
                not values
                or len(values) != 768
                or any(
                    type(v) not in (int, float) or not math.isfinite(v)
                    for v in values
                )
            ):
                raise OwnedValueError("Embedding API did not return a valid 768-dimensional vector")
            norm = math.sqrt(sum(v * v for v in values))
            if not math.isfinite(norm) or norm <= 0:
                raise OwnedValueError("Embedding API returned a zero vector")
            item_tokens = (base_tokens + (1 if idx < remainder_tokens else 0)) if base_tokens is not None else None
            results.append(
                {
                    "model": MODEL,
                    "dimensions": len(values),
                    "input_tokens": item_tokens,
                    "vector": [v / norm for v in values],
                }
            )
    return results


def batch_selected(texts: list[str], backend: str | None = None) -> list[dict]:
    """Embed a batch of texts using real cloud batching or honest local fallback."""
    if not texts:
        return []
    from local_mode.local_embeddings import preferences, embed as local_embed
    backend = preferences()["backend"] if backend is None else backend
    if backend not in {"auto", "cloud", "local", "cpu"}:
        raise OwnedValueError("Unknown embedding mode")
    if backend in {"cloud", "auto"}:
        try:
            return batch_embed(texts)
        except Exception as error:
            embedding_jobs.check()
            if backend != "auto" or not gemini.local_fallback_allowed(error):
                raise
            reason = gemini.safe_error(error)
            results = []
            for t in texts:
                res = local_embed(t, "", "local")
                res["fallback_reason"] = reason
                results.append(res)
            return results
    return [local_embed(t, "", backend) for t in texts]
