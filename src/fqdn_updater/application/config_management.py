from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from fqdn_updater.domain.config_schema import RouterConfig, RouterServiceMappingConfig
from fqdn_updater.domain.schedule import RuntimeScheduleConfig
from fqdn_updater.infrastructure.config_repository import ConfigRepository


def normalize_rci_url_input(value: str) -> str:
    """Normalize operator-entered KeenDNS RCI URL to the external HTTPS endpoint."""
    normalized_value = value.strip()
    if not normalized_value:
        return normalized_value

    if "://" not in normalized_value:
        normalized_value = f"https://{normalized_value}"

    parsed_url = urlsplit(normalized_value)
    scheme = "https" if parsed_url.scheme in {"http", "https"} else parsed_url.scheme
    path = parsed_url.path
    if path in {"", "/"}:
        path = "/rci/"

    return urlunsplit(
        (
            scheme,
            parsed_url.netloc,
            path,
            parsed_url.query,
            parsed_url.fragment,
        )
    )


class ConfigManagementService:
    """Manage persisted router and mapping config entries."""

    def __init__(self, repository: ConfigRepository) -> None:
        self._repository = repository

    def add_router(
        self,
        *,
        path: Path,
        router_id: str,
        name: str,
        rci_url: str,
        username: str,
        password_env: str | None,
        password_file: str | None,
        enabled: bool,
        tags: list[str],
        timeout_seconds: int,
        allowed_source_ips: list[str],
    ) -> RouterConfig:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        router_payload = {
            "id": router_id,
            "name": name,
            "rci_url": normalize_rci_url_input(rci_url),
            "username": username,
            "auth_method": "digest",
            "password_env": password_env,
            "password_file": password_file,
            "enabled": enabled,
            "tags": tags,
            "timeout_seconds": timeout_seconds,
            "allowed_source_ips": allowed_source_ips,
        }
        payload["routers"].append(router_payload)

        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        return updated_config.routers[-1]

    def list_routers(self, *, path: Path) -> list[RouterConfig]:
        return list(self._repository.load(path=path).routers)

    def replace_router(
        self,
        *,
        path: Path,
        router_id: str,
        name: str,
        rci_url: str,
        username: str,
        password_env: str | None,
        password_file: str | None,
        enabled: bool,
        tags: list[str],
        timeout_seconds: int,
        allowed_source_ips: list[str],
    ) -> RouterConfig:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        router_payload = {
            "id": router_id,
            "name": name,
            "rci_url": normalize_rci_url_input(rci_url),
            "username": username,
            "auth_method": "digest",
            "password_env": password_env,
            "password_file": password_file,
            "enabled": enabled,
            "tags": tags,
            "timeout_seconds": timeout_seconds,
            "allowed_source_ips": allowed_source_ips,
        }

        existing_index = self._find_router_index(
            routers=payload["routers"],
            router_id=router_id,
        )
        if existing_index is None:
            payload["routers"].append(router_payload)
        else:
            payload["routers"][existing_index] = router_payload

        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        if existing_index is None:
            return updated_config.routers[-1]
        return updated_config.routers[existing_index]

    def set_mapping(
        self,
        *,
        path: Path,
        router_id: str,
        service_key: str,
        object_group_name: str,
        route_target_type: Literal["interface", "gateway"],
        route_target_value: str,
        route_interface: str | None,
        auto: bool,
        exclusive: bool,
    ) -> RouterServiceMappingConfig:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        mapping_payload = {
            "router_id": router_id,
            "service_key": service_key,
            "object_group_name": object_group_name,
            "route_target_type": route_target_type,
            "route_target_value": route_target_value,
            "route_interface": route_interface,
            "exclusive": exclusive,
            "auto": auto,
            "managed": True,
        }

        mappings = payload["mappings"]
        existing_index = self._find_mapping_index(
            mappings=mappings,
            router_id=router_id,
            service_key=service_key,
        )
        if existing_index is None:
            mappings.append(mapping_payload)
        else:
            mappings[existing_index] = mapping_payload

        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        if existing_index is None:
            return updated_config.mappings[-1]
        return updated_config.mappings[existing_index]

    def list_mappings(self, *, path: Path) -> list[RouterServiceMappingConfig]:
        return list(self._repository.load(path=path).mappings)

    def get_schedule(self, *, path: Path) -> RuntimeScheduleConfig:
        return self._repository.load(path=path).runtime.schedule

    def replace_schedule(
        self,
        *,
        path: Path,
        schedule: RuntimeScheduleConfig,
    ) -> RuntimeScheduleConfig:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        runtime_payload = payload.setdefault("runtime", {})
        runtime_payload["schedule"] = schedule.model_dump(mode="json")

        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        return updated_config.runtime.schedule

    def replace_router_mappings(
        self,
        *,
        path: Path,
        router_id: str,
        mappings: list[dict[str, Any]],
    ) -> list[RouterServiceMappingConfig]:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        payload["mappings"] = [
            mapping for mapping in payload["mappings"] if mapping["router_id"] != router_id
        ]
        payload["mappings"].extend(mappings)

        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        return [mapping for mapping in updated_config.mappings if mapping.router_id == router_id]

    def save_router_setup(
        self,
        *,
        path: Path,
        router_id: str,
        name: str,
        rci_url: str,
        username: str,
        password_env: str | None,
        password_file: str | None,
        enabled: bool,
        tags: list[str],
        timeout_seconds: int,
        allowed_source_ips: list[str],
        replace_mappings: list[dict[str, Any]] | None,
    ) -> RouterConfig:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        router_payload = {
            "id": router_id,
            "name": name,
            "rci_url": normalize_rci_url_input(rci_url),
            "username": username,
            "auth_method": "digest",
            "password_env": password_env,
            "password_file": password_file,
            "enabled": enabled,
            "tags": tags,
            "timeout_seconds": timeout_seconds,
            "allowed_source_ips": allowed_source_ips,
        }

        existing_index = self._find_router_index(
            routers=payload["routers"],
            router_id=router_id,
        )
        if existing_index is None:
            payload["routers"].append(router_payload)
        else:
            payload["routers"][existing_index] = router_payload

        if replace_mappings is not None:
            payload["mappings"] = [
                mapping for mapping in payload["mappings"] if mapping["router_id"] != router_id
            ]
            payload["mappings"].extend(replace_mappings)

        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        if existing_index is None:
            return updated_config.routers[-1]
        return updated_config.routers[existing_index]

    def update_router_secret_reference(
        self,
        *,
        path: Path,
        router_id: str,
        password_env: str,
        password_file: str | None,
    ) -> RouterConfig:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        existing_index = self._find_router_index(
            routers=payload["routers"],
            router_id=router_id,
        )
        if existing_index is None:
            raise RuntimeError(f"Router '{router_id}' does not exist")

        router_payload = dict(payload["routers"][existing_index])
        router_payload["password_env"] = password_env
        router_payload["password_file"] = password_file
        payload["routers"][existing_index] = router_payload

        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        return updated_config.routers[existing_index]

    def remove_mapping(self, *, path: Path, router_id: str, service_key: str) -> bool:
        config = self._repository.load(path=path)
        payload = config.model_dump(mode="json")
        mappings = payload["mappings"]
        existing_index = self._find_mapping_index(
            mappings=mappings,
            router_id=router_id,
            service_key=service_key,
        )
        if existing_index is None:
            return False

        del mappings[existing_index]
        updated_config = self._repository.validate_payload(path=path, payload=payload)
        self._repository.overwrite(path=path, config=updated_config)
        return True

    def _find_router_index(
        self,
        *,
        routers: list[dict[str, Any]],
        router_id: str,
    ) -> int | None:
        for index, router in enumerate(routers):
            if router["id"] == router_id:
                return index
        return None

    def _find_mapping_index(
        self,
        *,
        mappings: list[dict[str, Any]],
        router_id: str,
        service_key: str,
    ) -> int | None:
        for index, mapping in enumerate(mappings):
            if mapping["router_id"] == router_id and mapping["service_key"] == service_key:
                return index
        return None
