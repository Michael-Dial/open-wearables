#!/usr/bin/env python3
"""Check this repository's GitHub Actions workflows against the org's CI policy.

Source of truth: Michael-Dial/app-store-release, ci-policy/. Every repository carries a copy at
.github/ci-policy/check_workflows.py, written by ci-policy/sync.py; edit the source, not a copy.

Rules (ci-policy/rules.md explains each):
  1. A workflow that runs on pull_request runs on push only for the default branch.
  3. Every job names its runner. A private repository's Mac jobs use the iOS runner group's
     labels (self-hosted, macOS, ARM64, ios), never GitHub's hosted macos-*; a public
     repository never uses a self-hosted runner.
  4. Every action is pinned to a version tag (v5, v5.1.0) or a commit SHA, never a branch.
  5. Every workflow is one GitHub can start: no job calls a reusable workflow in another
     repository, and no mapping repeats a key.
  6. A private repository's Mac job is routed: a `route` job runs .github/scripts/ios_route.py,
     the Mac job needs it, and a fallback job runs .github/scripts/circleci_run.py; or the job
     sets env CI_POLICY_MAC_ONLY to say why it runs on the Macs alone.
Rule 2 (no cancel-in-progress to hide a duplicate trigger) is guidance only; rule 1 removes the
duplicate it would hide.

Usage: check_workflows.py [--private true|false] [--default-branch NAME] [WORKFLOW_DIR]
In Actions both come from the event payload (see workflow-policy.yml). Needs PyYAML.
Exits 1 and prints one ::error:: line per violation, naming the file and job.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

MAC_LABELS = {"self-hosted", "macos", "arm64", "ios"}
VERSION_REF = re.compile(r"^v\d+(\.\d+){0,2}$")
SHA_REF = re.compile(r"^[0-9a-f]{40}$")


def triggers(doc):
    # PyYAML reads the bare key `on` as the boolean True.
    on = doc.get("on", doc.get(True))
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {k: None for k in on}
    return on or {}


def check_triggers(on, default_branch):
    if not ({"pull_request", "pull_request_target"} & on.keys()) or "push" not in on:
        return []
    push = on["push"] or {}
    if "branches-ignore" in push:
        return [f"runs on pull_request and on push with branches-ignore; give push `branches: [{default_branch}]` (rule 1)"]
    branches = push.get("branches")
    if branches is None:
        if push.get("tags") is not None:
            return []  # a tags-only push never fires for a PR branch
        return [f"runs on pull_request and on every push; give push `branches: [{default_branch}]` (rule 1)"]
    extra = [b for b in branches if b != default_branch]
    if extra:
        return [f"runs on pull_request and on push to {extra}; push may only name `{default_branch}` (rule 1)"]
    return []


def runner_candidates(runs_on):
    """Each possible label set a runs-on value can resolve to, as lowercased sets."""
    if isinstance(runs_on, list):
        return [{str(x).lower() for x in runs_on}]
    if isinstance(runs_on, dict):
        labels = runs_on.get("labels", [])
        labels = [labels] if isinstance(labels, str) else labels
        return [{str(x).lower() for x in labels} | {"group:" + str(runs_on.get("group", ""))}]
    s = str(runs_on)
    if "${{" not in s:
        return [{s.lower()}]
    found = []
    for m in re.finditer(r"fromJSON\(\s*'(\[.*?\])'\s*\)", s):
        try:
            found.append({str(x).lower() for x in json.loads(m.group(1))})
        except ValueError:
            pass
    stripped = re.sub(r"fromJSON\(\s*'\[.*?\]'\s*\)", "", s)
    for m in re.finditer(r"'([^']+)'", stripped):
        label = m.group(1)
        if re.match(r"^(macos|ubuntu|windows)-", label, re.I) or label.lower() == "self-hosted":
            found.append({label.lower()})
    return found


class _NoDuplicates(yaml.SafeLoader):
    """A loader that refuses a repeated key, as GitHub does (PyYAML keeps the last one)."""


def _mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"key {key!r} appears twice (line {key_node.start_mark.line + 1})", key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_NoDuplicates.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def is_mac(job):
    return any("self-hosted" in labels and "macos" in labels for labels in runner_candidates(job.get("runs-on", "")))


def needs_of(job):
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def runs_script(job, script):
    return any(script in str(step.get("run", "")) for step in job.get("steps") or [] if isinstance(step, dict))


def check_routing(jobs):
    """Rule 6, for a private repository: every Mac job is routed, with somewhere to fall back to."""
    macs = [n for n, j in jobs.items() if isinstance(j, dict) and is_mac(j)
            and not (j.get("env") or {}).get("CI_POLICY_MAC_ONLY")]
    if not macs:
        return []
    errors = []
    route = jobs.get("route") if isinstance(jobs.get("route"), dict) else None
    if not route or not runs_script(route, ".github/scripts/ios_route.py"):
        errors.append(("", "has a Mac job but no `route` job running .github/scripts/ios_route.py (rule 6)"))
    fallbacks = [n for n, j in jobs.items() if isinstance(j, dict) and "route" in needs_of(j)
                 and "'fallback'" in str(j.get("if", "")) and "==" in str(j.get("if", ""))
                 and (runs_script(j, ".github/scripts/ios_remote.py")
                      or runs_script(j, ".github/scripts/circleci_run.py"))]
    if not fallbacks:
        errors.append(("", "has a Mac job but no fallback job (needs: route, if: ...target == 'fallback', "
                           "running .github/scripts/ios_remote.py) (rule 6)"))
    for n in macs:
        job = jobs[n]
        if "route" not in needs_of(job) or "!= 'fallback'" not in str(job.get("if", "")):
            errors.append((n, "runs on the Macs without `needs: route` and "
                              "`if: ${{ !cancelled() && needs.route.outputs.target != 'fallback' }}`; "
                              "or set env CI_POLICY_MAC_ONLY to say why it runs on the Macs alone (rule 6)"))
    return errors


def check_runner(job, private):
    if "uses" in job:
        return []  # a reusable workflow call; its own jobs name their runners
    if "runs-on" not in job:
        return ["has no runs-on (rule 3)"]
    errors = []
    for labels in runner_candidates(job["runs-on"]):
        hosted_mac = [l for l in labels if l.startswith("macos-")]
        if "self-hosted" in labels:
            if not private:
                errors.append("uses a self-hosted runner in a public repository (rule 3)")
            elif "macos" in labels and not MAC_LABELS <= labels:
                errors.append("uses a self-hosted Mac without the iOS group's labels "
                              "[self-hosted, macOS, ARM64, ios] (rule 3)")
        elif hosted_mac and private:
            errors.append(f"can run on GitHub's hosted {hosted_mac[0]} in a private repository; "
                          "use [self-hosted, macOS, ARM64, ios] (rule 3)")
    return errors


def check_uses(steps):
    errors = []
    for step in steps or []:
        uses = step.get("uses") if isinstance(step, dict) else None
        if not uses or uses.startswith(("./", "docker://")):
            continue
        ref = uses.rpartition("@")[2] if "@" in uses else ""
        if not (VERSION_REF.match(ref) or SHA_REF.match(ref)):
            errors.append(f"uses {uses}: pin to a version tag or commit SHA, not a branch (rule 4)")
    return errors


def check_file(path, private, default_branch):
    text = path.read_text()
    try:
        yaml.load(text, Loader=_NoDuplicates)
    except yaml.YAMLError as e:
        return [("", f"GitHub cannot start this workflow: {str(e).splitlines()[0]} (rule 5)")]
    doc = yaml.safe_load(text) or {}
    errors = [("", e) for e in check_triggers(triggers(doc), default_branch)]
    jobs = doc.get("jobs") or {}
    for name, job in jobs.items():
        errors += [(name, e) for e in check_runner(job, private)]
        errors += [(name, e) for e in check_uses(job.get("steps"))]
        if "uses" in job:
            ref = job["uses"]
            if not ref.startswith("./"):
                errors.append((name, f"calls {ref}, a workflow in another repository; GitHub fetches it "
                                     "before the run starts, even when the job's `if:` is false, and one it "
                                     "cannot read fails the whole workflow. Use a script synced into "
                                     ".github/scripts/ instead (rule 5)"))
                if not (VERSION_REF.match(ref.rpartition("@")[2]) or SHA_REF.match(ref.rpartition("@")[2])):
                    errors.append((name, f"calls {ref}: pin to a version tag or commit SHA, not a branch (rule 4)"))
    if private:
        errors += check_routing(jobs)
    return errors


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--private", choices=["true", "false"], required=True)
    p.add_argument("--default-branch", required=True)
    p.add_argument("dir", nargs="?", default=".github/workflows")
    a = p.parse_args()
    files = sorted(Path(a.dir).glob("*.y*ml"))
    failed = 0
    for f in files:
        for job, msg in check_file(f, a.private == "true", a.default_branch):
            where = f"{f}" + (f" job {job}" if job else "")
            print(f"::error file={f}::{where}: {msg}")
            failed += 1
    print(f"{len(files)} workflow(s) checked, {failed} violation(s). Policy: Michael-Dial/app-store-release ci-policy/rules.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
