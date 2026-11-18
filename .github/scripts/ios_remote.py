#!/usr/bin/env python3
"""Run a Mac job on a cloud Mac when the org's own Macs are busy or offline, and wait for it.

Source of truth: Michael-Dial/app-store-release, ci-fallback/ios_remote.py. Every repository carries
a copy at .github/scripts/ios_remote.py, written by ci-policy/sync.py; edit the source, not a copy.

A workflow's fallback job runs this when ios_route.py answers "fallback". It tries the cloud Macs
set up for the repository in order, and the first that accepts the build runs it; the GitHub
check then passes or fails with that build. A service that refuses (out of minutes, a token
revoked, the branch not visible to it yet) is reported and the next one is tried.

  xcodecloud  the app's Xcode Cloud workflow, through the App Store Connect API. 25 compute hours
              a month come with the Apple Developer Program. The API cannot cancel a build.
  codemagic   the app's codemagic.yaml workflow, through Codemagic's API (500 free M2 minutes a
              month on a personal account; none on a team).
  circleci    the repository's .circleci/config.yml pipeline (circleci_run.py), with PARAMETERS.

    ios_remote.py start    # writes backend=... and build_id=... to $GITHUB_OUTPUT
    ios_remote.py wait     # BACKEND, BUILD_ID: polls until the build ends; fails with it
    ios_remote.py cancel   # BACKEND, BUILD_ID: the GitHub run was cancelled

Environment (the workflow maps repository variables and secrets onto these names):
    BRANCH                   branch to build
    BACKENDS                 order to try, comma-separated (route's output); default: every one
                             set up, in the order xcodecloud, codemagic, circleci
    XCODE_CLOUD_WORKFLOW_ID  variable; the Xcode Cloud workflow (no start conditions of its own)
    ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_P8
                             secrets ASC_CI_KEY_ID, ASC_CI_ISSUER_ID, ASC_CI_KEY_P8: an App Store
                             Connect API key for starting builds, not the release pipeline's key
    CODEMAGIC_APP_ID         variable; the app in Codemagic
    CODEMAGIC_WORKFLOW_ID    the workflow in codemagic.yaml (default mac-fallback)
    CODEMAGIC_TOKEN          secret; Codemagic API token
    CIRCLECI_PROJECT_SLUG, CIRCLECI_DEFINITION_ID, CIRCLE_TOKEN, PARAMETERS
                             as circleci_run.py
    TIMEOUT_MINUTES          for wait (default 60)
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import circleci_run  # noqa: E402

ORDER = ("xcodecloud", "codemagic", "circleci")
ASC_API = os.environ.get("ASC_API", "https://api.appstoreconnect.apple.com").rstrip("/")
CODEMAGIC_API = os.environ.get("CODEMAGIC_API", "https://api.codemagic.io").rstrip("/")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "30"))
# Xcode Cloud learns of a pushed branch from its own GitHub App; give it a moment.
REF_WAIT_SECONDS = float(os.environ.get("XCODE_CLOUD_REF_WAIT_SECONDS", "180"))
# Consecutive status errors tolerated while waiting (a network blip is not the build failing).
STATUS_RETRIES = 5

# Codemagic build states that end a build; "finished" is the only passing one. "waiting" and
# "queued" mean accepted but not yet given a machine (seen 2026-11-18: a build "waiting" for 14+
# minutes on a personal account).
CODEMAGIC_DONE = {"finished", "failed", "canceled", "timeout", "skipped"}
CODEMAGIC_NOT_STARTED = {"waiting", "queued"}
# A build accepted but not started within this many minutes is cancelled, and the next cloud Mac
# in BACKENDS is tried: a service holding a build in its own queue is the logjam this replaces.
START_TIMEOUT_MINUTES = float(os.environ.get("START_TIMEOUT_MINUTES", "10"))


class RemoteError(Exception):
    """A cloud Mac service refused or failed a request."""


def configured(env=os.environ) -> list[str]:
    """The cloud Macs set up for this repository, in the order to try them.

    A value may be the secret itself or just a marker that it is set (route passes markers, not
    secrets). When CIRCLE_TOKEN is absent from the environment altogether, as in a workflow wired
    before this script existed, CircleCI counts as set up on its project and definition alone.
    """
    ready = {
        "xcodecloud": all(env.get(k) for k in ("XCODE_CLOUD_WORKFLOW_ID", "ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_P8")),
        "codemagic": all(env.get(k) for k in ("CODEMAGIC_APP_ID", "CODEMAGIC_TOKEN")),
        "circleci": bool(env.get("CIRCLECI_PROJECT_SLUG", "").strip() and env.get("CIRCLECI_DEFINITION_ID", "").strip()
                         and env.get("CIRCLE_TOKEN", "unchecked")),
    }
    return [b for b in ORDER if ready[b]]


def http(method: str, url: str, headers: dict[str, str], body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise RemoteError(f"{method} {url.split('?')[0]}: HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise RemoteError(f"{method} {url.split('?')[0]}: {e}") from None
    return json.loads(text) if text else {}


# --- Xcode Cloud ---------------------------------------------------------------------------------

def asc_token(key_id: str, issuer: str, p8: str, now: int | None = None) -> str:
    """An App Store Connect API token: a JWT signed ES256 with the .p8 key, valid 20 minutes."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    now = int(time.time()) if now is None else now
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    claims = {"iss": issuer, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"}
    signing = b64(json.dumps(header, separators=(",", ":")).encode()) + b"." + \
        b64(json.dumps(claims, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(p8.strip().encode(), password=None)
    r, s = decode_dss_signature(key.sign(signing, ec.ECDSA(hashes.SHA256())))
    return (signing + b"." + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))).decode()


def asc(method: str, path: str, body: dict | None = None) -> dict:
    token = asc_token(os.environ["ASC_KEY_ID"], os.environ["ASC_ISSUER_ID"], os.environ["ASC_KEY_P8"])
    url = path if path.startswith("http") else ASC_API + path
    return http(method, url, {"Authorization": f"Bearer {token}"}, body)


def git_reference(repository: str, branch: str) -> str:
    """Xcode Cloud's id for the branch, waiting a little for it to learn of a new one."""
    want = f"refs/heads/{branch}"
    deadline = time.monotonic() + REF_WAIT_SECONDS
    while True:
        url: str | None = f"/v1/scmRepositories/{repository}/gitReferences?limit=200"
        while url:
            page = asc("GET", url)
            for ref in page.get("data", []):
                a = ref.get("attributes", {})
                if a.get("canonicalName") == want and not a.get("isDeleted"):
                    return ref["id"]
            url = (page.get("links") or {}).get("next")
        if time.monotonic() > deadline:
            raise RemoteError(f"Xcode Cloud does not see branch {branch} (is its GitHub App on this repository?)")
        print(f"Xcode Cloud does not see {branch} yet; checking again", flush=True)
        time.sleep(15)


def xcodecloud_start(branch: str) -> tuple[str, str]:
    workflow = os.environ["XCODE_CLOUD_WORKFLOW_ID"]
    repository = asc("GET", f"/v1/ciWorkflows/{workflow}/repository")["data"]["id"]
    ref = git_reference(repository, branch)
    run = asc("POST", "/v1/ciBuildRuns", {"data": {
        "type": "ciBuildRuns", "attributes": {},
        "relationships": {"workflow": {"data": {"type": "ciWorkflows", "id": workflow}},
                          "sourceBranchOrTag": {"data": {"type": "scmGitReferences", "id": ref}}}}})["data"]
    return run["id"], f"Xcode Cloud build {run.get('attributes', {}).get('number', '?')}"


def xcodecloud_status(build: str) -> tuple[str, str]:
    a = asc("GET", f"/v1/ciBuildRuns/{build}")["data"]["attributes"]
    if a.get("executionProgress") == "PENDING":
        return "waiting", "PENDING"
    if a.get("executionProgress") != "COMPLETE":
        return "running", str(a.get("executionProgress"))
    status = str(a.get("completionStatus"))
    return ("passed" if status == "SUCCEEDED" else "failed"), status


def xcodecloud_cancel(build: str) -> None:
    print(f"::notice::Xcode Cloud build {build} keeps running: the App Store Connect API cannot cancel "
          "a build. Cancel it in App Store Connect → Xcode Cloud if it should stop.")


# --- Codemagic -----------------------------------------------------------------------------------

def codemagic(method: str, path: str, body: dict | None = None) -> dict:
    return http(method, CODEMAGIC_API + path, {"x-auth-token": os.environ["CODEMAGIC_TOKEN"]}, body)


def codemagic_start(branch: str) -> tuple[str, str]:
    workflow = os.environ.get("CODEMAGIC_WORKFLOW_ID") or "mac-fallback"
    r = codemagic("POST", "/builds", {"appId": os.environ["CODEMAGIC_APP_ID"], "workflowId": workflow, "branch": branch})
    build = r.get("buildId")
    if not build:
        raise RemoteError(f"Codemagic returned no build id: {r}")
    return build, f"Codemagic build {build} ({workflow})"


def codemagic_status(build: str) -> tuple[str, str]:
    status = str(codemagic("GET", f"/builds/{build}").get("build", {}).get("status"))
    if status in CODEMAGIC_NOT_STARTED:
        return "waiting", status
    if status not in CODEMAGIC_DONE:
        return "running", status
    return ("passed" if status == "finished" else "failed"), status


def codemagic_cancel(build: str) -> None:
    codemagic("POST", f"/builds/{build}/cancel")
    print(f"Cancelled Codemagic build {build}")


# --- CircleCI (circleci_run.py) ------------------------------------------------------------------

def _circleci_env() -> None:
    os.environ.setdefault("PROJECT_SLUG", os.environ.get("CIRCLECI_PROJECT_SLUG", ""))
    os.environ.setdefault("DEFINITION_ID", os.environ.get("CIRCLECI_DEFINITION_ID", ""))


def circleci_start(branch: str) -> tuple[str, str]:
    _circleci_env()
    body = {"definition_id": os.environ["DEFINITION_ID"], "config": {"branch": branch},
            "checkout": {"branch": branch}, "parameters": json.loads(os.environ.get("PARAMETERS") or "{}")}
    try:
        pipeline = circleci_run.request("POST", f"/project/{os.environ['PROJECT_SLUG']}/pipeline/run", body)
    except SystemExit as e:  # circleci_run reports and exits; here the next service gets a turn
        raise RemoteError(str(e)) from None
    if not pipeline.get("id"):
        raise RemoteError(f"CircleCI returned no pipeline id: {pipeline}")
    return pipeline["id"], f"CircleCI pipeline {pipeline.get('number', '?')}"


def circleci_status(build: str) -> tuple[str, str]:
    try:
        flows = circleci_run.workflows(build)
    except circleci_run.NotFound:
        return "waiting", "pipeline not visible yet"
    except SystemExit as e:
        raise RemoteError(str(e)) from None
    if not flows:
        return "waiting", "no workflow yet"
    states = ", ".join(f"{w.get('name')}: {w.get('status')}" for w in flows)
    if any(w.get("status") in circleci_run.RUNNING for w in flows):
        return "running", states
    return ("passed" if all(w.get("status") in circleci_run.PASSED for w in flows) else "failed"), states


def circleci_cancel(build: str) -> None:
    os.environ["PIPELINE_ID"] = build
    circleci_run.cancel()


BACKEND = {
    "xcodecloud": (xcodecloud_start, xcodecloud_status, xcodecloud_cancel),
    "codemagic": (codemagic_start, codemagic_status, codemagic_cancel),
    "circleci": (circleci_start, circleci_status, circleci_cancel),
}


# --- Commands ------------------------------------------------------------------------------------

def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{name}={value}\n")


def summary(line: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


def order() -> list[str]:
    """The cloud Macs to try, in order: route's BACKENDS, of those set up here."""
    ready = configured()
    asked = [b.strip() for b in os.environ.get("BACKENDS", "").split(",") if b.strip()]
    return [b for b in asked if b in ready] if asked else ready


def start_first(backends: list[str], branch: str, refused: list[str]) -> tuple[str, str] | None:
    """Start the build on the first of `backends` that accepts it; (backend, build id) or None."""
    for backend in backends:
        try:
            build, label = BACKEND[backend][0](branch)
        except (RemoteError, KeyError) as e:
            print(f"::warning::{backend} did not start the build: {e}")
            refused.append(f"{backend}: {e}")
            continue
        print(f"Started {label} on {branch}")
        summary(f"Mac job run on **{backend}**: {label}" + (f" (passed over first: {'; '.join(refused)})" if refused else ""))
        output("backend", backend)
        output("build_id", build)
        return backend, build
    return None


def start() -> None:
    backends = order()
    if not backends:
        sys.exit("::error::No cloud Mac is set up for this repository (Xcode Cloud, Codemagic or CircleCI).")
    refused: list[str] = []
    if not start_first(backends, os.environ["BRANCH"], refused):
        sys.exit("::error::No cloud Mac accepted the build. " + " | ".join(refused))


def wait() -> None:
    """Follow the build to its end. One that is still waiting for a machine after
    START_TIMEOUT_MINUTES is cancelled and the build moves to the next cloud Mac in order; once a
    build has started, it is followed to the end wherever it is."""
    backend, build = os.environ["BACKEND"], os.environ["BUILD_ID"]
    later = order()
    later = later[later.index(backend) + 1:] if backend in later else []
    refused: list[str] = []
    deadline = time.monotonic() + 60 * float(os.environ.get("TIMEOUT_MINUTES", "60"))
    errors, waiting_since = 0, None
    while True:
        try:
            state, detail = BACKEND[backend][1](build)
            errors = 0
        except RemoteError as e:
            errors += 1
            if errors > STATUS_RETRIES:
                sys.exit(f"::error::{backend} status failed {errors} times in a row: {e}")
            state, detail = "unknown", f"status unavailable ({e})"
        print(f"[{time.strftime('%H:%M:%S')}] {backend} {build}: {detail}", flush=True)
        if state == "passed":
            return
        if state == "failed":
            sys.exit(f"::error::{backend} build {build} ended {detail}")
        if state == "waiting":
            waiting_since = waiting_since or time.monotonic()
            if time.monotonic() - waiting_since > 60 * START_TIMEOUT_MINUTES:
                note = f"{backend}: accepted but not started within {START_TIMEOUT_MINUTES:g} minutes ({detail})"
                print(f"::warning::{note}; cancelling it")
                refused.append(note)
                try:
                    BACKEND[backend][2](build)
                except RemoteError as e:
                    print(f"::warning::could not cancel {backend} build {build}: {e}")
                moved = start_first(later, os.environ["BRANCH"], refused)
                if not moved:
                    sys.exit("::error::No cloud Mac started the build. " + " | ".join(refused))
                backend, build = moved
                later = later[later.index(backend) + 1:]
                waiting_since, errors = None, 0
                continue
        elif state == "running":
            waiting_since = None
        if time.monotonic() > deadline:
            sys.exit(f"::error::{backend} build {build} did not finish in time ({detail})")
        time.sleep(POLL_SECONDS)


def cancel() -> None:
    backend, build = os.environ.get("BACKEND", ""), os.environ.get("BUILD_ID", "")
    if backend in BACKEND and build:
        try:
            BACKEND[backend][2](build)
        except RemoteError as e:
            print(f"::warning::could not cancel {backend} build {build}: {e}")


if __name__ == "__main__":
    commands = {"start": start, "wait": wait, "cancel": cancel}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(f"usage: {sys.argv[0]} {'|'.join(commands)}")
    commands[sys.argv[1]]()
