"""Shared storage for secretctl and its hooks.

Secrets live in one owner-only directory (0700), one file per secret (0600),
named by a hash of the secret's name. Nothing here prints values.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
VAULT_DIRNAME = "secretctl-vault"
# Values shorter than this are not redacted from tool output (too many false hits).
MIN_REDACT_LEN = 8


def vault_dir(create: bool = True) -> Path:
    override = os.environ.get("SECRETCTL_VAULT")
    if override:
        p = Path(override).expanduser()
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        p = Path(base) / VAULT_DIRNAME
    if create:
        old = os.umask(0o077)
        try:
            (p / "pending").mkdir(parents=True, exist_ok=True)
        finally:
            os.umask(old)
        os.chmod(p, 0o700)
        os.chmod(p / "pending", 0o700)
    return p


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))


def _fname(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:32] + ".json"


def secret_path(name: str) -> Path:
    return vault_dir() / _fname(name)


def pending_path(name: str) -> Path:
    return vault_dir() / "pending" / _fname(name)


def _write_private(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def save(name: str, value: str, note: str = "") -> None:
    _write_private(
        secret_path(name),
        {"name": name, "value": value, "note": note, "created": int(time.time())},
    )


def load(name: str) -> dict | None:
    try:
        with open(secret_path(name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def exists(name: str) -> bool:
    return secret_path(name).exists()


def remove(name: str) -> bool:
    try:
        secret_path(name).unlink()
        return True
    except FileNotFoundError:
        return False


def all_entries() -> list[dict]:
    out = []
    d = vault_dir()
    for p in sorted(d.glob("*.json")):
        try:
            with open(p) as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda e: e.get("name", ""))


def redaction_pairs(entries: list[dict] | None = None) -> list[tuple[str, str]]:
    """(needle, replacement) pairs: raw value plus common encodings of it."""
    pairs = []
    for e in entries if entries is not None else all_entries():
        v = e.get("value") or ""
        if len(v) < MIN_REDACT_LEN:
            continue
        label = f"[secret:{e.get('name')}]"
        needles = {v}
        b = v.encode()
        needles.add(base64.b64encode(b).decode())
        needles.add(base64.b64encode(b).decode().rstrip("="))
        needles.add(base64.urlsafe_b64encode(b).decode().rstrip("="))
        needles.add(b.hex())
        for n in needles:
            if len(n) >= MIN_REDACT_LEN:
                pairs.append((n, label))
    # Longest first so a value inside another value doesn't leave fragments.
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def redact_text(text: str, pairs: list[tuple[str, str]]) -> str:
    for needle, label in pairs:
        if needle in text:
            text = text.replace(needle, label)
    return text
