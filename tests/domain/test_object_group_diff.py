from __future__ import annotations

from fqdn_updater.domain.keenetic import ObjectGroupState
from fqdn_updater.domain.object_group_diff import build_object_group_diff
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry


def test_build_object_group_diff_reports_no_changes_for_identical_entries() -> None:
    actual_state = ObjectGroupState(
        name="svc-telegram",
        entries=["b.example", "a.example"],
        exists=True,
    )

    diff = build_object_group_diff(
        desired_entries=["a.example", "b.example"],
        actual_state=actual_state,
    )

    assert diff.object_group_name == "svc-telegram"
    assert diff.needs_create is False
    assert diff.to_add == ()
    assert diff.to_remove == ()
    assert diff.unchanged == ("a.example", "b.example")
    assert diff.has_changes is False


def test_build_object_group_diff_reports_additions_and_removals_deterministically() -> None:
    actual_state = ObjectGroupState(
        name="svc-telegram",
        entries=["keep.example", "remove.example"],
        exists=True,
    )

    diff = build_object_group_diff(
        desired_entries=["add.example", "keep.example", "add.example"],
        actual_state=actual_state,
    )

    assert diff.needs_create is False
    assert diff.to_add == ("add.example",)
    assert diff.to_remove == ("remove.example",)
    assert diff.unchanged == ("keep.example",)
    assert diff.has_changes is True


def test_build_object_group_diff_marks_absent_group_for_creation_when_desired_entries_exist() -> (
    None
):
    actual_state = ObjectGroupState(name="svc-telegram", entries=(), exists=False)

    diff = build_object_group_diff(
        desired_entries=["b.example", "a.example"],
        actual_state=actual_state,
    )

    assert diff.needs_create is True
    assert diff.to_add == ("a.example", "b.example")
    assert diff.to_remove == ()
    assert diff.unchanged == ()
    assert diff.has_changes is True


def test_build_object_group_diff_keeps_absent_empty_group_as_noop() -> None:
    actual_state = ObjectGroupState(name="svc-telegram", entries=(), exists=False)

    diff = build_object_group_diff(
        desired_entries=[],
        actual_state=actual_state,
    )

    assert diff.needs_create is False
    assert diff.to_add == ()
    assert diff.to_remove == ()
    assert diff.unchanged == ()
    assert diff.has_changes is False


def test_build_object_group_diff_handles_mixed_typed_entries_and_serializes_them() -> None:
    actual_state = ObjectGroupState(
        name="svc-telegram",
        typed_entries=(
            ObjectGroupEntry.from_domain("keep.example"),
            ObjectGroupEntry.from_network("10.0.1.1/24"),
            ObjectGroupEntry.from_network("2001:db8:1::1/64"),
        ),
        exists=True,
    )

    diff = build_object_group_diff(
        desired_entries=(
            ObjectGroupEntry.from_domain("keep.example"),
            ObjectGroupEntry.from_network("10.0.0.1/24"),
            ObjectGroupEntry.from_network("2001:db8::1/64"),
        ),
        actual_state=actual_state,
    )

    assert diff.to_add == ("10.0.0.0/24", "2001:db8::/64")
    assert diff.to_remove == ("10.0.1.0/24", "2001:db8:1::/64")
    assert diff.unchanged == ("keep.example",)
    assert [(entry.kind, entry.value) for entry in diff.typed_to_add] == [
        ("ipv4_network", "10.0.0.0/24"),
        ("ipv6_network", "2001:db8::/64"),
    ]
    assert [(entry.kind, entry.value) for entry in diff.typed_to_remove] == [
        ("ipv4_network", "10.0.1.0/24"),
        ("ipv6_network", "2001:db8:1::/64"),
    ]
    assert diff.model_dump(mode="json") == {
        "has_changes": True,
        "needs_create": False,
        "object_group_name": "svc-telegram",
        "to_add": ["10.0.0.0/24", "2001:db8::/64"],
        "to_remove": ["10.0.1.0/24", "2001:db8:1::/64"],
        "typed_to_add": [
            {"kind": "ipv4_network", "value": "10.0.0.0/24"},
            {"kind": "ipv6_network", "value": "2001:db8::/64"},
        ],
        "typed_to_remove": [
            {"kind": "ipv4_network", "value": "10.0.1.0/24"},
            {"kind": "ipv6_network", "value": "2001:db8:1::/64"},
        ],
        "typed_unchanged": [
            {"kind": "domain", "value": "keep.example"},
        ],
        "unchanged": ["keep.example"],
    }
