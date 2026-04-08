from __future__ import annotations

import os
from pathlib import Path

from fqdn_updater.domain.config_schema import RouterConfig


class EnvironmentFileSecretResolver:
    def resolve(self, router: RouterConfig) -> str:
        if router.password_env is not None:
            return self._resolve_env(router=router)
        if router.password_file is not None:
            return self._resolve_file(router=router)
        raise RuntimeError(f"Router '{router.id}' has no configured secret source")

    def _resolve_env(self, *, router: RouterConfig) -> str:
        assert router.password_env is not None
        value = os.environ.get(router.password_env)
        if value is None:
            raise RuntimeError(
                f"Router '{router.id}' password env '{router.password_env}' is not set"
            )
        normalized_value = value.strip()
        if not normalized_value:
            raise RuntimeError(
                f"Router '{router.id}' password env '{router.password_env}' is blank"
            )
        return normalized_value

    def _resolve_file(self, *, router: RouterConfig) -> str:
        assert router.password_file is not None
        path = Path(router.password_file)
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(
                f"Router '{router.id}' password file '{path}' could not be read: {exc}"
            ) from exc
        if not value:
            raise RuntimeError(f"Router '{router.id}' password file '{path}' is blank")
        return value
