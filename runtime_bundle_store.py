"""Server-side storage for runtime function bundles.

The Discord attachment contains only an artifact URL and a short-lived token.
This module is used by the bot, so MONGODB_URI never crosses into the Lua
output or the public website response headers. The website reads the same
MongoDB database/collection with its own server-side credentials.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone

try:
    from pymongo import ASCENDING, MongoClient
except ImportError:  # Keep compiler-only/local use independent of Mongo extras.
    ASCENDING = 1
    MongoClient = None

logger = logging.getLogger(__name__)

# The website already uses the vadrifts database. Keep runtime delivery in
# its own collection, with an override for a separately scoped DB if desired.
_DB_NAME = (os.environ.get("OBF_RUNTIME_BUNDLE_DB") or "vadrifts").strip()
_COLLECTION_NAME = (
    os.environ.get("OBF_RUNTIME_BUNDLE_COLLECTION") or "obf_runtime_bundles"
).strip()
_MAX_BUNDLE_BYTES = 15_000_000
_DEFAULT_TTL_SECONDS = 24 * 60 * 60

_client = None
_collection = None
_init_attempted = False
_init_lock = threading.Lock()


def _runtime_ttl_seconds() -> int:
    try:
        value = int(os.environ.get("OBF_RUNTIME_BUNDLE_TTL_SECONDS", "") or _DEFAULT_TTL_SECONDS)
    except ValueError:
        value = _DEFAULT_TTL_SECONDS
    return min(max(value, 300), 30 * 24 * 60 * 60)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _get_collection():
    global _client, _collection, _init_attempted

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
        if not uri or MongoClient is None:
            logger.warning(
                "Remote runtime bundle storage is unavailable: MONGODB_URI or pymongo is missing."
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
            _collection.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)
            _collection.create_index("created_at")
            logger.info(
                "Remote runtime bundle storage enabled: %s.%s",
                _DB_NAME,
                _COLLECTION_NAME,
            )
            return _collection
        except Exception as exc:
            logger.warning("Could not initialize remote runtime bundle storage: %s", exc)
            _client = None
            _collection = None
            return None


def store_runtime_bundle(
    bundle: str,
    *,
    capability: str | None = None,
    challenge_secret: str | None = None,
    artifact_id: str | None = None,
    user_id=None,
    source_label: str | None = None,
    engine_version: str = "Kryos v16.2",
):
    """Store one bundle and return its locator, or ``None`` on failure.

    ``capability`` is supplied by the VM-hidden capability mode. The optional
    ``challenge_secret`` is used only by the experimental nonce/HMAC mode and
    remains server-side. The legacy remote mode generates its own access token
    when this is omitted.
    """
    if not isinstance(bundle, str) or not bundle.strip().startswith("return {"):
        logger.warning("Refusing to store an invalid runtime bundle")
        return None

    bundle_bytes = len(bundle.encode("utf-8"))
    if bundle_bytes > _MAX_BUNDLE_BYTES:
        logger.warning("Refusing oversized runtime bundle: %s bytes", bundle_bytes)
        return None

    collection = _get_collection()
    if collection is None:
        return None

    if challenge_secret is not None:
        if not isinstance(challenge_secret, str) or not 32 <= len(challenge_secret) <= 256:
            logger.warning("Refusing an invalid challenge secret")
            return None
        if capability is None:
            capability = challenge_secret

    artifact_id = artifact_id or secrets.token_urlsafe(18)
    access_token = capability or secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_runtime_ttl_seconds())
    document = {
        "_id": artifact_id,
        "kind": "kryos_runtime_bundle",
        "capability_sha256": _sha256_text(access_token),
        "bundle": bundle,
        "bundle_sha256": _sha256_text(bundle),
        "bundle_bytes": bundle_bytes,
        "created_at": now,
        "expires_at": expires_at,
    }
    if challenge_secret is not None:
        # Experimental challenge mode keeps the verifier secret server-side.
        # It is never returned to the website client or placed in the output.
        document["challenge_secret"] = challenge_secret
    elif capability is None:
        # Legacy token-URL mode keeps its optional observability metadata.
        document.update({
            "token_sha256": _sha256_text(access_token),
            "user_id": str(user_id) if user_id is not None else None,
            "source_label": (source_label or "")[:255],
            "engine_version": engine_version,
            "access_count": 0,
        })

    try:
        collection.insert_one(document)
    except Exception as exc:
        logger.warning("Failed to store remote runtime bundle: %s", exc)
        return None

    logger.info(
        "Stored remote runtime bundle id=%s bytes=%s expires=%s",
        artifact_id,
        bundle_bytes,
        expires_at.isoformat(),
    )
    return {
        "artifact_id": artifact_id,
        "access_token": access_token,
        "capability": access_token,
        "expires_at": expires_at,
        "bundle_sha256": document["bundle_sha256"],
    }


def revoke_runtime_bundle(artifact_id: str) -> bool | None:
    """Delete one artifact; return None when storage is unavailable."""
    if not artifact_id or len(str(artifact_id)) > 128:
        return False
    collection = _get_collection()
    if collection is None:
        return None
    try:
        result = collection.delete_one({"_id": str(artifact_id)})
        return bool(getattr(result, "deleted_count", 0))
    except Exception as exc:
        logger.warning("Failed to revoke runtime bundle %s: %s", str(artifact_id)[:12], exc)
        return None
