#!/usr/bin/env python3
"""Decide where a Mac job runs: the org's self-hosted Macs, or a cloud Mac (ios_remote.py).

Source of truth: Michael-Dial/app-store-release, ci-fallback/ios_route.py. Every repository carries
a copy at .github/scripts/ios_route.py, written by ci-policy/sync.py; edit the source, not a copy.

Prints target=mac or target=fallback to $GITHUB_OUTPUT, and with fallback, backends=<the cloud
Macs set up, in the order ios_remote.py tries them>. The workflow runs the Mac job unless the
answer is a clear "fallback", so any doubt (no token, an API error, no matching runner visible,
no cloud Mac set up for this repository) sends the job to the Mac as it ran before the fallback
existed.

Fallback when a cloud Mac (Xcode Cloud, Codemagic or CircleCI; ios_remote.configured) is set up
for the repository and, among the self-hosted runners carrying every label in RUNNER_LABELS:
  - none is online (the Mac is off or asleep), or
  - REQUIRE_ALL_IDLE is true and any of them is busy. The Macs' runners share one simulator set
    and take turns on it, so a second idle runner is not a free slot.

It runs as a script in the repository rather than as a call to a reusable workflow in another
repository: GitHub fetches every such call before a run starts, even for a job whose `if:` is
false, and one it cannot read fails the whole workflow with no jobs and no log (HPC-Validation-Hub
and sleep-validation-hub, 2026-11-17). Policy rule 5.

Environment:
    RUNNER_STATUS_TOKEN  token that can list runners. The Mac runners are org-level, so this is a
                         fine-grained personal access token with resource owner Michael-Dial and the
                         organization permission Self-hosted runners: Read-only (org secret).
    RUNNER_LABELS        comma-separated, e.g. self-hosted,macOS,ARM64,ios
    REQUIRE_ALL_IDLE     true or false (default true)
    the cloud Macs' settings, as ios_remote.py names them; for secrets the workflow passes only
                         whether each is set

    GITHUB_REPOSITORY    owner/repo (set by Actions)
    GITHUB_API_URL       API root (set by Actions; overridable for tests)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ios_remote  # noqa: E402

API = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def output(target: str, why: str, backends: list[str] | None = None) -> None:
    print(f"target={target}: {why}")
    lines = f"target={target}\n" + (f"backends={','.join(backends)}\n" if backends else "")
    for name, line in (("GITHUB_OUTPUT", lines),
                       ("GITHUB_STEP_SUMMARY", f"Mac job routed to **{target}**: {why}\n")):
        path = os.environ.get(name)
        if path:
            with open(path, "a") as f:
                f.write(line)


def runners(path: str, token: str) -> list[dict] | None:
    """Every runner at this API path, or None when the token cannot see them."""
    found: list[dict] = []
    page = 1
    while True:
        req = urllib.request.Request(f"{API}{path}?per_page=100&page={page}")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.load(resp)
        except urllib.error.HTTPError as e:
            print(f"{path}: HTTP {e.code}")
            return None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            print(f"{path}: {e}")
            return None
        batch = body.get("runners", [])
        found += batch
        if len(found) >= body.get("total_count", 0) or not batch:
            return found
        page += 1


def main() -> None:
    ready = ios_remote.configured()
    if not ready:
        output("mac", "no cloud Mac is set up for this repository (Xcode Cloud, Codemagic or CircleCI)")
        return
    token = os.environ.get("RUNNER_STATUS_TOKEN", "")
    if not token:
        output("mac", "RUNNER_STATUS_TOKEN is not set, so runner status is unknown")
        return
    wanted = {l.strip().lower() for l in os.environ.get("RUNNER_LABELS", "").split(",") if l.strip()}
    all_idle = os.environ.get("REQUIRE_ALL_IDLE", "true").lower() == "true"
    repo = os.environ["GITHUB_REPOSITORY"]
    owner = repo.split("/")[0]

    # Runners registered on the repository, and on the organization when the owner is one.
    seen: dict[int, dict] = {}
    visible = False
    for path in (f"/repos/{repo}/actions/runners", f"/orgs/{owner}/actions/runners"):
        listed = runners(path, token)
        if listed is None:
            continue
        visible = True
        for r in listed:
            seen[r.get("id")] = r
    if not visible:
        output("mac", "the token could not list runners")
        return

    matching = [r for r in seen.values() if wanted <= {l.get("name", "").lower() for l in r.get("labels", [])}]
    if not matching:
        output("mac", f"no runner labelled {sorted(wanted)} is visible to the token")
        return
    for r in matching:
        print(f"  {r.get('name')}: {r.get('status')}{', busy' if r.get('busy') else ''}")
    online = [r for r in matching if r.get("status") == "online"]
    if not online:
        output("fallback", "the Mac's runners are all offline", ready)
    elif all_idle and any(r.get("busy") for r in online):
        output("fallback", "a Mac runner is busy, and the Macs' runners share one simulator set", ready)
    elif not all_idle and all(r.get("busy") for r in online):
        output("fallback", "every Mac runner is busy", ready)
    else:
        output("mac", "the Mac is online and free")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # Any surprise sends the job to the Mac, never nowhere.
        print(f"::warning::iOS routing failed ({e}); sending the job to the Mac")
        output("mac", "routing failed")
        sys.exit(0)
