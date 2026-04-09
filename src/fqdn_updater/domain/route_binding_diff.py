from __future__ import annotations

from fqdn_updater.domain.keenetic import RouteBindingDiff, RouteBindingSpec, RouteBindingState


def build_route_binding_diff(
    *,
    actual_state: RouteBindingState,
    desired_binding: RouteBindingSpec,
) -> RouteBindingDiff:
    has_changes = (
        not actual_state.exists
        or actual_state.route_target_type != desired_binding.route_target_type
        or actual_state.route_target_value != desired_binding.route_target_value
        or actual_state.route_interface != desired_binding.route_interface
        or actual_state.auto != desired_binding.auto
        or actual_state.exclusive != desired_binding.exclusive
    )

    return RouteBindingDiff(
        object_group_name=desired_binding.object_group_name,
        current_binding=actual_state,
        desired_binding=desired_binding,
        has_changes=has_changes,
    )
