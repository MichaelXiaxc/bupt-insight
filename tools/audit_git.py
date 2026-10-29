"""Audit every reachable Git blob and optionally the staged tree. No secret output."""

from __future__ import annotations

import argparse
import json
import re
import subprocess

from core.config import ROOT


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def forbidden(path: str) -> bool:
    p = path.replace("\\", "/").lower()
    return (
        any(part in (".local", ".session", ".venv", "secrets", "__pycache__", ".test-output") for part in p.split("/"))
        or p == "config.yaml"
        or p.endswith((".sqlite3", ".sqlite", ".db", ".log", ".bin", ".enc", ".key", ".state.json"))
        or p.startswith("data/")
        or p.endswith("profile.yaml")
    )


def audit(staged: bool = False) -> dict:
    objects: dict[str, set] = {}
    commits = git("rev-list", "--all").decode().splitlines()
    # Enumerate each tree, not just rev-list's one path per object: the same blob
    # could have been stored at a private path in an earlier commit.
    for commit in commits:
        for entry in git("ls-tree", "-r", "-z", commit).decode("utf-8").split("\0"):
            if not entry:
                continue
            meta, path = entry.split("\t", 1)
            if meta.split()[1] == "blob":
                objects.setdefault(meta.split()[2], set()).add(path)
    if staged:
        for line in git("ls-files", "--stage", "-z").decode("utf-8").split("\0"):
            if not line:
                continue
            meta, path = line.split("\t", 1)
            objects.setdefault(meta.split()[1], set()).add(path)

    violations = []
    manual_review = []
    token_patterns = [
        rb"gh[pousr]_[A-Za-z0-9]{30,}",
        rb"github_pat_[A-Za-z0-9_]{40,}",
        rb"sk-[A-Za-z0-9_-]{25,}",
        rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
    ]
    for oid, paths in objects.items():
        raw = git("cat-file", "blob", oid)
        for path in sorted(paths):
            if forbidden(path):
                violations.append({"path": path, "reason": "private_path", "object": oid})
            if any(re.search(pattern, raw) for pattern in token_patterns):
                violations.append({"path": path, "reason": "credential_pattern", "object": oid})
        # Flag plausible non-placeholder webhook config for manual review; never
        # include its value in the report.
        if re.search(rb"https://open\.feishu\.cn/open-apis/bot/v2/hook/[a-f0-9-]{30,}", raw):
            manual_review.append({"paths": sorted(paths), "reason": "webhook_candidate", "object": oid})
    return {
        "commits": int(git("rev-list", "--all", "--count")),
        "blobs": len(objects),
        "staged_included": staged,
        "violations": violations,
        "manual_review": manual_review,
        "passed": not violations and not manual_review,
        "limits": "精确路径与常见密钥格式检查不能替代人工检查；不判定所有正文或个人信息。",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true")
    args = parser.parse_args()
    result = audit(args.staged)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
