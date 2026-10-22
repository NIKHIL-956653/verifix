"""
The path jail: every file path the agent or the API names goes through here first.

A path is accepted only if, after resolving every symlink, it is still inside the
repository root and it is not on the secret deny-list. Both the path as written and the
path it resolves to are checked, so a harmless-looking symlink (`docs/config -> ../.env`)
cannot be used to read a secret.

The deny-list is applied even to files Git tracks: a committed `.env` or private key is
still a secret, and the agent has no business reading it.
"""

import fnmatch
import re
from pathlib import Path, PurePosixPath


class PathRejected(ValueError):
    """A path was refused. `reason` is safe to show to the user and to the model."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path!r}: {reason}")
        self.path = path
        self.reason = reason


# Example/template env files are documentation, not secrets.
_ENV_ALLOWED = {".env.example", ".env.sample", ".env.template", ".env.dist", ".env.defaults"}

_DENY_NAME_GLOBS = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*.ppk",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "_netrc",
    ".git-credentials",
    "*.tfstate",
    "*.tfstate.*",
    "credentials",
    "credentials.json",
    "secrets.*",
    "secret.*",
    "service-account*.json",
    ".htpasswd",
)

_DENY_DIRS = {".git", ".ssh", ".aws", ".gnupg", ".docker", ".azure", ".kube"}

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def deny_reason(rel_posix: str) -> str | None:
    """Why this repo-relative path is off-limits, or None if it is readable."""
    parts = [p for p in rel_posix.split("/") if p]
    if not parts:
        return None
    for d in parts[:-1]:
        if d.lower() in _DENY_DIRS:
            return f"inside a protected directory ({d}/)"
    name = parts[-1]
    lower = name.lower()
    if lower in _DENY_DIRS:
        return f"protected directory ({name})"
    if lower.startswith(".env") and lower not in _ENV_ALLOWED:
        return "secret file (.env)"
    for pattern in _DENY_NAME_GLOBS:
        if fnmatch.fnmatch(lower, pattern):
            return f"secret file ({pattern})"
    return None


def normalise(rel: str) -> str:
    """Forward slashes, no leading './'. Does not resolve anything."""
    rel = rel.replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


class RepoJail:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve(strict=True)

    def resolve(self, rel: str) -> tuple[Path, str]:
        """Return (absolute real path, repo-relative posix path) or raise PathRejected."""
        if not isinstance(rel, str) or not rel.strip():
            raise PathRejected(str(rel), "empty path")
        if "\x00" in rel:
            raise PathRejected(rel, "NUL byte in path")
        norm = normalise(rel)
        if norm.startswith("/") or _WINDOWS_DRIVE.match(norm) or norm.startswith("//"):
            raise PathRejected(
                rel, "absolute paths are not allowed; use a path relative to the repository root"
            )
        parts = PurePosixPath(norm).parts
        if ".." in parts:
            raise PathRejected(rel, "'..' is not allowed")
        reason = deny_reason(norm)
        if reason:
            raise PathRejected(rel, reason)

        candidate = self.root.joinpath(*parts)
        real = candidate.resolve(strict=False)
        if real != self.root and not real.is_relative_to(self.root):
            raise PathRejected(rel, "resolves outside the repository (symlink escape)")
        rel_real = real.relative_to(self.root).as_posix() if real != self.root else ""
        reason = deny_reason(rel_real)
        if reason:
            raise PathRejected(rel, f"resolves to a {reason}")
        return real, rel_real or "."

    def is_allowed(self, rel: str) -> bool:
        try:
            self.resolve(rel)
            return True
        except PathRejected:
            return False
