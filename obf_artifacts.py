"""Optional server-side persistence for obfuscation artifacts.

This module is deliberately storage-only. It does not expose a route, return
source to a client, or change the obfuscator's runtime output. By default it
stores nothing. Enable it on the bot with:

    OBF_ARTIFACTS_ENABLED=true

If retaining the full submitted source and generated output is desired, also
set:

    OBF_STORE_ARTIFACT_CONTENT=true

The MongoDB credential remains server-side in MONGODB_URI. The collection is
separate from the existing key collections so artifact retention can be
managed independently.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
from datetime import datetime, timezone

from pymongo import ASCENDING, MongoClient

logger = logging.getLogger(__name__)

_DB_NAME = (os.environ.get("OBF_ARTIFACTS_DB") or "vadrifts_bots").strip()
_COLLECTION_NAME = (
    os.environ.get("OBF_ARTIFACTS_COLLECTION") or "obf_artifacts"
).strip()
# Leave headroom below MongoDB's 16 MiB BSON document limit when full content
# retention is enabled and a bundle is present.
_CONTENT_LIMIT_BYTES = 15_000_000

_client = None
_collection = None
_init_lock = threading.Lock()
_init_attempted = False


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    """Whether optional artifact persistence is enabled."""
    return _env_bool("OBF_ARTIFACTS_ENABLED", False)


def store_content_enabled() -> bool:
    """Whether full source/output text should be retained."""
    return _env_bool("OBF_STORE_ARTIFACT_CONTENT", False)


def _get_collection():
    global _client, _collection, _init_attempted

    if not enabled():
        return None
    if _collection is not None:
        return _collection
    if _init_attempted:
        return None

    with _init_lock:
        if _collection is not None:
            return _collection
        if _init_attempted:
            return None
        _init_attempted = True

        uri = (os.environ.get("MONGODB_URI") or "").strip()
        if not uri:
            logger.warning(
                "OBF_ARTIFACTS_ENABLED is true but MONGODB_URI is not set; "
                "artifact persistence is disabled."
            )
            return None

        try:
            _client = MongoClient(
                uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=10000,
            )
            _client.admin.command("ping")
            _collection = _client[_DB_NAME][_COLLECTION_NAME]
            _collection.create_index([("created_at", ASCENDING)])
            _collection.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
            _collection.create_index("source_sha256")
            logger.info(
                "Obfuscation artifact persistence enabled: %s.%s",
                _DB_NAME,
                _COLLECTION_NAME,
            )
            return _collection
        except Exception as exc:
            logger.warning("Could not initialize obfuscation artifact storage: %s", exc)
            _client = None
            _collection = None
            return None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_obfuscation(
    source: str,
    output: str,
    *,
    bundle: str | None = None,
    user_id=None,
    source_label: str | None = None,
    elapsed_seconds: float | None = None,
    engine_version: str = "Kryos v16.2",
):
    """Persist metadata, and optionally content, for one successful run.

    Any database failure is intentionally non-fatal: an artifact write must
    never make an otherwise successful obfuscation fail.

    Returns the generated artifact ID, or ``None`` when disabled/unavailable.
    """
    collection = _get_collection()
    if collection is None:
        return None

    source = source if isinstance(source, str) else str(source)
    output = output if isinstance(output, str) else str(output)
    bundle = bundle if isinstance(bundle, str) else (str(bundle) if bundle is not None else "")
    artifact_id = secrets.token_urlsafe(18)

    document = {
        "_id": artifact_id,
        "kind": "obfuscation",
        "created_at": datetime.now(timezone.utc),
        "engine_version": engine_version,
        "user_id": str(user_id) if user_id is not None else None,
        "source_label": (source_label or "")[:255],
        "source_bytes": len(source.encode("utf-8")),
        "output_bytes": len(output.encode("utf-8")),
        "bundle_bytes": len(bundle.encode("utf-8")) if bundle else 0,
        "source_sha256": _sha256_text(source),
        "output_sha256": _sha256_text(output),
        "bundle_sha256": _sha256_text(bundle) if bundle else None,
    }
    if elapsed_seconds is not None:
        document["elapsed_ms"] = round(float(elapsed_seconds) * 1000, 3)

    content_requested = store_content_enabled()
    content_bytes = (
        len(source.encode("utf-8"))
        + len(output.encode("utf-8"))
        + len(bundle.encode("utf-8"))
    )
    document["content_stored"] = False
    if content_requested and content_bytes <= _CONTENT_LIMIT_BYTES:
        document["source"] = source
        document["output"] = output
        if bundle:
            document["bundle"] = bundle
        document["content_stored"] = True
    elif content_requested:
        logger.warning(
            "Full obfuscation artifact content skipped because it is too large "
            "(%s bytes)",
            content_bytes,
        )

    try:
        collection.insert_one(document)
        logger.info(
            "Stored obfuscation artifact id=%s source_bytes=%s output_bytes=%s bundle_bytes=%s content=%s",
            artifact_id,
            document["source_bytes"],
            document["output_bytes"],
            document["bundle_bytes"],
            document["content_stored"],
        )
        return artifact_id
    except Exception as exc:
        logger.warning("Failed to store obfuscation artifact: %s", exc)
        return None


def get_artifact(artifact_id: str):
    """Internal/server-side lookup helper; no HTTP route is registered."""
    collection = _get_collection()
    if collection is None or not artifact_id:
        return None
    try:
        return collection.find_one({"_id": str(artifact_id)})
    except Exception as exc:
        logger.warning("Failed to read obfuscation artifact: %s", exc)
        return None
