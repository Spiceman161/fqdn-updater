from __future__ import annotations

from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.object_group_sharding import (
    MAX_MANAGED_FQDN_SHARDS,
    managed_shard_names,
    shard_object_group_entries,
)


def test_managed_shard_names_reserve_base_and_numbered_suffixes() -> None:
    assert MAX_MANAGED_FQDN_SHARDS == 4
    assert managed_shard_names("svc-telegram") == (
        "svc-telegram",
        "svc-telegram-2",
        "svc-telegram-3",
        "svc-telegram-4",
    )


def test_shard_object_group_entries_keeps_300_entries_in_first_group() -> None:
    entries = [f"host-{index:03d}.example" for index in range(301)]

    shards = shard_object_group_entries(base_name="svc-telegram", entries=entries)

    assert [shard.name for shard in shards] == ["svc-telegram", "svc-telegram-2"]
    assert [len(shard.entries) for shard in shards] == [300, 1]
    assert shards[0].entries[0] == ObjectGroupEntry.from_domain("host-000.example")
    assert shards[0].entries[-1] == ObjectGroupEntry.from_domain("host-299.example")
    assert shards[1].entries == (ObjectGroupEntry.from_domain("host-300.example"),)


def test_shard_object_group_entries_returns_no_shards_for_empty_entries() -> None:
    assert shard_object_group_entries(base_name="svc-telegram", entries=()) == ()
