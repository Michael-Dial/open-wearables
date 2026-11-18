#!/usr/bin/env python3
"""Run a CircleCI pipeline from GitHub Actions and wait for it.

Source of truth: Michael-Dial/app-store-release, ci-fallback/circleci_run.py. Every repository carries
a copy at .github/scripts/circleci_run.py, written by ci-policy/sync.py; edit the source, not a copy.

A workflow's fallback job uses this when ios_route.py answers "fallback" (the self-hosted Macs are
busy or offline): it starts the repository's pipeline (.circleci/config.yml) on the same branch and
fails or passes with it. A script in the repository, not a call to another repository's reusable
workflow, for the reason in ios_route.py (policy rule 5).

    circleci_run.py start   # prints pipeline_id=... to $GITHUB_OUTPUT
    circleci_run.py wait    # polls until every workflow in the pipeline has finished
    circleci_run.py cancel  # cancels the pipeline's running workflows (GitHub run was cancelled)

Configuration comes from the environment:
    CIRCLE_TOKEN     personal API token (org secret CIRCLECI_TOKEN)
    PROJECT_SLUG     e.g. circleci/<org-id>/<project-id> (repository variable CIRCLECI_PROJECT_SLUG)
    DEFINITION_ID    the pipeline definition, CircleCI → Project settings → Pipelines
                     (repository variable CIRCLECI_DEFINITION_ID)
    BRANCH           branch to build and to read the config from
    PARAMETERS       JSON object of pipeline parameters, e.g. {"run_mac": true}
    PIPELINE_ID      for wait and cancel
    TIMEOUT_MINUTES  for wait (default 60)
    CIRCLECI_API     API root (default https://circleci.com/api/v2), overridable for tests
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("CIRCLECI_API", "https://circleci.com/api/v2").rstrip("/")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "30"))
# CircleCI needs a moment before GET /pipeline/{id}/workflow knows about a pipeline POSTed
# by /pipeline/run; a too-early GET gets HTTP 404 "Pipeline run not found".
STARTUP_GRACE_SECONDS = float(os.environ.get("CIRCLECI_STARTUP_GRACE_SECONDS", "120"))


class NotFound(Exception):
    """The pipeline id was not (yet) resolvable."""


# Workflow states, from CircleCI's API reference for GET /pipeline/{id}/workflow.
RUNNING = {"running", "on_hold", "failing"}
PASSED = {"success"}


def request(method: str, path: str, body: dict | None = None, *, not_found_ok: bool = False) -> dict:
    token = os.environ.get("CIRCLE_TOKEN", "")
    if not token:
        sys.exit("::error::CIRCLECI_TOKEN is not set, so the CircleCI fallback cannot start.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Circle-Token", token)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        # A pipeline POSTed seconds ago can 404 on /pipeline/{id}/workflow until CircleCI's
        # eventual consistency settles; wait() polls through that window instead of dying.
        if not_found_ok and e.code == 404:
            raise NotFound(f"HTTP 404 on {path}") from e
        detail = e.read().decode(errors="replace")[:500]
        sys.exit(f"::error::CircleCI {method} {path} returned HTTP {e.code}: {detail}")
    return json.loads(text) if text else {}


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")
    print(f"{name}={value}")


def start() -> None:
    slug = os.environ["PROJECT_SLUG"]
    branch = os.environ["BRANCH"]
    body = {
        "definition_id": os.environ["DEFINITION_ID"],
        "config": {"branch": branch},
        "checkout": {"branch": branch},
        "parameters": json.loads(os.environ.get("PARAMETERS") or "{}"),
    }
    pipeline = request("POST", f"/project/{slug}/pipeline/run", body)
    pid = pipeline.get("id")
    if not pid:
        sys.exit(f"::error::CircleCI did not return a pipeline id: {pipeline}")
    print(f"Started CircleCI pipeline {pipeline.get('number', '?')} on {branch}")
    output("pipeline_id", pid)


def workflows(pid: str) -> list[dict]:
    items: list[dict] = []
    token = None
    while True:
        page = request("GET", f"/pipeline/{pid}/workflow" + (f"?page-token={token}" if token else ""), not_found_ok=True)
        items += page.get("items", [])
        token = page.get("next_page_token")
        if not token:
            return items


def wait() -> None:
    pid = os.environ["PIPELINE_ID"]
    deadline = time.monotonic() + 60 * float(os.environ.get("TIMEOUT_MINUTES", "60"))
    seen_any = False
    grace_until = time.monotonic() + STARTUP_GRACE_SECONDS
    while True:
        try:
            flows = workflows(pid)
        except NotFound as exc:
            if time.monotonic() < grace_until:
                print(f"[{time.strftime('%H:%M:%S')}] pipeline not visible yet ({exc}); retrying", flush=True)
                time.sleep(min(POLL_SECONDS, 10.0))
                continue
            sys.exit(f"::error::CircleCI pipeline did not become visible within {STARTUP_GRACE_SECONDS:.0f}s: {exc}")
        if flows:
            seen_any = True
            states = ", ".join(f"{w.get('name')}: {w.get('status')}" for w in flows)
            print(f"[{time.strftime('%H:%M:%S')}] {states}", flush=True)
            if all(w.get("status") not in RUNNING for w in flows):
                failed = [w for w in flows if w.get("status") not in PASSED]
                for w in flows:
                    print(f"{w.get('name')}: https://app.circleci.com/pipelines/workflows/{w.get('id')}")
                if failed:
                    sys.exit("::error::CircleCI " + ", ".join(f"{w.get('name')} {w.get('status')}" for w in failed))
                return
        if time.monotonic() > deadline:
            sys.exit("::error::CircleCI pipeline did not finish in time" + ("" if seen_any else " (no workflow ever started: check the pipeline parameter that runs the job, and the pipeline's trigger)"))
        time.sleep(POLL_SECONDS)


def cancel() -> None:
    pid = os.environ.get("PIPELINE_ID")
    if not pid:
        return
    try:
        flows = workflows(pid)
    except NotFound as exc:
        print(f"::notice::pipeline {pid} not visible, nothing to cancel ({exc})")
        return
    for w in flows:
        if w.get("status") in RUNNING:
            request("POST", f"/workflow/{w['id']}/cancel")
            print(f"Cancelled CircleCI workflow {w.get('name')}")


if __name__ == "__main__":
    commands = {"start": start, "wait": wait, "cancel": cancel}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: {sys.argv[0]} {'|'.join(commands)}")
    commands[sys.argv[1]]()
