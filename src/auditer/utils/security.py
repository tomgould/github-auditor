from __future__ import annotations

import re

# Matches all known GitHub token formats
TOKEN_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),       # Classic PAT
    re.compile(r"github_pat_[A-Za-z0-9_]{80,}"),  # Fine-grained PAT
    re.compile(r"gho_[A-Za-z0-9]{36,}"),        # OAuth access token
    re.compile(r"ghs_[A-Za-z0-9]{36,}"),        # GitHub App installation token
    re.compile(r"ghu_[A-Za-z0-9]{36,}"),        # GitHub App user-to-server token
    re.compile(r"ghr_[A-Za-z0-9]{36,}"),        # GitHub App refresh token
]

# Combined pattern for any GitHub token
_COMBINED_PATTERN = re.compile(
    r"(ghp_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{80,}|"
    r"gho_[A-Za-z0-9]{36,}|ghs_[A-Za-z0-9]{36,}|"
    r"ghu_[A-Za-z0-9]{36,}|ghr_[A-Za-z0-9]{36,})"
)

# Pattern for tokens embedded in URLs (oauth2:token@github.com)
_URL_TOKEN_PATTERN = re.compile(r"(https?://)([^@:]+:[^@]+)@(github\.com)")


def mask_token(token: str | None) -> str:
    if not token:
        return "(none)"
    if len(token) <= 8:
        return "****"
    return f"****{token[-4:]}"


def sanitise_url(url: str) -> str:
    return _URL_TOKEN_PATTERN.sub(r"\1****@\3", url)


def sanitise_git_output(text: str) -> str:
    result = _COMBINED_PATTERN.sub(lambda m: mask_token(m.group(0)), text)
    result = _URL_TOKEN_PATTERN.sub(r"\1****@\3", result)
    return result


def contains_token(text: str) -> bool:
    return bool(_COMBINED_PATTERN.search(text))
