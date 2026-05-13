from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from fqdn_updater.application.config_management import normalize_rci_url_input
from fqdn_updater.cli import panel_formatting
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
from fqdn_updater.cli.panel_router_support import (
    ADD_ROUTER_HINT_LINES,
    ADD_ROUTER_PASSWORD_HINT_LINES,
    ADD_ROUTER_RCI_URL_HINT_LINES,
    ADD_ROUTER_SAVE_HINT_LINES,
    ADD_ROUTER_USERNAME_HINT_LINES,
    BASE_ROUTE_INTERFACE_HINT_LINES,
    DEFAULT_RCI_TIMEOUT_SECONDS,
    DEFAULT_SELECTED_DIRECT_SERVICES,
    DEFAULT_SELECTED_SERVICES,
    DELETE_ROUTER_HINT_LINES,
    DIRECT_SERVICE_KEYS,
    EDIT_ROUTER_PASSWORD_HINT_LINES,
    GOOGLE_AI_OVERRIDE_HINT_LINES,
    SERVICE_SELECTION_HINT_LINES,
    MappingPlan,
    RouteTargetDraft,
    default_interface_target_value,
    derive_mapping_plan_defaults,
    derive_router_id,
    ensure_password_env_available,
    find_interface_state,
    first_provider_interface_value,
    is_missing_password_env_error,
)
from fqdn_updater.domain.config_schema import (
    AppConfig,
    RouterConfig,
    RouterServiceMappingConfig,
)
from fqdn_updater.domain.keenetic import RouterInterfaceState, RouteTargetCandidate
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
        self._last_interface_discovery: tuple[str, tuple[RouterInterfaceState, ...]] | None = None
        self._last_discovery_failed = False

    @property
    def last_discovery_failed(self) -> bool:
        return self._last_discovery_failed

    @property
    def _config_path(self):
        return self._panel._config_path

    @property
    def _console(self):
        return self._panel._console

    @property
    def _prompts(self):
        return self._panel._prompts

    @property
    def _repository(self):
        return self._panel._repository

    @property
    def _management_service(self):
        return self._panel._management_service

    @property
    def _password_generator(self):
        return self._panel._password_generator

    @property
    def _route_target_discovery_service(self):
        return self._panel._route_target_discovery_service

    @property
    def _service_count_cache_repository(self):
        return self._panel._service_count_cache_repository

    def _load_config(self):
        return self._panel._load_config()

    def _pause(self):
        return self._panel._pause()

    def _render_summary(self, *, title, rows):
        return self._panel._render_summary(title=title, rows=rows)

    def _secrets_env_path(self, *, config):
        return self._panel._secrets_env_path(config=config)

    def _service_count_cache_path(self, *, config):
        return self._panel._service_count_cache_path(config=config)

    def _service_count_source_loader(self, *, config):
        return self._panel._service_count_source_loader(config=config)

    def _render_route_target_candidates(self, *, candidates):
        return self._panel._render_route_target_candidates(candidates=candidates)

    def _print_discovery_error(self, message):
        return self._panel._print_discovery_error(message)

    def _print_router_connectivity_error(self):
        return self._panel._print_router_connectivity_error()

    def add_router(self) -> bool:
        config = self._load_config()
        name = self._prompts.text(
            message="Имя маршрутизатора",
            default="",
            hint_lines=ADD_ROUTER_HINT_LINES,
        )
        if name is None:
            return False
        router_id = derive_router_id(name=name, config=config)
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
        ensure_password_env_available(
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

        default_route_interface = self.prompt_default_route_interface(
            config=config,
            router=draft_router,
            default_value="",
            discovery_password=password,
            hint_lines=BASE_ROUTE_INTERFACE_HINT_LINES,
        )
        if default_route_interface is None:
            return False
        discovered_interfaces = self.cached_discovered_interfaces(router=draft_router)
        default_interface_state = find_interface_state(
            interfaces=discovered_interfaces,
            value=default_route_interface,
        )
        direct_services_available = _has_enabled_direct_services(config)
        use_default_interface_for_mappings = False
        default_is_vpn = default_interface_state.is_vpn_like if default_interface_state else None
        if default_is_vpn is None and not direct_services_available:
            default_is_vpn = False
            use_default_interface_for_mappings = True
        elif default_is_vpn is None:
            default_is_vpn = self.prompt_default_route_mode()
        if default_is_vpn is None:
            return False
        if default_is_vpn and not direct_services_available:
            use_default_interface_for_mappings = True
            default_is_vpn = False
        service_filter = DIRECT_SERVICE_KEYS if default_is_vpn else None
        default_selected_services = (
            set(DEFAULT_SELECTED_DIRECT_SERVICES)
            if default_is_vpn
            else set(DEFAULT_SELECTED_SERVICES)
        )
        selected_services = self.prompt_service_selection(
            config=config,
            selected=default_selected_services,
            allowed_service_keys=service_filter,
            hint_lines=ADD_ROUTER_HINT_LINES,
        )
        if selected_services is None:
            return False

        mapping_plan = None
        if selected_services:
            if use_default_interface_for_mappings:
                mapping_plan = MappingPlan(
                    default_target=RouteTargetDraft("interface", default_route_interface)
                )
                if "google_ai" in selected_services and any(
                    service_key != "google_ai" for service_key in selected_services
                ):
                    use_override = self._prompts.confirm(
                        message="Использовать отдельный маршрут для google_ai?",
                        default=False,
                        hint_lines=GOOGLE_AI_OVERRIDE_HINT_LINES,
                    )
                    if use_override is None:
                        return False
                    if use_override:
                        google_ai_target = self.prompt_route_target(
                            config=config,
                            router=draft_router,
                            label="Route target для google_ai",
                            default_target=mapping_plan.default_target,
                            missing_secret_message=None,
                            discovery_password=password,
                            hint_lines=ADD_ROUTER_HINT_LINES,
                        )
                        if google_ai_target is None:
                            return False
                        mapping_plan = MappingPlan(
                            default_target=mapping_plan.default_target,
                            google_ai_target=google_ai_target,
                        )
            elif default_is_vpn:
                provider_default = first_provider_interface_value(discovered_interfaces)
                mapping_plan = MappingPlan(
                    default_target=RouteTargetDraft("interface", provider_default)
                )
            else:
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
                ("Default route", f"interface:{default_route_interface}"),
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
            default_route={"interface": default_route_interface, "managed": True},
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

        self._console.print(
            f"[green]{panel_formatting.ICON_SAVE} Маршрутизатор сохранён. "
            "Пароль записан в secrets env.[/green]"
        )
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
            ensure_password_env_available(
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
                    "default_route": (
                        router.default_route.model_dump(mode="json")
                        if router.default_route is not None
                        else None
                    ),
                }
            )
        except ValidationError as exc:
            self._console.print(
                "[yellow]Некорректные параметры маршрутизатора:[/yellow] "
                f"{_format_validation_error(exc)}"
            )
            self._pause()
            return

        default_route_interface = self.prompt_default_route_interface(
            config=config,
            router=draft_router,
            default_value=router.default_route.interface if router.default_route else "",
            discovery_password=connectivity_password_override,
        )
        if default_route_interface is None:
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
                ("Default route", f"interface:{default_route_interface}"),
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
            default_route={"interface": default_route_interface, "managed": True},
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
                f"[green]{panel_formatting.ICON_SAVE} Параметры маршрутизатора обновлены. "
                "Пароль записан в secrets env.[/green]"
            )
        else:
            self._console.print(
                f"[green]{panel_formatting.ICON_SAVE} Параметры маршрутизатора обновлены.[/green]"
            )
        self._pause()

    def delete_router(self) -> None:
        config = self._load_config()
        router = self.select_router(
            config=config,
            message="Выберите маршрутизатор для удаления",
            back_title="Назад",
        )
        if router is None:
            return

        removed_mappings = [
            mapping for mapping in config.mappings if mapping.router_id == router.id
        ]
        self._render_summary(
            title="Проверка удаления",
            rows=[
                ("Операция", "удалить маршрутизатор"),
                ("Маршрутизатор", router.id),
                ("Имя", router.name),
                ("RCI URL", str(router.rci_url)),
                ("Статус", "включён" if router.enabled else "выключен"),
                ("Удаляемые mappings", str(len(removed_mappings))),
                ("Password env", router.password_env or "нет"),
                ("Password file", router.password_file or "нет"),
                ("Секреты", "не изменяются"),
            ],
        )
        should_delete = self._prompts.confirm(
            message="Удалить маршрутизатор из config.json?",
            default=False,
            hint_lines=DELETE_ROUTER_HINT_LINES,
        )
        if should_delete is not True:
            return

        removed = self._management_service.remove_router(
            path=self._config_path,
            router_id=router.id,
        )
        if not removed:
            self._console.print("[yellow]Маршрутизатор уже отсутствует в config.json.[/yellow]")
            self._pause()
            return

        self._console.print(
            f"[green]{panel_formatting.ICON_SAVE} Маршрутизатор удалён. "
            f"Удалены mappings: {len(removed_mappings)}.[/green]"
        )
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
        self._console.print(
            f"[green]{panel_formatting.ICON_SAVE} Статусы маршрутизаторов обновлены.[/green]"
        )
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
        allowed_service_keys: frozenset[str] | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> set[str] | None:
        service_counts = self.load_service_entry_counts(config=config)
        enabled_service_keys = {
            service.key
            for service in config.services
            if service.enabled
            and (allowed_service_keys is None or service.key in allowed_service_keys)
        }
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
            and (allowed_service_keys is None or service.key in allowed_service_keys)
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

        self._console.print(
            f"[dim]{panel_formatting.ICON_SEARCH} Считаю текущий размер исходных списков...[/dim]"
        )
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
        abort_on_discovery_error: bool = False,
    ) -> MappingPlan | None:
        default_target, has_inconsistent_default, google_ai_override = derive_mapping_plan_defaults(
            editable_mappings=editable_mappings,
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
            abort_on_discovery_error=abort_on_discovery_error,
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
                    abort_on_discovery_error=abort_on_discovery_error,
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
        abort_on_discovery_error: bool = False,
    ) -> RouteTargetDraft | None:
        return self.prompt_interface_target(
            config=config,
            router=router,
            label=label,
            default_value=default_interface_target_value(default_target),
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
            hint_lines=hint_lines,
            abort_on_discovery_error=abort_on_discovery_error,
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
        vpn_only: bool = True,
        abort_on_discovery_error: bool = False,
    ) -> RouteTargetDraft | None:
        if vpn_only:
            candidates = self.discover_route_targets(
                config=config,
                router=router,
                missing_secret_message=missing_secret_message,
                discovery_password=discovery_password,
            )
            render_candidates = True
        else:
            interfaces = self.discover_interfaces(
                config=config,
                router=router,
                missing_secret_message=missing_secret_message,
                discovery_password=discovery_password,
            )
            candidates = _route_candidates_from_interfaces(_global_route_interfaces(interfaces))
            render_candidates = False

        if not candidates and abort_on_discovery_error and self._last_discovery_failed:
            return None
        if candidates:
            if render_candidates:
                self._render_route_target_candidates(candidates=candidates)
            choices = [
                PromptChoice(
                    title=_route_candidate_title(candidate),
                    value=candidate.value,
                )
                for candidate in candidates
            ]
            choices.append(
                _flow_choice(panel_formatting.ICON_EDIT, "Ввести интерфейс вручную", "manual")
            )
            choices.append(_flow_choice(panel_formatting.ICON_BACK, "Назад", "__back__"))
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

    def prompt_default_route_interface(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        default_value: str,
        label: str = "Базовый интерфейс маршрутизации",
        missing_secret_message: str | None = None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
        abort_on_discovery_error: bool = False,
    ) -> str | None:
        prompt_hint_lines = BASE_ROUTE_INTERFACE_HINT_LINES if hint_lines is None else hint_lines
        interfaces = self.discover_interfaces(
            config=config,
            router=router,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
        )
        if not interfaces and abort_on_discovery_error and self._last_discovery_failed:
            return None
        global_interfaces = _global_route_interfaces(interfaces)
        if global_interfaces:
            choice_widths = _interface_choice_widths(global_interfaces)
            choices = [
                PromptChoice(
                    title=_interface_choice_title(interface, widths=choice_widths),
                    value=interface.value,
                )
                for interface in global_interfaces
            ]
            choices.append(_flow_choice(panel_formatting.ICON_BACK, "Назад", "__back__"))
            default_choice = (
                default_value
                if any(interface.value == default_value for interface in global_interfaces)
                else global_interfaces[0].value
            )
            selected_value = self._prompts.select(
                message=label,
                choices=choices,
                default=default_choice,
                hint_lines=prompt_hint_lines,
            )
            if selected_value in {None, "__back__"}:
                return None
            return selected_value

        if interfaces:
            self._console.print("[yellow]Интерфейсы с Global=yes не обнаружены.[/yellow]")
        return None

    def prompt_default_route_mode(self) -> bool | None:
        selected_mode = self._prompts.select(
            message="Режим списков для default route",
            choices=[
                _flow_choice(panel_formatting.ICON_ROUTE, "default VPN, direct groups", "vpn"),
                _flow_choice(
                    panel_formatting.ICON_ROUTE,
                    "default provider, VPN-routed groups",
                    "provider",
                ),
                _flow_choice(panel_formatting.ICON_BACK, "Назад", "__back__"),
            ],
            default="vpn",
        )
        if selected_mode in {None, "__back__"}:
            return None
        return selected_mode == "vpn"

    def discover_interfaces(
        self,
        *,
        config: AppConfig,
        router: RouterConfig | None,
        missing_secret_message: str | None = None,
        discovery_password: str | None = None,
    ) -> tuple[RouterInterfaceState, ...]:
        self._last_discovery_failed = False
        if router is None:
            return ()

        if discovery_password is None:
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()
            except RuntimeError as exc:
                self._last_discovery_failed = True
                self._print_discovery_error(str(exc))
                return ()

        if hasattr(self._route_target_discovery_service, "discover_interfaces"):
            result = self._route_target_discovery_service.discover_interfaces(
                router=router,
                password_override=discovery_password,
            )
        else:
            wireguard_result = self._route_target_discovery_service.discover_wireguard_targets(
                router=router,
                password_override=discovery_password,
            )
            result = _interface_result_from_route_targets(wireguard_result)
        if result.error_message is not None:
            if missing_secret_message is not None and is_missing_password_env_error(
                result.error_message
            ):
                self._console.print(f"[yellow]{missing_secret_message}[/yellow]")
                return ()
            self._last_discovery_failed = True
            self._print_discovery_error(result.error_message)
            return ()
        if not result.interfaces:
            self._console.print("[yellow]Интерфейсы не обнаружены.[/yellow]")
            return ()
        self._last_interface_discovery = (router.id, result.interfaces)
        return result.interfaces

    def cached_discovered_interfaces(
        self,
        *,
        router: RouterConfig,
    ) -> tuple[RouterInterfaceState, ...]:
        if (
            self._last_interface_discovery is not None
            and self._last_interface_discovery[0] == router.id
        ):
            return self._last_interface_discovery[1]
        return ()

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
        self._last_discovery_failed = False
        if router is None:
            return ()

        if discovery_password is None:
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()
            except RuntimeError as exc:
                self._last_discovery_failed = True
                self._print_discovery_error(str(exc))
                return ()

        result = self._route_target_discovery_service.discover_wireguard_targets(
            router=router,
            password_override=discovery_password,
        )
        if result.error_message is not None:
            if missing_secret_message is not None and is_missing_password_env_error(
                result.error_message
            ):
                self._console.print(f"[yellow]{missing_secret_message}[/yellow]")
                return ()
            self._last_discovery_failed = True
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


def _flow_choice(icon: str, title: str, value: str) -> PromptChoice:
    return PromptChoice(
        title=panel_formatting._icon_label(icon, title),
        value=value,
        answer_title=title,
    )


def _route_candidates_from_interfaces(
    interfaces: tuple[RouterInterfaceState, ...],
) -> tuple[RouteTargetCandidate, ...]:
    return tuple(
        RouteTargetCandidate(
            value=interface.value,
            display_name=interface.display_name or interface.value,
            status=interface.status,
            detail=_interface_detail(interface),
            connected=interface.connected,
        )
        for interface in interfaces
    )


def _global_route_interfaces(
    interfaces: tuple[RouterInterfaceState, ...],
) -> tuple[RouterInterfaceState, ...]:
    return tuple(interface for interface in interfaces if interface.global_enabled is True)


def _interface_choice_widths(interfaces: tuple[RouterInterfaceState, ...]) -> tuple[int, ...]:
    rows = tuple(_interface_choice_columns(interface) for interface in interfaces)
    return tuple(max(len(row[index]) for row in rows) for index in range(len(rows[0])))


def _interface_choice_columns(interface: RouterInterfaceState) -> tuple[str, ...]:
    priority = interface.global_priority if interface.global_priority is not None else "-"
    return (
        interface.display_name or interface.value,
        interface.interface_type or interface.interface_class or "-",
        interface.status or "-",
        f"global={_format_optional_bool(interface.global_enabled)}",
        f"defaultgw={_format_optional_bool(interface.default_gateway)}",
        f"priority={priority}",
    )


def _interface_choice_title(
    interface: RouterInterfaceState,
    *,
    widths: tuple[int, ...],
) -> str:
    return " | ".join(
        value.ljust(width)
        for value, width in zip(_interface_choice_columns(interface), widths, strict=True)
    )


def _interface_detail(interface: RouterInterfaceState) -> str | None:
    parts = tuple(
        part
        for part in (
            f"type={interface.interface_type}" if interface.interface_type else None,
            f"class={interface.interface_class}" if interface.interface_class else None,
            (
                f"priority={interface.global_priority}"
                if interface.global_priority is not None
                else None
            ),
        )
        if part is not None
    )
    return ", ".join(parts) if parts else None


def _format_optional_bool(value: bool | None) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"


def _interface_result_from_route_targets(result: Any) -> Any:
    if getattr(result, "error_message", None) is not None:
        return result
    interfaces = tuple(
        RouterInterfaceState(
            value=candidate.value,
            display_name=candidate.display_name,
            interface_type="WireGuard",
            status=candidate.status,
            connected=candidate.connected,
            global_enabled=True,
        )
        for candidate in getattr(result, "candidates", ())
    )

    class _Result:
        def __init__(self, *, router_id: str, interfaces: tuple[RouterInterfaceState, ...]) -> None:
            self.router_id = router_id
            self.interfaces = interfaces
            self.error_message = None

    return _Result(router_id=getattr(result, "router_id", ""), interfaces=interfaces)


def _has_enabled_direct_services(config: AppConfig) -> bool:
    return any(
        service.enabled and service.key in DIRECT_SERVICE_KEYS for service in config.services
    )
