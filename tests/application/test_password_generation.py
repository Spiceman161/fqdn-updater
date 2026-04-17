from __future__ import annotations

import secrets

from fqdn_updater.application.password_generation import (
    DIGIT_PASSWORD_CHARS,
    LOWERCASE_PASSWORD_CHARS,
    PASSWORD_ALPHABET,
    SPECIAL_PASSWORD_CHARS,
    UPPERCASE_PASSWORD_CHARS,
    RciPasswordGenerator,
)


class _NoOpRandom:
    def shuffle(self, characters: list[str]) -> None:
        return None


def test_rci_password_generator_produces_20_characters_from_allowed_alphabet(
    monkeypatch,
) -> None:
    generated_characters = iter(
        [
            "a",
            "A",
            "1",
            "!",
            "b",
            "c",
            "d",
            "e",
            "f",
            "g",
            "h",
            "i",
            "j",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "q",
        ]
    )

    monkeypatch.setattr(secrets, "choice", lambda _seq: next(generated_characters))
    monkeypatch.setattr(secrets, "SystemRandom", lambda: _NoOpRandom())

    password = RciPasswordGenerator().generate()

    assert len(password) == 20
    assert any(character in LOWERCASE_PASSWORD_CHARS for character in password)
    assert any(character in UPPERCASE_PASSWORD_CHARS for character in password)
    assert any(character in DIGIT_PASSWORD_CHARS for character in password)
    assert any(character in SPECIAL_PASSWORD_CHARS for character in password)
    assert set(password).issubset(set(PASSWORD_ALPHABET))
