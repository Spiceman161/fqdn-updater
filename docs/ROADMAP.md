# FQDN-updater Roadmap

Canonical slice state lives in `.codex/slices.json`. This document is the human-readable mirror.

## Workflow Rules

- `slice_preparer` selects the next minimal valid slice from `.codex/slices.json`.
- Planning does not change slice status.
- The active implementation slice is marked `in_progress`.
- After green verification, the slice moves to `done`.
- The next unblocked slice can move to `ready` when one exists.
- Only one slice may be `in_progress` at a time.

## Current State

- Current slice: `S19` — Mixed Domain And Subnet Source Groups
- Completed slices: `S0` — Workflow state bootstrap; `S1` — Python CLI scaffold baseline; `S2` — Config domain model expansion; `S3` — Source registry scaffold; `S4` — Logging and run artifact scaffold; `S5` — RCI client contracts; `S6` — Workflow enforcement gates; `S7` — Fetch And Normalize Pipeline; `S8` — Deterministic Managed Diff Planning; `S9` — Read-Only Sync Orchestration; `S10` — RCI Read Transport Implementation; `S11` — CLI Dry-Run Entry Point; `S12` — Managed Object-Group Apply Core; `S13` — Apply Orchestration And Sync CLI; `S14` — Route Binding Read And Apply; `S15` — Run Logging And Rich Failure Artifacts; `S16` — Status And Preconditions Diagnostics; `S17` — Config Management CLI; `S18` — Packaging And Scheduled Execution; `S19` — Mixed Domain And Subnet Source Groups
- Next ready slice: none

## Backlog

| ID | Status | Title | Goal |
| --- | --- | --- | --- |
| S0 | done | Workflow state bootstrap | Introduce canonical slice tracking artifacts for roadmap-driven work. |
| S1 | done | Python CLI scaffold baseline | Create the minimal installable Python CLI scaffold with config init and validation. |
| S2 | done | Config domain model expansion | Deepen typed config models and validation rules toward router and mapping UX. |
| S3 | done | Source registry scaffold | Introduce explicit service source registry contracts without fetching data yet. |
| S4 | done | Logging and run artifact scaffold | Prepare machine-readable run artifacts and logging boundaries. |
| S5 | done | RCI client contracts | Define transport-facing interfaces and RCI-only client contracts. |
| S6 | done | Workflow enforcement gates | Add enforced verification and repo-local workflow rules for slice execution. |
| S7 | done | Fetch And Normalize Pipeline | Fetch raw service sources and normalize them into deterministic domain entries. |
| S8 | done | Deterministic Managed Diff Planning | Plan managed object-group diffs deterministically before any apply path exists. |
| S9 | done | Read-Only Sync Orchestration | Assemble the first dry-run orchestration flow without any router writes. |
| S10 | done | RCI Read Transport Implementation | Implement the first real Keenetic RCI read path for object-group state and DNS proxy status. |
| S11 | done | CLI Dry-Run Entry Point | Expose the read-only orchestration flow as a user-facing dry-run command with deterministic output and exit codes. |
| S12 | done | Managed Object-Group Apply Core | Implement safe managed-only object-group mutation through the RCI client. |
| S13 | done | Apply Orchestration And Sync CLI | Add a user-facing sync command that applies managed object-group diffs across routers. |
| S14 | done | Route Binding Read And Apply | Implement managed route binding support for configured object-groups. |
| S15 | done | Run Logging And Rich Failure Artifacts | Add operator-grade logging and richer artifact detail for dry-run and sync runs. |
| S16 | done | Status And Preconditions Diagnostics | Add user-facing status diagnostics for local config checks and remote Keenetic preconditions. |
| S17 | pending | Config Management CLI | Provide first-class CLI workflows for router and mapping management without manual JSON editing. |
| S18 | done | Packaging And Scheduled Execution | Package the tool for repeatable VPS execution through Docker and systemd. |
| S19 | done | Mixed Domain And Subnet Source Groups | Allow one managed Keenetic object-group to be synchronized from mixed domain, IPv4 CIDR, and IPv6 CIDR sources. |
