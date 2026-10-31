"""Small GitHub REST adapter. Publication is explicit; no credentials are logged."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from rendering import MARKER


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token, *, opener=None):
        if not token:
            raise ValueError("Set GITHUB_TOKEN before explicitly requesting publication")
        self._token = token
        self._open = opener or urllib.request.urlopen

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request("https://api.github.com" + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self._token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "CS5351-TestPilot"})
        try:
            with self._open(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Do not echo the response body, request headers or transient URLs.
            raise GitHubError(f"GitHub {method} failed (HTTP {exc.code})") from None
        except (OSError, ValueError) as exc:
            raise GitHubError(f"GitHub {method} failed ({type(exc).__name__})") from None

    def publish(self, repo, number, body, *, expected_head):
        """Upsert only our own marked comment, after checking PR/head identity."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(part in {".", ".."} for part in repo.split("/")) or type(number) is not int or number <= 0:
            raise ValueError("Expected owner/repository and a positive PR number")
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", expected_head or ""):
            raise ValueError("Publishing requires the analysed full head SHA")
        if not body.startswith(MARKER) or len(body) > 65000:
            raise ValueError("Invalid or oversized TestPilot comment")
        pr = self.request("GET", f"/repos/{repo}/pulls/{number}")
        if pr.get("head", {}).get("sha") != expected_head:
            raise GitHubError("PR head changed; refusing to publish a stale report")
        if pr.get("state") != "open":
            raise GitHubError("PR is no longer open")
        if pr.get("base", {}).get("repo", {}).get("full_name", "").lower() != repo.lower():
            raise GitHubError("PR repository does not match the report target")
        try:
            identity = self.request("GET", "/user")["login"]
        except GitHubError as exc:
            if "HTTP 403" not in str(exc) or os.environ.get("GITHUB_ACTIONS") != "true":
                raise
            # GitHub Actions installation tokens cannot always call /user.
            identity = "github-actions[bot]"
        existing = None
        for page in range(1, 101):
            comments = self.request("GET", f"/repos/{repo}/issues/{number}/comments?per_page=100&page={page}")
            if not isinstance(comments, list):
                raise GitHubError("Unexpected comment-list response")
            for comment in comments:
                if comment.get("user", {}).get("login") == identity and str(comment.get("body", "")).startswith(MARKER):
                    existing = comment
            if len(comments) < 100:
                break
        else:
            raise GitHubError("Comment pagination limit reached; no comment was written")
        # Check once more immediately before the write to reduce the stale-run race.
        latest = self.request("GET", f"/repos/{repo}/pulls/{number}")
        if latest.get("head", {}).get("sha") != expected_head:
            raise GitHubError("PR head changed during publication")
        if existing and existing.get("body") == body:
            return {"action": "unchanged", "id": existing["id"]}
        if existing:
            result = self.request("PATCH", f"/repos/{repo}/issues/comments/{int(existing['id'])}", {"body": body})
            return {"action": "updated", "id": result["id"]}
        result = self.request("POST", f"/repos/{repo}/issues/{number}/comments", {"body": body})
        return {"action": "created", "id": result["id"]}

