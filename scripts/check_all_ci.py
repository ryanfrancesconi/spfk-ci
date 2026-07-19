#!/usr/bin/env python3
"""Check all — verify fresh-checkout CI is green for every package with an
unpushed local tag, BEFORE running "push tags".

Run after "tag all" (local tags created) but before "push tags" (tags made
public). At this point tag refs aren't on the remote yet, so this dispatches
each package's ci.yml (workflow_dispatch) against the remote branch that
already holds the about-to-be-tagged commit -- requires "push development" /
"push to main" to have landed that commit first.

Dependencies between changed packages are read directly from each
Package.swift (not hardcoded), and checks run in dependency order (upstream
first). If a package depends on another package that *also* has an unpushed
tag in this round, its CI run can only resolve that dependency's OLD
published tag -- the new code isn't visible to anyone outside this checkout
yet. That case is flagged rather than silently reported as a full pass.

Usage: python3 check_all_ci.py
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

PACKAGES_DIR = Path("/Users/rf/Documents/Dev/Spongefork/workspace/spfk-packages")
ORG = "ryanfrancesconi"
SKIP = {"_spfk-packages"}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def latest_tag(repo: Path) -> str | None:
    out = run(["git", "-C", str(repo), "tag", "-l", "--sort=-v:refname"]).stdout.split()
    return out[0] if out else None


def tag_pushed(repo: Path, tag: str) -> bool:
    out = run(["git", "-C", str(repo), "ls-remote", "--tags", "origin", f"refs/tags/{tag}"]).stdout
    return bool(out.strip())


def remote_branch_for(repo: Path, sha: str) -> str | None:
    out = run(["git", "-C", str(repo), "branch", "-r", "--contains", sha]).stdout
    for candidate in ("main", "development"):
        if f"origin/{candidate}" in out:
            return candidate
    return None


def spfk_deps(repo: Path) -> set[str]:
    pkg_swift = repo / "Package.swift"
    if not pkg_swift.exists():
        return set()
    text = pkg_swift.read_text()
    return set(re.findall(rf"github\.com/{re.escape(ORG)}/([\w.-]+)", text))


def topological_order(deps: dict[str, set[str]]) -> list[str]:
    remaining = {name: set(d) for name, d in deps.items()}
    order = []
    while remaining:
        ready = sorted(name for name, d in remaining.items() if not d)
        if not ready:
            print(f"warning: dependency cycle among {sorted(remaining)}, breaking arbitrarily", file=sys.stderr)
            ready = sorted(remaining)
        order.extend(ready)
        for name in ready:
            del remaining[name]
        for d in remaining.values():
            d.difference_update(ready)
    return order


def main():
    repos = {
        p.name: p
        for p in PACKAGES_DIR.iterdir()
        if p.is_dir() and p.name not in SKIP and (p / ".git").exists()
    }

    changed = {}
    for name, repo in repos.items():
        tag = latest_tag(repo)
        if not tag or tag_pushed(repo, tag):
            continue
        sha = run(["git", "-C", str(repo), "rev-list", "-n1", tag]).stdout.strip()
        branch = remote_branch_for(repo, sha)
        changed[name] = {"tag": tag, "sha": sha, "branch": branch}

    if not changed:
        print("No unpushed tags found -- nothing to check.")
        return

    deps = {name: spfk_deps(repos[name]) & changed.keys() for name in changed}
    order = topological_order(deps)

    print("=== Check order (upstream first) ===")
    for name in order:
        info = changed[name]
        upstream_unpushed = deps[name]
        flag = ""
        if upstream_unpushed:
            flag = f"  [WARNING: depends on unpushed {', '.join(sorted(upstream_unpushed))} -- this run validates against their OLD published tags]"
        print(f"{name} -> {info['tag']} ({info['branch'] or 'NOT ON REMOTE'}){flag}")

    print("\n=== Dispatching CI ===")
    for name in order:
        info = changed[name]
        if not info["branch"]:
            print(f"{name} | {info['tag']} ({info['sha']}) not found on any pushed remote branch -- push development/main first")
            continue
        print(f"{name} | dispatching CI for {info['tag']} on {info['branch']}")
        run(["gh", "workflow", "run", "ci.yml", "--repo", f"{ORG}/{name}", "--ref", info["branch"]])

    print("\n=== Waiting for results ===")
    for name in order:
        info = changed[name]
        if not info["branch"]:
            continue

        run_id = None
        for _ in range(5):
            out = run([
                "gh", "run", "list", "--repo", f"{ORG}/{name}", "--workflow=ci.yml",
                "--limit", "1", "--json", "databaseId,event",
            ])
            try:
                data = json.loads(out.stdout)
            except json.JSONDecodeError:
                data = []
            hits = [d["databaseId"] for d in data if d.get("event") == "workflow_dispatch"]
            if hits:
                run_id = hits[0]
                break
            time.sleep(3)

        if run_id is None:
            print(f"{name} | could not find the dispatched run")
            continue

        watch = run(["gh", "run", "watch", str(run_id), "--repo", f"{ORG}/{name}", "--exit-status"])
        if watch.returncode == 0:
            print(f"{name} | PASS")
        else:
            print(f"{name} | FAIL -- gh run view {run_id} --repo {ORG}/{name} --log-failed")


if __name__ == "__main__":
    main()
