from __future__ import annotations

import secrets
import string

LOWERCASE_PASSWORD_CHARS = string.ascii_lowercase
UPPERCASE_PASSWORD_CHARS = string.ascii_uppercase
DIGIT_PASSWORD_CHARS = string.digits
SPECIAL_PASSWORD_CHARS = r"""!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~"""
PASSWORD_ALPHABET = (
    LOWERCASE_PASSWORD_CHARS
    + UPPERCASE_PASSWORD_CHARS
    + DIGIT_PASSWORD_CHARS
    + SPECIAL_PASSWORD_CHARS
)
RCI_PASSWORD_LENGTH = 20


class RciPasswordGenerator:
    """Generate panel-managed RCI passwords from the production policy."""

    def generate(self) -> str:
        required_characters = [
            secrets.choice(LOWERCASE_PASSWORD_CHARS),
            secrets.choice(UPPERCASE_PASSWORD_CHARS),
            secrets.choice(DIGIT_PASSWORD_CHARS),
            secrets.choice(SPECIAL_PASSWORD_CHARS),
        ]
        remaining_length = RCI_PASSWORD_LENGTH - len(required_characters)
        characters = required_characters + [
            secrets.choice(PASSWORD_ALPHABET) for _ in range(remaining_length)
        ]
        secrets.SystemRandom().shuffle(characters)
        return "".join(characters)
