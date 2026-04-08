# FQDN-updater Roadmap

Canonical slice state lives in `.codex/slices.json`. This document is the human-readable mirror.

## Workflow Rules

- `slice_preparer` selects the next minimal valid slice from `.codex/slices.json`.
- Planning does not change slice status.
- The active implementation slice is marked `in_progress`.
- After green verification, the slice moves to `done`.
- The next unblocked slice can move to `ready`.
- Only one slice may be `in_progress` at a time.

## Current State

- Current slice: `S7` — Fetch And Normalize Pipeline
- Completed slices: `S0` — Workflow state bootstrap; `S1` — Python CLI scaffold baseline; `S2` — Config domain model expansion; `S3` — Source registry scaffold; `S4` — Logging and run artifact scaffold; `S5` — RCI client contracts; `S6` — Workflow enforcement gates; `S7` — Fetch And Normalize Pipeline

## Backlog

| ID | Status | Title | Goal |
| --- | --- | --- | --- |
| S0 | done | Workflow state bootstrap | Introduce canonical slice tracking artifacts for roadmap-driven work. |
| S1 | done | Python CLI scaffold baseline | Create the minimal installable Python CLI scaffold with config init and validation. |
| S2 | done | Config domain model expansion | Deepen typed config models and validation rules toward router and mapping UX. |
| S3 | done | Source registry scaffold | Introduce explicit service source registry contracts without fetching data yet. |
| S4 | ready | Logging and run artifact scaffold | Prepare machine-readable run artifacts and logging boundaries. |
| S5 | done | RCI client contracts | Define transport-facing interfaces and RCI-only client contracts. |
| S6 | done | Workflow enforcement gates | Add enforced verification and repo-local workflow rules for slice execution. |
| S7 | done | Fetch And Normalize Pipeline | Fetch raw service sources and normalize them into deterministic domain entries. |
