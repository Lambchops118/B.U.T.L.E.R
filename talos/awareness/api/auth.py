"""API write authorization (SEC-002, Phase 8).

The API binds to loopback by default; state-changing endpoints additionally
require the configured bearer token (``TALOS_AWARENESS_API_TOKEN``) when one
is set. Physical-action endpoints use their own stricter fail-closed check
(no token configured ⇒ actions disabled); the write check here covers
non-physical mutations (memory writes/deletion, alert lifecycle, outbox
retry) and permits loopback-trusted operation when no token is configured —
that stance is documented, and setting the token upgrades every mutation to
authenticated in one move.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request


async def require_write_auth(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    configured = request.app.state.settings.api_token
    if configured is None:
        return  # loopback-trusted mode (documented); actions remain fail-closed
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        supplied, configured.get_secret_value()
    ):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
