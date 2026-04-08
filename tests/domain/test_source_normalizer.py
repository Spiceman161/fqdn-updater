from __future__ import annotations

import pytest

from fqdn_updater.domain.source_normalizer import normalize_entries


def test_normalize_entries_for_domains_trims_lowercases_and_sorts() -> None:
    normalized_entries = normalize_entries(
        raw_text="\n# comment\n  B.Example.com \na.example.com.\nB.Example.com\n",
        source_format="raw_domain_list",
    )

    assert normalized_entries == ("a.example.com", "b.example.com")


def test_normalize_entries_for_cidr_canonicalizes_networks() -> None:
    normalized_entries = normalize_entries(
        raw_text="10.0.0.1/24\n10.0.0.0/24\n2001:db8::1/64\n",
        source_format="raw_cidr_list",
    )

    assert normalized_entries == ("10.0.0.0/24", "2001:db8::/64")


def test_normalize_entries_for_mixed_accepts_domains_and_cidrs() -> None:
    normalized_entries = normalize_entries(
        raw_text="Example.com\n10.0.0.1/24\n",
        source_format="mixed",
    )

    assert normalized_entries == ("10.0.0.0/24", "example.com")


@pytest.mark.parametrize(
    ("source_format", "raw_text", "message"),
    [
        ("raw_domain_list", "bad domain", "invalid domain entry"),
        ("raw_domain_list", "10.0.0.0/24", "invalid domain entry"),
        ("raw_cidr_list", "example.com", "invalid CIDR entry"),
        ("mixed", "bad domain", "invalid domain entry"),
    ],
)
def test_normalize_entries_rejects_malformed_input(
    source_format: str, raw_text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_entries(raw_text=raw_text, source_format=source_format)  # type: ignore[arg-type]
