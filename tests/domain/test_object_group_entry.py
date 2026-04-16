from __future__ import annotations

from fqdn_updater.domain.object_group_entry import (
    ObjectGroupEntry,
    sort_object_group_entries,
)


def test_object_group_entry_canonicalizes_and_sorts_mixed_entries_deterministically() -> None:
    entries = sort_object_group_entries(
        {
            ObjectGroupEntry.classify("2001:DB8::1/64"),
            ObjectGroupEntry.classify("B.Example.com."),
            ObjectGroupEntry.classify("10.0.0.1/24"),
        }
    )

    assert [(entry.kind, entry.value) for entry in entries] == [
        ("domain", "b.example.com"),
        ("ipv4_network", "10.0.0.0/24"),
        ("ipv6_network", "2001:db8::/64"),
    ]
