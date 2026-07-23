"""Validates and parses a manifest.json downloaded from a peer's Drive subfolder.

A manifest is untrusted input once it comes from someone else's app instance, so it's
schema-validated before anything touches the local cache.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import jsonschema

CURRENT_SCHEMA_VERSION = 2


class ManifestValidationError(Exception):
    """A downloaded manifest.json is malformed or from an unsupported schema version."""


def _load_schema() -> dict:
    schema_text = resources.files("white_soapstone.schema").joinpath("manifest.schema.json").read_text(
        encoding="utf-8"
    )
    return json.loads(schema_text)


_SCHEMA = None


def _schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = _load_schema()
    return _SCHEMA


def _migrate(manifest: dict) -> dict:
    """Upgrades older schema_version payloads to the current shape.

    No migration path exists yet - v1 predates any real external users of this app,
    so the jump to v2 (content-addressed track ids for cross-user dedup) is treated as
    a clean break rather than something worth writing a compatibility shim for. A
    manifest at an unsupported version is rejected outright; the fix is just re-syncing.
    """
    version = manifest.get("schema_version")
    if version != CURRENT_SCHEMA_VERSION:
        raise ManifestValidationError(
            f"Unsupported manifest schema_version {version!r}; this app understands "
            f"{CURRENT_SCHEMA_VERSION}. The peer may be running a newer or older app version."
        )
    return manifest


def parse_manifest(raw_bytes: bytes) -> dict:
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(f"manifest.json is not valid JSON: {exc}") from exc

    try:
        jsonschema.validate(data, _schema())
    except jsonschema.ValidationError as exc:
        raise ManifestValidationError(f"manifest.json failed schema validation: {exc.message}") from exc

    return _migrate(data)


def parse_manifest_file(path: str | Path) -> dict:
    return parse_manifest(Path(path).read_bytes())
