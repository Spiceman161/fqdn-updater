# Third-Party Notices

FQDN-updater is distributed under the PolyForm Noncommercial License 1.0.0. Runtime dependencies keep their own licenses.

## itdoginfo/allow-domains

FQDN-updater uses raw URLs from `https://github.com/itdoginfo/allow-domains` as an upstream source registry for service domain and subnet lists.

The project does not vendor, copy, redistribute, or modify those lists. It stores only runtime URLs and fetches current upstream content during `dry-run` and `sync`.

At the time this notice was written, no upstream license file was found in the referenced repository. Treat the upstream data as third-party material with its own terms and availability. FQDN-updater distributes only its own source code and the URL mapping needed to fetch the lists at runtime.

## Python Dependencies

Declared runtime dependencies are listed in `pyproject.toml`:

- `pydantic`
- `questionary`
- `rich`
- `typer`

Development dependencies:

- `pytest`
- `ruff`
