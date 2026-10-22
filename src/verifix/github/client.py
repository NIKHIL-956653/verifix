"""
A small GitHub REST client: only the three calls verifix needs.

The token never appears in argv, URLs or logs:
- REST calls send it in the Authorization header (httpx2 client, not logged).
- git clone/push receive it through GIT_CONFIG_* environment variables as an
  `http.<github>.extraheader`, scoped to github.com only, so a repository whose remote
  points elsewhere never receives it.
"""

import base64
import re
from dataclasses import dataclass
from typing import Any

import httpx2

_URL = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def https_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_repo_url(url: str) -> RepoRef:
    """Only https://github.com/<owner>/<repo>[.git]. No ssh, no other hosts, no credentials."""
    m = _URL.match(url.strip())
    if not m or m.group("name") in (".", ".."):
        raise GitHubError("expected a URL like https://github.com/owner/repo")
    return RepoRef(m.group("owner"), m.group("name"))


def git_auth_env(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
    }


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        api_url: str = "https://api.github.com",
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout_s: float = 30,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "verifix",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx2.AsyncClient(
            base_url=api_url.rstrip("/"), headers=headers, timeout=timeout_s, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self._client.request(method, path, **kw)
        except httpx2.HTTPError as e:
            raise GitHubError(f"GitHub unreachable: {type(e).__name__}") from None
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", "")
            except ValueError:
                msg = ""
            if r.status_code == 401:
                msg = "token missing or rejected"
            raise GitHubError(f"GitHub {r.status_code}: {msg}"[:300], r.status_code)
        return r.json() if r.content else None

    async def get_repo(self, ref: RepoRef) -> dict[str, Any]:
        return await self._req("GET", f"/repos/{ref.owner}/{ref.name}")

    async def create_pull(
        self, ref: RepoRef, *, head: str, base: str, title: str, body: str, draft: bool = True
    ) -> dict[str, Any]:
        return await self._req(
            "POST",
            f"/repos/{ref.owner}/{ref.name}/pulls",
            json={"head": head, "base": base, "title": title, "body": body, "draft": draft},
        )

    async def check_runs(self, ref: RepoRef, sha_or_branch: str) -> list[dict[str, Any]]:
        data = await self._req(
            "GET", f"/repos/{ref.owner}/{ref.name}/commits/{sha_or_branch}/check-runs"
        )
        return [
            {
                "name": c.get("name"),
                "status": c.get("status"),
                "conclusion": c.get("conclusion"),
                "url": c.get("html_url"),
            }
            for c in (data or {}).get("check_runs", [])
        ]
