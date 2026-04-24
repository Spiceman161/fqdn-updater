from __future__ import annotations

import fqdn_updater.cli.panel as panel_module

from .panel_test_support import ScriptedPromptAdapter, make_panel_controller, write_config


def test_run_returns_to_root_dashboard_after_successful_add_router(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["routers", "add", "exit"],
        checkbox_answers=[["telegram", "google_ai"]],
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
            "Wireguard0",
        ],
        confirm_answers=[True, False, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator,
        "generate",
        lambda self: "Aa1!bcdefghijklmnopq",
    )
    dashboard_router_counts: list[int] = []
    original_render_dashboard = controller._render_dashboard

    def record_dashboard(*, config):
        dashboard_router_counts.append(len(config.routers))
        original_render_dashboard(config=config)

    controller._render_dashboard = record_dashboard  # type: ignore[method-assign]

    controller.run()

    assert dashboard_router_counts == [0, 1]
    assert [call["message"] for call in prompts.select_calls] == [
        "Выберите раздел панели",
        "Маршрутизаторы",
        "Выберите раздел панели",
    ]
