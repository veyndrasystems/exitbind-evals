#!/usr/bin/env python3
"""Fetch one pinned repository into ignored cache without checking out source."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_common import load_json


def command(argv: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


parser = argparse.ArgumentParser()
parser.add_argument("--repo-id", required=True)
parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "corpus/manifest.json")
parser.add_argument("--cache-root", type=Path, default=Path(__file__).resolve().parents[1] / "corpus/cache")
args = parser.parse_args()
manifest = load_json(args.manifest)
entry = next((repo for repo in manifest["repositories"] if repo["id"] == args.repo_id), None)
if entry is None:
    parser.error(f"unknown repository: {args.repo_id}")
url, commit = entry["url"] + ".git", entry["commit"]
if not url.startswith("https://github.com/"):
    parser.error("only HTTPS GitHub sources are allowed")
cache_root = args.cache_root.resolve()
target = (cache_root / args.repo_id).resolve()
if target.parent != cache_root:
    parser.error("repository ID escaped cache root")
cache_root.mkdir(parents=True, exist_ok=True)
if target.exists():
    if not (target / ".git").is_dir():
        parser.error(f"refusing to use non-git cache path: {target}")
else:
    command(["git", "clone", "--no-checkout", "--filter=blob:none", "--no-tags", url, str(target)])
command(["git", "fetch", "--no-tags", "--depth", "1", "origin", commit], target)
actual = command(["git", "rev-parse", f"{commit}^{{commit}}"], target)
if actual != commit:
    raise SystemExit(f"pinned commit mismatch: expected {commit}, got {actual}")
print(f"verified {args.repo_id} at {commit} (source remains in ignored cache; no checkout performed)")
