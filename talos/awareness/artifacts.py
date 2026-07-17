"""Local artifact store (C15/RET-003, Phase 8).

Bytes live under ``{data_directory}/artifacts/`` with a **generated** rooted
path (``{uuid}/{sanitized-name}``) — no caller, tool, or model input ever
becomes a filesystem path component beyond a sanitized display name, and the
resolved path is verified to stay inside the artifact root. Metadata
(MIME/size/SHA-256/provenance/retention class) is authoritative in the
``artifacts`` table.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from talos.awareness.config import AwarenessSettings
from talos.awareness.db.models import Artifact

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_name(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", name.strip()).strip("._")
    return cleaned[:120] or "artifact"


class ArtifactStore:
    def __init__(self, engine: AsyncEngine, settings: AwarenessSettings) -> None:
        self._engine = engine
        self._root = (settings.data_directory / "artifacts").resolve()

    async def store(
        self,
        *,
        content: bytes,
        display_name: str,
        kind: str,
        mime_type: str | None = None,
        provenance: dict[str, Any] | None = None,
        retention_class: str | None = None,
    ) -> dict[str, Any]:
        artifact_id = uuid4()
        relative = f"{artifact_id}/{_sanitize_name(display_name)}"
        target = (self._root / relative).resolve()
        if not target.is_relative_to(self._root):  # defense in depth
            raise ValueError("resolved artifact path escaped the artifact root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

        digest = hashlib.sha256(content).hexdigest()
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.insert(Artifact).values(
                    artifact_id=artifact_id,
                    kind=kind,
                    display_name=display_name[:300],
                    relative_path=relative,
                    mime_type=mime_type,
                    size_bytes=len(content),
                    sha256=digest,
                    provenance=provenance or {},
                    retention_class=retention_class,
                )
            )
        return {
            "artifact_id": str(artifact_id),
            "relative_path": relative,
            "size_bytes": len(content),
            "sha256": digest,
        }

    async def load(self, artifact_id: UUID) -> tuple[dict[str, Any], bytes] | None:
        async with self._engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.select(Artifact).where(Artifact.artifact_id == artifact_id)
                )
            ).one_or_none()
        if row is None:
            return None
        path = (self._root / row.relative_path).resolve()
        if not path.is_relative_to(self._root) or not path.exists():
            return None
        content = path.read_bytes()
        metadata = {
            "artifact_id": str(row.artifact_id),
            "kind": row.kind,
            "display_name": row.display_name,
            "mime_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "sha256": row.sha256,
            "provenance": row.provenance,
            "retention_class": row.retention_class,
            "created_at": row.created_at.isoformat(),
            "checksum_ok": hashlib.sha256(content).hexdigest() == row.sha256,
        }
        return metadata, content
