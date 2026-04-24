# Security Policy

## Supported Versions

Security fixes target the latest released version of FQDN-updater.

## Reporting a Vulnerability

Do not open public issues for credential leaks, router mutation vulnerabilities, or other sensitive
security reports.

Report privately to the repository owner through GitHub. Include:

- affected version or commit;
- a minimal reproduction;
- expected and actual behavior;
- any logs with secrets redacted.

## Secret Handling

FQDN-updater expects production secrets to live outside git:

- `.env` or `.env.*` for environment-backed passwords;
- `secrets/` for file-backed passwords;
- local `config.json` for deployment-specific router configuration.

The repository `.gitignore` excludes those paths. Do not paste real Keenetic credentials into
issues, pull requests, or logs.
