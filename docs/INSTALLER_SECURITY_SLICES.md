# Installer Security Slices

**Date:** 2026-05-07
**Status:** Planned
**Scope:** Incremental hardening of the root installer and update path.

This plan covers supply-chain risk in `install.sh` and the host wrapper installed
as `/usr/local/bin/fqdn-updater`. It does not change the product runtime scope:
FQDN-updater remains a Keenetic-only CLI/Docker Compose job with systemd timer
support, no daemon, no web UI, and no new router transport.

## Goal

Reduce the amount of mutable remote code executed as root during install/update,
then require release artifacts to be verified before deployment.

These slices are intentionally sequential. They all touch the installer contract,
packaging tests, and operator documentation, so parallel implementation would
create unnecessary conflicts.

## Guardrails

- Do not change runtime CLI behavior, config schema, RCI behavior, Docker Compose
  job semantics, or systemd timer semantics.
- Do not touch production `config.json`, `config.*.json`, `.env*`, `secrets/`,
  `data/`, or local AI/editor state.
- Keep updates compatible with the existing wrapper UX:
  - `fqdn-updater update`;
  - `fqdn-updater update --version vX.Y.Z`.
- Update operator-facing docs whenever install/update behavior changes:
  `README.md`, `README_EN.md`, `docs/DEPLOYMENT.md`,
  `docs/USER_QUICKSTART.md`, and `docs/LLM_CONTEXT.md`.
- Update `tests/packaging/test_packaging_assets.py` so packaging tests encode
  the new installer contract instead of the old `main` fallback behavior.
- Run the verification gate after every slice:
  - `bash -n install.sh`;
  - `.venv/bin/python -m pytest tests/packaging/test_packaging_assets.py`;
  - `./scripts/verify.sh`.

## Agent Use

No subagent is required for these slices. If the implementation workflow uses
agents anyway, run at most one worker per slice and complete the slices in order.
Do not split a single slice across parallel workers because the write set is
shared: `install.sh`, packaging tests, and install/update docs.

## Slice 1 - Local Installer For Update

**Purpose:** Stop `fqdn-updater update` from executing
`raw.githubusercontent.com/.../main/install.sh` as root.

**Changes:**

- Change the generated host wrapper so `run_update` executes the local installer
  from `/opt/fqdn-updater/install.sh` instead of downloading installer code from
  `main`.
- Copy the local installer to a temporary file before execution, run that copy
  with `bash`, then remove it. This prevents the running script from depending on
  a file that deployment may replace.
- Preserve root and non-root behavior: root runs the temporary installer
  directly; non-root uses `sudo`.
- If `/opt/fqdn-updater/install.sh` is missing or unreadable, fail with a clear
  message telling the operator to reinstall from a versioned release tag.

**Target files:**

- `install.sh`
- `tests/packaging/test_packaging_assets.py`
- `README.md`
- `README_EN.md`
- `docs/DEPLOYMENT.md`

**Risk:** Low to medium. Existing installations still have the old wrapper until
they are updated once, so the first migration to this slice may still use the
previous update path or a manual versioned reinstall.

**Done when:**

- The generated wrapper no longer contains `raw.githubusercontent.com` or
  `main/install.sh`.
- Packaging tests assert that `update` runs a local installer copy.
- `fqdn-updater update --version vX.Y.Z` remains documented and supported.
- The slice verification gate passes.

## Slice 2 - Release/Tag Only Default

**Purpose:** Remove silent fallback to `heads/main` when no GitHub Release is
available.

**Changes:**

- `resolve_release_ref` must no longer return `heads/main`.
- With `--version`, the installer resolves and installs only that release tag.
- Without `--version`, the installer resolves and installs the latest GitHub
  Release. If latest release lookup fails, returns malformed data, or no release
  exists, fail before downloading project code.
- Remove `DEFAULT_BRANCH` if it is no longer needed.
- Replace operator install examples that use mutable `main` with versioned tag
  examples, for example:

  ```bash
  curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/v1.0.2/install.sh | sudo bash -s -- --version v1.0.2
  ```

- Do not document `main` install/update as a normal operator path. If a dev path
  is needed later, keep it explicit and separate from production docs.

**Target files:**

- `install.sh`
- `tests/packaging/test_packaging_assets.py`
- `README.md`
- `README_EN.md`
- `docs/DEPLOYMENT.md`
- `docs/USER_QUICKSTART.md`
- `docs/LLM_CONTEXT.md`

**Risk:** Medium. Production installs become fail-fast when no GitHub Release
exists. This is intentional, but it makes release creation part of the deployment
contract.

**Done when:**

- The installer no longer downloads `archive/refs/heads/main.tar.gz`.
- The old packaging test for installing `main` when no release exists is
  replaced with a fail-fast packaging test.
- Operator docs no longer recommend `curl .../main/install.sh | sudo bash`.
- The slice verification gate passes.

## Slice 3 - Checksummed Release Archive

**Purpose:** Verify the release archive before extraction and deployment.

**Release asset contract:**

- Each GitHub Release must contain:
  - `fqdn-updater-${tag}.tar.gz`;
  - `fqdn-updater-${tag}.tar.gz.sha256`.
- The checksum file must contain the SHA256 for the release tarball and must not
  be read from inside the downloaded tarball.
- `--version vX.Y.Z` resolves the GitHub Release for tag `vX.Y.Z`; no release
  object means installation fails.
- The latest install path uses the same asset contract for the latest release.

**Changes:**

- Download the release tarball asset and its `.sha256` asset for the selected
  release.
- Verify SHA256 before `tar -xzf` and before any deployment step.
- Fail before extraction if the checksum asset is missing, malformed, or does not
  match the downloaded tarball.
- Update release documentation so maintainers know checksum assets are mandatory.
- If release asset publication is automated, prefer repository-local shell or
  `gh` CLI over adding third-party marketplace actions.

**Target files:**

- `install.sh`
- `tests/packaging/test_packaging_assets.py`
- `.github/workflows/*`, only if release asset publication is automated
- `docs/DEPLOYMENT.md`
- `docs/LLM_CONTEXT.md`
- `SECURITY.md`, if the residual risk model is documented there

**Risk:** Medium to high. New installers will reject old releases that do not
have checksum assets. Plan the migration by publishing a release with the new
asset contract before treating this as the standard update path.

**Done when:**

- Packaging tests assert that checksum verification occurs before extraction and
  deployment.
- `tar -xzf` is unreachable after a failed checksum.
- Docs state that SHA256 protects download integrity but does not replace release
  signatures and does not fully protect against a compromised GitHub account.
- The slice verification gate passes.

## Recommended Order

1. Slice 1 - Local installer for update.
2. Slice 2 - Release/tag only default.
3. Slice 3 - Checksummed release archive.

Commit each slice separately during implementation. Use focused Conventional
Commit messages such as `fix: harden update installer path`,
`fix: require release tags for installer`, and
`feat: verify installer release checksums`.
