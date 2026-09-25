"""Twilio SMS transport: config, webhook signature validation, outbound send.

Stdlib only; the twilio SDK is not a dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Sequence

from butler.config import env_bool, env_int, load_environment
from butler.phone.provider import is_e164_number


TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
# Twilio concatenates long messages up to 1600 characters; stay under it.
TWILIO_MAX_BODY_CHARS = 1600


@dataclass(frozen=True)
class SmsConfig:
    enabled: bool
    host: str
    port: int
    public_url: str
    account_sid: str
    auth_token: str
    from_number: str
    allowed_senders: tuple[str, ...]
    reply_timeout: float
    job_follow_up_timeout: float
    max_reply_chars: int

    @classmethod
    def from_env(cls) -> "SmsConfig":
        load_environment()
        return cls(
            enabled=env_bool("BUTLER_SMS_ENABLED", False),
            host=os.getenv("BUTLER_SMS_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=env_int("BUTLER_SMS_PORT", 8430),
            public_url=os.getenv("BUTLER_SMS_PUBLIC_URL", "").strip(),
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", "").strip(),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", "").strip(),
            from_number=os.getenv("BUTLER_SMS_FROM_NUMBER", "").strip(),
            allowed_senders=tuple(_parse_senders(os.getenv("BUTLER_SMS_ALLOWED_SENDERS", ""))),
            reply_timeout=float(os.getenv("BUTLER_SMS_REPLY_TIMEOUT", "300")),
            job_follow_up_timeout=float(os.getenv("BUTLER_SMS_JOB_FOLLOW_UP_TIMEOUT", "1800")),
            max_reply_chars=min(TWILIO_MAX_BODY_CHARS, max(160, env_int("BUTLER_SMS_MAX_REPLY_CHARS", 1500))),
        )

    def missing_requirements(self) -> list[str]:
        """Settings without which the webhook must not be exposed."""
        missing: list[str] = []
        if not self.public_url:
            missing.append("BUTLER_SMS_PUBLIC_URL")
        if not self.account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.auth_token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not is_e164_number(self.from_number):
            missing.append("BUTLER_SMS_FROM_NUMBER (E.164)")
        if not self.allowed_senders:
            missing.append("BUTLER_SMS_ALLOWED_SENDERS")
        return missing


def compute_signature(auth_token: str, url: str, params: Mapping[str, Sequence[str]]) -> str:
    """Twilio's X-Twilio-Signature for a form-encoded POST.

    HMAC-SHA1 over the full webhook URL followed by every POST parameter name
    and value, sorted by name, base64 encoded.
    """
    payload = url
    for key in sorted(params):
        for value in sorted(params[key]):
            payload += f"{key}{value}"
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def is_valid_signature(
    auth_token: str,
    url: str,
    params: Mapping[str, Sequence[str]],
    signature: str,
) -> bool:
    if not auth_token or not signature:
        return False
    expected = compute_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature.strip())


def send_sms(config: SmsConfig, to_number: str, body: str) -> str:
    """Send one SMS through the Twilio Messages API; returns the message SID."""
    text = fit_sms_body(body, config.max_reply_chars)
    url = f"{TWILIO_API_BASE}/Accounts/{urllib.parse.quote(config.account_sid)}/Messages.json"
    data = urllib.parse.urlencode({"To": to_number, "From": config.from_number, "Body": text}).encode("utf-8")
    credentials = base64.b64encode(f"{config.account_sid}:{config.auth_token}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Twilio send failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Twilio send failed: {exc.reason}") from exc
    parsed = json.loads(raw) if raw else {}
    return str(parsed.get("sid") or "")


def fit_sms_body(text: str, limit: int) -> str:
    cleaned = str(text or "").strip() or "(no response)"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _parse_senders(raw_value: str) -> list[str]:
    if not raw_value.strip():
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception as exc:
        raise RuntimeError("BUTLER_SMS_ALLOWED_SENDERS must be a JSON array of E.164 numbers.") from exc
    if not isinstance(parsed, list):
        raise RuntimeError("BUTLER_SMS_ALLOWED_SENDERS must be a JSON array of E.164 numbers.")
    senders: list[str] = []
    for item in parsed:
        number = str(item or "").strip()
        if not is_e164_number(number):
            raise RuntimeError(f"BUTLER_SMS_ALLOWED_SENDERS entry '{number}' is not a valid E.164 number.")
        senders.append(number)
    return senders
