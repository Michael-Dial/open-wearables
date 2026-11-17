# CLAUDE.md

Please follow the guidelines and project structure defined in ./AGENTS.md

For Cursor and other agents: Refer to .cursor/rules/ for detailed configuration.

<!-- ci-policy:start -->
<!-- Written by Michael-Dial/app-store-release ci-policy/sync.py, policy 05258732bc. Edit it there, not here. -->
## CI workflow rules (org policy)

These apply to every file in `.github/workflows/`. `.github/ci-policy/check_workflows.py` enforces
rules 1, 3 and 4 on any pull request that touches a workflow (the "Workflow policy" check). The
source of this section, the check and the sync script is `Michael-Dial/app-store-release`,
`ci-policy/`: change them there and run `ci-policy/sync.py`, never by editing a copy.

1. **A workflow that runs on `pull_request` runs on `push` only for the default branch**
   (`push: branches: [main]`, or `[master]`). A PR branch is checked through its pull request.
   Without this, every commit on an open PR starts two runs; the concurrency group cancels one,
   and the PR shows a red "Cancelled" check that is not a failure (hpcosugarmin #30,
   open_wearables_ios_sdk #16).
2. **Never use `cancel-in-progress` to hide a duplicate trigger.** Remove the duplicate trigger
   instead. `cancel-in-progress` is for a newer run of the same work superseding an older one.
3. **Every job names its runner, and the runner matches the repository's visibility.**
   - Private repository, Mac job: `runs-on: [self-hosted, macOS, ARM64, ios]`, the org's iOS
     runner group (MacBook-Pro-6, Macbook-Pro-7). Never GitHub's hosted `macos-*`, including as
     a fallback in a `runs-on` expression: it bills at about ten times Linux and stops when the
     account's spending limit is hit.
   - Public repository: GitHub's hosted runners only, never `self-hosted`. A fork's pull request
     can edit the workflow, so nothing in the file can keep fork code off a self-hosted machine.
   - Linux jobs may use hosted `ubuntu-*` or the self-hosted `[self-hosted, linux, ...]` pool.
4. **Pin every action to a version tag (`@v5`, `@v5.1.0`) or a commit SHA, never a branch**
   (`@main`, `@master`). A branch changes under you; a startup failure from a moved or missing
   reusable workflow fails every caller with zero jobs and no log.
<!-- ci-policy:end -->
