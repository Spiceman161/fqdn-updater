from __future__ import annotations

KEENETIC_MAX_FQDN_OBJECT_GROUP_ENTRIES = 300
KEENETIC_MAX_TOTAL_FQDN_ENTRIES = 1024


def validate_fqdn_object_group_entry_count(
    *,
    object_group_name: str,
    entry_count: int,
) -> None:
    if entry_count > KEENETIC_MAX_FQDN_OBJECT_GROUP_ENTRIES:
        raise ValueError(
            f"object-group '{object_group_name}' has {entry_count} desired entries, "
            "exceeding Keenetic FQDN object-group limit "
            f"{KEENETIC_MAX_FQDN_OBJECT_GROUP_ENTRIES}"
        )


def validate_total_fqdn_entry_count(
    *,
    router_id: str,
    entry_count: int,
) -> None:
    if entry_count > KEENETIC_MAX_TOTAL_FQDN_ENTRIES:
        raise ValueError(
            f"router '{router_id}' has {entry_count} desired managed FQDN entries, "
            "exceeding Keenetic total FQDN section limit "
            f"{KEENETIC_MAX_TOTAL_FQDN_ENTRIES}"
        )
