"""Shared storage for secretctl and its hooks.

Secrets live in one owner-only directory (0700), one file per secret (0600),
named by a hash of the secret's name. Nothing here prints values.

Every secret belongs to a scope. The global scope sits at the vault root and is
shared by every project. Each project gets its own folder under scopes/, keyed by
a hash of the project's identity (see project_scope). Lookups try the project
first and fall back to global, so a project can override a shared key.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
SCOPE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,63}$")
SCOPE_FILE = ".secretctl"
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


# ---------------------------------------------------------------- scopes

@dataclass(frozen=True)
class Scope:
    id: str = ""          # "" is the global scope; else "path:/abs/root" or "name:foo"
    label: str = "global"
    root: str = ""        # folder that defines the project, "" for global

    @property
    def is_global(self) -> bool:
        return not self.id

    def describe(self) -> str:
        return "global (shared by every project)" if self.is_global else f"project {self.label}"

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "root": self.root}

    @classmethod
    def from_dict(cls, d: dict) -> "Scope":
        return cls(d.get("id", ""), d.get("label", "global"), d.get("root", ""))


GLOBAL = Scope()


def _read_scope_file(path: str) -> str | None:
    """Scope name from a .secretctl file: first non-comment line, `scope = NAME` or just `NAME`."""
    try:
        with open(path) as f:
            text = f.read(4096)
    except OSError:
        return None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^scope\s*[:=]\s*(.+)$", line)
        name = (m.group(1) if m else line).strip().strip("\"'")
        return name if SCOPE_NAME_RE.match(name) else None
    return None


def _git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def _git_roots(cwd: str) -> tuple[str | None, str | None]:
    """(this worktree's top folder, the main checkout's top folder), or (None, None) outside git.

    Linked worktrees share the main checkout's .git, so all worktrees of a repo
    resolve to the same project.
    """
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return None, None
    top = os.path.realpath(top)
    main = top
    common = _git(cwd, "rev-parse", "--git-common-dir")
    if common:
        common = os.path.realpath(os.path.join(cwd, common))
        if os.path.basename(common) == ".git":  # not a submodule or bare repo
            main = os.path.dirname(common)
    return top, main


def project_scope(cwd: str | None = None) -> Scope:
    """The project that `cwd` belongs to, or GLOBAL if it isn't in one.

    1. The nearest .secretctl file, from cwd up to the git root (or up to $HOME
       outside git). It names the scope explicitly.
    2. In a linked worktree, a .secretctl in the main checkout.
    3. The git repo, identified by the main checkout's path.
    """
    cwd = os.path.realpath(cwd or os.getcwd())
    top, main = _git_roots(cwd)
    home = os.path.realpath(os.path.expanduser("~"))
    d = cwd
    while True:
        if not top and d == home:
            break
        name = _read_scope_file(os.path.join(d, SCOPE_FILE))
        if name:
            return Scope("name:" + name, name, d)
        parent = os.path.dirname(d)
        if d == top or parent == d:
            break
        d = parent
    if main and main != top:
        name = _read_scope_file(os.path.join(main, SCOPE_FILE))
        if name:
            return Scope("name:" + name, name, main)
    if main:
        return Scope("path:" + main, os.path.basename(main) or main, main)
    return GLOBAL


def chain(scope: Scope) -> list[Scope]:
    """Lookup order: the project, then global."""
    return [scope] if scope.is_global else [scope, GLOBAL]


def scope_dir(scope: Scope, create: bool = False) -> Path:
    base = vault_dir(create)
    if scope.is_global:
        return base
    p = base / "scopes" / hashlib.sha256(scope.id.encode()).hexdigest()[:16]
    if create:
        old = os.umask(0o077)
        try:
            (p / "pending").mkdir(parents=True, exist_ok=True)
        finally:
            os.umask(old)
        for q in (base / "scopes", p, p / "pending"):
            os.chmod(q, 0o700)
        meta = p / "scope.meta"
        if not meta.exists():
            _write_private(meta, scope.to_dict())
    return p


def all_scopes() -> list[Scope]:
    """Global first, then every project that has a folder in the vault."""
    found = []
    base = vault_dir(create=False)
    for meta in (base / "scopes").glob("*/scope.meta"):
        try:
            with open(meta) as f:
                found.append(Scope.from_dict(json.load(f)))
        except (OSError, ValueError):
            continue
    return [GLOBAL] + sorted(found, key=lambda s: (s.label.lower(), s.root))


# ---------------------------------------------------------------- secrets

def _fname(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:32] + ".json"


def secret_path(scope: Scope, name: str, create: bool = False) -> Path:
    return scope_dir(scope, create) / _fname(name)


def pending_path(scope: Scope, name: str, create: bool = False) -> Path:
    return scope_dir(scope, create) / "pending" / _fname(name)


def _write_private(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def save(scope: Scope, name: str, value: str, note: str = "") -> None:
    _write_private(
        secret_path(scope, name, create=True),
        {"name": name, "value": value, "note": note, "scope": scope.id, "created": int(time.time())},
    )


def load(scope: Scope, name: str) -> dict | None:
    try:
        with open(secret_path(scope, name)) as f:
            return json.load(f)
    except (FileNotFoundError, NotADirectoryError):
        return None


def exists(scope: Scope, name: str) -> bool:
    return secret_path(scope, name).exists()


def find(scope: Scope, name: str) -> tuple[dict | None, Scope | None]:
    """The entry `name` resolves to from `scope` (project first, then global), and where it was."""
    for s in chain(scope):
        e = load(s, name)
        if e:
            return e, s
    return None, None


def remove(scope: Scope, name: str) -> bool:
    try:
        secret_path(scope, name).unlink()
        return True
    except FileNotFoundError:
        return False


def entries(scope: Scope) -> list[dict]:
    out = []
    for p in scope_dir(scope).glob("*.json"):
        try:
            with open(p) as f:
                e = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(e, dict) and "value" in e:
            out.append(e)
    return sorted(out, key=lambda e: e.get("name", ""))


def all_entries() -> list[dict]:
    """Every secret in every scope. Used for redaction, which must cover all of them."""
    return [e for s in all_scopes() for e in entries(s)]


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
