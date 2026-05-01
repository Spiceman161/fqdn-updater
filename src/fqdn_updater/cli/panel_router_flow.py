from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from fqdn_updater.application.config_management import normalize_rci_url_input
from fqdn_updater.cli.panel import (
    ADD_ROUTER_HINT_LINES,
    ADD_ROUTER_PASSWORD_HINT_LINES,
    ADD_ROUTER_RCI_URL_HINT_LINES,
    ADD_ROUTER_SAVE_HINT_LINES,
    ADD_ROUTER_USERNAME_HINT_LINES,
    BASE_ROUTE_INTERFACE_HINT_LINES,
    DEFAULT_RCI_TIMEOUT_SECONDS,
    DEFAULT_SELECTED_SERVICES,
    EDIT_ROUTER_PASSWORD_HINT_LINES,
    GOOGLE_AI_OVERRIDE_HINT_LINES,
    SERVICE_SELECTION_HINT_LINES,
    MappingPlan,
    RouteTargetDraft,
    _default_interface_target_value,
    _derive_mapping_plan_defaults,
    _derive_router_id,
    _ensure_password_env_available,
    _is_missing_password_env_error,
)
from fqdn_updater.cli.panel_formatting import (
    ServiceEntryCounts,
    _effective_service_selection,
    _enabled_service_selection_groups,
    _find_router,
    _format_service_list,
    _format_validation_error,
    _route_candidate_title,
    _router_selection_column_widths,
    _router_selection_title,
    _router_toggle_header,
    _router_toggle_summary,
    _router_toggle_title,
    _service_display_label,
    _service_entry_counts_from_report,
    _service_entry_counts_from_snapshot,
    _service_selection_header,
    _service_selection_title,
    _service_selection_totals_line,
)
from fqdn_updater.cli.panel_prompts import CheckboxTableMeta, PromptChoice
from fqdn_updater.domain.config_schema import (
    AppConfig,
    RouterConfig,
    RouterServiceMappingConfig,
)
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)

if TYPE_CHECKING:
    from fqdn_updater.cli.panel import PanelController


class PanelRouterFlow:
    """Router management workflows for the interactive panel."""

    def __init__(self, *, panel: PanelController) -> None:
        self._panel = panel

    def __getattr__(self, name: str) -> Any:
        return getattr(self._panel, name)

    def add_router(self) -> bool:
        config = self._load_config()
        name = self._prompts.text(
            message="Имя маршрутизатора",
            default="",
            hint_lines=ADD_ROUTER_HINT_LINES,
        )
        if name is None:
            return False
        router_id = _derive_router_id(name=name, config=config)
        username = self._prompts.text(
            message="RCI username",
            default="api_updater",
            hint_lines=ADD_ROUTER_USERNAME_HINT_LINES,
        )
        if username is None:
            return False
        username = username.strip()
        if not username:
            self._console.print("[yellow]RCI username не может быть пустым.[/yellow]")
            self._pause()
            return False

        password_env = password_env_key_for_router_id(router_id)
        _ensure_password_env_available(
            config=config,
            router_id=router_id,
            password_env=password_env,
        )
        password = self._password_generator.generate()
        self._render_summary(
            title="Новый пароль RCI",
            rows=[
                ("Username", username),
                ("Пароль", password),
            ],
        )
        password_ready = self._prompts.confirm(
            message="Пароль уже введён для нового пользователя Keenetic?",
            default=True,
            hint_lines=ADD_ROUTER_PASSWORD_HINT_LINES,
        )
        if password_ready is not True:
            return False

        rci_url = self._prompts.text(
            message="KeenDNS RCI URL",
            default="",
            hint_lines=ADD_ROUTER_RCI_URL_HINT_LINES,
        )
        if rci_url is None:
            return False
        timeout_seconds = DEFAULT_RCI_TIMEOUT_SECONDS

        try:
            draft_router = RouterConfig.model_validate(
                {
                    "id": router_id,
                    "name": name,
                    "rci_url": normalize_rci_url_input(rci_url),
                    "username": username,
                    "password_env": password_env,
                    "password_file": None,
                    "enabled": True,
                    "tags": [],
                    "timeout_seconds": timeout_seconds,
                    "allowed_source_ips": [],
                }
            )
        except ValidationError as exc:
            self._console.print(
                "[yellow]Некорректные параметры маршрутизатора:[/yellow] "
                f"{_format_validation_error(exc)}"
            )
            self._pause()
            return False

        selected_services = self.prompt_service_selection(
            config=config,
            selected=DEFAULT_SELECTED_SERVICES,
            hint_lines=ADD_ROUTER_HINT_LINES,
        )
        if selected_services is None:
            return False

        mapping_plan = None
        if selected_services:
            mapping_plan = self.prompt_mapping_plan(
                config=config,
                router=draft_router,
                editable_mappings=[],
                selected_services=selected_services,
                discovery_password=password,
                hint_lines=ADD_ROUTER_HINT_LINES,
            )
            if mapping_plan is None:
                return False

        new_mappings = self.build_router_mappings(
            router_id=router_id,
            selected_services=selected_services,
            existing_mappings={},
            mapping_plan=mapping_plan,
        )

        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "добавить маршрутизатор"),
                ("Маршрутизатор", router_id),
                ("Имя", name),
                ("RCI URL", normalize_rci_url_input(rci_url)),
                ("Username", username),
                ("Timeout", str(timeout_seconds)),
                ("Password env", password_env),
                ("Сервисы", _format_service_list(sorted(selected_services)) or "нет"),
                (
                    "Базовый target",
                    mapping_plan.default_target.summary() if mapping_plan is not None else "нет",
                ),
                (
                    "google_ai override",
                    (
                        mapping_plan.google_ai_target.summary()
                        if mapping_plan is not None and mapping_plan.google_ai_target is not None
                        else "нет"
                    ),
                ),
            ],
        )
        should_save = self._prompts.confirm(
            message="Сохранить новый маршрутизатор?",
            default=True,
            hint_lines=ADD_ROUTER_SAVE_HINT_LINES,
        )
        if not should_save:
            return False

        self._management_service.save_router_setup(
            path=self._config_path,
            router_id=router_id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            password_file=None,
            enabled=True,
            tags=[],
            timeout_seconds=timeout_seconds,
            allowed_source_ips=[],
            replace_mappings=new_mappings,
        )
        try:
            SecretEnvFile(path=self._secrets_env_path(config=config)).write_value(
                key=password_env,
                value=password,
            )
        except Exception:
            self._repository.overwrite(path=self._config_path, config=config)
            raise

        self._console.print("[green]Маршрутизатор сохранён. Пароль записан в secrets env.[/green]")
        self._pause()
        return True

    def edit_router(self) -> None:
        config = self._load_config()
        router = self.select_router(
            config=config,
            message="Выберите маршрутизатор для редактирования",
            back_title="Назад",
        )
        if router is None:
            return

        name = self._prompts.text(message="Имя маршрутизатора", default=router.name)
        if name is None:
            return
        rci_url = self._prompts.text(message="KeenDNS RCI URL", default=str(router.rci_url))
        if rci_url is None:
            return
        username = self._prompts.text(message="RCI username", default=router.username)
        if username is None:
            return
        username = username.strip()
        if not username:
            self._console.print("[yellow]RCI username не может быть пустым.[/yellow]")
            self._pause()
            return

        next_password_env = router.password_env or password_env_key_for_router_id(router.id)
        password = self._password_generator.generate()
        self._render_summary(
            title="Новый пароль RCI",
            rows=[
                ("Username", username),
                ("Password env", next_password_env),
                (
                    "Password file",
                    (
                        f"{router.password_file} (будет очищен)"
                        if router.password_file is not None
                        else "нет"
                    ),
                ),
                ("Пароль", password),
            ],
        )
        password_ready = self._prompts.confirm(
            message="Пароль уже обновлён у пользователя Keenetic?",
            default=True,
            hint_lines=EDIT_ROUTER_PASSWORD_HINT_LINES,
        )
        if password_ready is None:
            return
        update_password = password_ready
        if update_password:
            _ensure_password_env_available(
                config=config,
                router_id=router.id,
                password_env=next_password_env,
            )
        password_env = next_password_env if update_password else router.password_env
        password_file = None if update_password else router.password_file
        connectivity_password_override = password if update_password else None
        timeout_seconds = DEFAULT_RCI_TIMEOUT_SECONDS

        try:
            draft_router = RouterConfig.model_validate(
                {
                    "id": router.id,
                    "name": name,
                    "rci_url": normalize_rci_url_input(rci_url),
                    "username": username,
                    "password_env": password_env,
                    "password_file": password_file,
                    "enabled": router.enabled,
                    "tags": list(router.tags),
                    "timeout_seconds": timeout_seconds,
                    "allowed_source_ips": list(router.allowed_source_ips),
                }
            )
        except ValidationError as exc:
            self._console.print(
                "[yellow]Некорректные параметры маршрутизатора:[/yellow] "
                f"{_format_validation_error(exc)}"
            )
            self._pause()
            return

        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "изменить маршрутизатор"),
                ("Маршрутизатор", router.id),
                ("Имя", name),
                ("RCI URL", normalize_rci_url_input(rci_url)),
                ("Username", username),
                ("Timeout", str(timeout_seconds)),
                ("Статус", "включён" if router.enabled else "выключен"),
                ("Password env", password_env or "нет"),
                ("Password file", password_file or "нет"),
            ],
        )
        connectivity_error = self.probe_router_connectivity(
            config=config,
            router=draft_router,
            password_override=connectivity_password_override,
        )
        if connectivity_error is not None:
            self._print_router_connectivity_error()
            self._print_discovery_error(connectivity_error)
        should_save = self._prompts.confirm(
            message="Сохранить изменения маршрутизатора?",
            default=True,
        )
        if not should_save:
            return

        self._management_service.replace_router(
            path=self._config_path,
            router_id=router.id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            password_file=password_file,
            enabled=router.enabled,
            tags=list(router.tags),
            timeout_seconds=timeout_seconds,
            allowed_source_ips=list(router.allowed_source_ips),
        )
        if update_password:
            assert password_env is not None
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).write_value(
                    key=password_env,
                    value=password,
                )
            except Exception:
                self._repository.overwrite(path=self._config_path, config=config)
                raise
            self._console.print(
                "[green]Параметры маршрутизатора обновлены. Пароль записан в secrets env.[/green]"
            )
        else:
            self._console.print("[green]Параметры маршрутизатора обновлены.[/green]")
        self._pause()

    def toggle_router_enabled(self) -> None:
        config = self._load_config()
        if not config.routers:
            self._console.print("[yellow]Нет настроенных маршрутизаторов.[/yellow]")
            self._pause()
            return

        router_id_width, router_name_width = _router_selection_column_widths(config.routers)
        selected_enabled_router_ids = self._prompts.checkbox(
            message="Выберите маршрутизаторы, которые должны быть включены",
            choices=[
                PromptChoice(
                    title=_router_toggle_title(
                        router=router,
                        router_id_width=router_id_width,
                        router_name_width=router_name_width,
                    ),
                    value=router.id,
                    checked=router.enabled,
                    answer_title=router.id,
                )
                for router in config.routers
            ],
            instruction=(
                "Стрелки выбирают, Пробел включает или выключает, Enter сохраняет, Esc назад."
            ),
            table_meta=CheckboxTableMeta(
                header=_router_toggle_header(
                    router_id_width=router_id_width,
                    router_name_width=router_name_width,
                ),
                summary=lambda selected_values: _router_toggle_summary(
                    selected_values=selected_values,
                    total=len(config.routers),
                ),
            ),
        )
        if selected_enabled_router_ids is None:
            return

        enabled_router_ids = set(selected_enabled_router_ids)
        current_enabled_router_ids = {router.id for router in config.routers if router.enabled}
        if enabled_router_ids == current_enabled_router_ids:
            return

        payload = config.model_dump(mode="json")
        for router_payload in payload["routers"]:
            router_payload["enabled"] = router_payload["id"] in enabled_router_ids

        updated_config = self._repository.validate_payload(path=self._config_path, payload=payload)
        self._repository.overwrite(
            path=self._config_path,
            config=updated_config,
        )
        self._console.print("[green]Статусы маршрутизаторов обновлены.[/green]")
        self._pause()

    def select_router(
        self,
        *,
        config: AppConfig,
        message: str,
        back_title: str = "Назад",
    ) -> RouterConfig | None:
        if not config.routers:
            self._console.print("[yellow]Нет настроенных маршрутизаторов.[/yellow]")
            self._pause()
            return None

        router_id_width, router_name_width = _router_selection_column_widths(config.routers)
        choices = [
            PromptChoice(
                title=_router_selection_title(
                    router=router,
                    router_id_width=router_id_width,
                    router_name_width=router_name_width,
                ),
                value=router.id,
            )
            for router in config.routers
        ]
        choices.append(PromptChoice(back_title, "__back__"))
        selected_router_id = self._prompts.select(
            message=message,
            choices=choices,
            default=config.routers[0].id,
        )
        if selected_router_id in {None, "__back__"}:
            return None
        return _find_router(config=config, router_id=selected_router_id)

    def prompt_service_selection(
        self,
        *,
        config: AppConfig,
        selected: set[str],
        hint_lines: tuple[str, ...] | None = None,
    ) -> set[str] | None:
        service_counts = self.load_service_entry_counts(config=config)
        enabled_service_keys = {service.key for service in config.services if service.enabled}
        selection_groups = _enabled_service_selection_groups(enabled_service_keys)
        service_choices = [
            PromptChoice(
                title=_service_selection_title(
                    service_key=service.key,
                    counts=service_counts.get(service.key),
                ),
                value=service.key,
                checked=service.key in selected,
                answer_title=_service_display_label(service.key),
            )
            for service in config.services
            if service.enabled
        ]
        if not service_choices:
            self._console.print("[yellow]Нет enabled сервисов для выбора.[/yellow]")
            self._pause()
            return set()

        result = self._prompts.checkbox(
            message="Выберите управляемые сервисы",
            choices=service_choices,
            instruction=(
                "Стрелки выбирают, Пробел отмечает, Enter сохраняет набор, Esc возвращает назад."
            ),
            hint_lines=SERVICE_SELECTION_HINT_LINES,
            table_meta=CheckboxTableMeta(
                header=_service_selection_header(),
                summary=lambda selected_values: _service_selection_totals_line(
                    selected_values=selected_values,
                    service_counts=service_counts,
                ),
                selection_groups=selection_groups,
            ),
        )
        if result is None:
            return None
        return _effective_service_selection(result)

    def load_service_entry_counts(self, *, config: AppConfig) -> dict[str, ServiceEntryCounts]:
        enabled_services = [service for service in config.services if service.enabled]
        if not enabled_services:
            return {}

        cache_path = self._service_count_cache_path(config=config)
        cached_counts = self._service_count_cache_repository.read(path=cache_path)
        enabled_service_keys = {service.key for service in enabled_services}
        if cached_counts and enabled_service_keys.issubset(cached_counts):
            return {
                service.key: _service_entry_counts_from_snapshot(cached_counts.get(service.key))
                for service in enabled_services
            }

        self._console.print("[dim]Считаю текущий размер исходных списков...[/dim]")
        report = self._service_count_source_loader(config=config).load_enabled_services(
            enabled_services
        )
        updated_counts = self._service_count_cache_repository.read(path=cache_path)
        if updated_counts:
            return {
                service.key: _service_entry_counts_from_snapshot(updated_counts.get(service.key))
                for service in enabled_services
            }
        return _service_entry_counts_from_report(
            services=enabled_services,
            report=report,
        )

    def prompt_mapping_plan(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        editable_mappings: list[RouterServiceMappingConfig],
        selected_services: set[str],
        missing_secret_message: str | None = None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> MappingPlan | None:
        default_target, has_inconsistent_default, google_ai_override = (
            _derive_mapping_plan_defaults(
                editable_mappings=editable_mappings,
            )
        )
        if has_inconsistent_default:
            self._console.print(
                "[yellow]У текущих non-google_ai mappings разные route targets. "
                "После сохранения они будут нормализованы к одному базовому target.[/yellow]"
            )

        default_target = self.prompt_route_target(
            config=config,
            router=router,
            label="Базовый интерфейс маршрутизации",
            default_target=default_target,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
            hint_lines=BASE_ROUTE_INTERFACE_HINT_LINES,
        )
        if default_target is None:
            return None

        google_ai_target = None
        has_non_google_services = any(
            service_key != "google_ai" for service_key in selected_services
        )
        if "google_ai" in selected_services and has_non_google_services:
            use_override = self._prompts.confirm(
                message="Использовать отдельный маршрут для google_ai?",
                default=google_ai_override is not None,
                hint_lines=GOOGLE_AI_OVERRIDE_HINT_LINES,
            )
            if use_override is None:
                return None
            if use_override:
                google_ai_target = self.prompt_route_target(
                    config=config,
                    router=router,
                    label="Route target для google_ai",
                    default_target=google_ai_override or default_target,
                    missing_secret_message=missing_secret_message,
                    discovery_password=discovery_password,
                    hint_lines=hint_lines,
                )
                if google_ai_target is None:
                    return None

        return MappingPlan(default_target=default_target, google_ai_target=google_ai_target)

    def prompt_route_target(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        label: str,
        default_target: RouteTargetDraft,
        missing_secret_message: str | None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> RouteTargetDraft | None:
        return self.prompt_interface_target(
            config=config,
            router=router,
            label=label,
            default_value=_default_interface_target_value(default_target),
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
            hint_lines=hint_lines,
        )

    def prompt_interface_target(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        label: str,
        default_value: str,
        missing_secret_message: str | None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> RouteTargetDraft | None:
        candidates = self.discover_route_targets(
            config=config,
            router=router,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
        )
        if candidates:
            self._render_route_target_candidates(candidates=candidates)
            choices = [
                PromptChoice(
                    title=_route_candidate_title(candidate),
                    value=candidate.value,
                )
                for candidate in candidates
            ]
            choices.append(PromptChoice("Ввести интерфейс вручную", "manual"))
            choices.append(PromptChoice("Назад", "__back__"))
            default_choice = (
                default_value
                if any(candidate.value == default_value for candidate in candidates)
                else "manual"
            )
            selected_value = self._prompts.select(
                message=label,
                choices=choices,
                default=default_choice,
                hint_lines=hint_lines,
            )
            if selected_value in {None, "__back__"}:
                return None
            if selected_value != "manual":
                return RouteTargetDraft(
                    route_target_type="interface",
                    route_target_value=selected_value,
                    route_interface=None,
                )

        manual_value = self._prompts.text(
            message=label,
            default=default_value,
            instruction="Введите имя интерфейса Keenetic.",
            hint_lines=hint_lines,
        )
        if manual_value is None:
            return None
        normalized_value = manual_value.strip() or default_value
        return RouteTargetDraft(
            route_target_type="interface",
            route_target_value=normalized_value,
            route_interface=None,
        )

    def prompt_gateway_target(
        self,
        *,
        label: str,
        default_target: RouteTargetDraft,
        hint_lines: tuple[str, ...] | None = None,
    ) -> RouteTargetDraft | None:
        default_gateway = (
            default_target.route_target_value
            if default_target.route_target_type == "gateway"
            else ""
        )
        default_interface = default_target.route_interface or ""

        gateway_value = self._prompts.text(
            message=f"{label}: IP или адрес шлюза",
            default=default_gateway,
            hint_lines=hint_lines,
        )
        if gateway_value is None:
            return None
        route_interface = self._prompts.text(
            message=f"{label}: интерфейс для gateway route (опционально)",
            default=default_interface,
            instruction="Оставьте пустым, если интерфейс не нужен.",
            hint_lines=hint_lines,
        )
        if route_interface is None:
            return None

        normalized_gateway = gateway_value.strip()
        normalized_interface = route_interface.strip() or None
        return RouteTargetDraft(
            route_target_type="gateway",
            route_target_value=normalized_gateway,
            route_interface=normalized_interface,
        )

    def build_router_mappings(
        self,
        *,
        router_id: str,
        selected_services: set[str],
        existing_mappings: dict[str, RouterServiceMappingConfig],
        mapping_plan: MappingPlan | None,
    ) -> list[dict[str, Any]]:
        if not selected_services:
            return []
        if mapping_plan is None:
            raise RuntimeError("mapping plan is required when services are selected")

        mappings: list[dict[str, Any]] = []
        for service_key in sorted(selected_services):
            existing_mapping = existing_mappings.get(service_key)
            target = mapping_plan.target_for_service(service_key)
            mappings.append(
                {
                    "router_id": router_id,
                    "service_key": service_key,
                    "object_group_name": (
                        existing_mapping.object_group_name
                        if existing_mapping is not None
                        else f"fqdn-{service_key}"
                    ),
                    "route_target_type": target.route_target_type,
                    "route_target_value": target.route_target_value,
                    "route_interface": target.route_interface,
                    "exclusive": (
                        existing_mapping.exclusive if existing_mapping is not None else True
                    ),
                    "auto": existing_mapping.auto if existing_mapping is not None else True,
                    "managed": existing_mapping.managed if existing_mapping is not None else True,
                }
            )
        return mappings

    def discover_route_targets(
        self,
        *,
        config: AppConfig,
        router: RouterConfig | None,
        missing_secret_message: str | None = None,
        discovery_password: str | None = None,
    ) -> tuple[RouteTargetCandidate, ...]:
        if router is None:
            return ()

        if discovery_password is None:
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()
            except RuntimeError as exc:
                self._print_discovery_error(str(exc))
                return ()

        result = self._route_target_discovery_service.discover_wireguard_targets(
            router=router,
            password_override=discovery_password,
        )
        if result.error_message is not None:
            if missing_secret_message is not None and _is_missing_password_env_error(
                result.error_message
            ):
                self._console.print(f"[yellow]{missing_secret_message}[/yellow]")
                return ()
            self._print_discovery_error(result.error_message)
            return ()
        if not result.candidates:
            self._console.print("[yellow]WireGuard интерфейсы не обнаружены.[/yellow]")
            return ()
        return result.candidates

    def probe_router_connectivity(
        self,
        *,
        config: AppConfig,
        router: RouterConfig | None,
        password_override: str | None = None,
    ) -> str | None:
        if router is None:
            return None

        if password_override is None:
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()
            except RuntimeError as exc:
                return str(exc)

        result = self._route_target_discovery_service.discover_wireguard_targets(
            router=router,
            password_override=password_override,
        )
        return result.error_message
