from __future__ import annotations

from fqdn_updater.domain.keenetic import ObjectGroupState
from fqdn_updater.domain.object_group_diff import build_object_group_diff


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
