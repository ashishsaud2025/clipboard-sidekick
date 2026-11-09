"""Tests for clipboard_sidekick.redaction."""


def test_redact_api_key_triggers():
    from clipboard_sidekick.redaction import redact, should_skip_prefetch
    text = "sk-1234567890abcdef1234567890abcdef"
    out, triggered = redact(text)
    assert triggered is True
    assert "sk-" not in out
    assert should_skip_prefetch(text) is True


def test_redact_pk_live_token():
    from clipboard_sidekick.redaction import redact
    text = "use pk_live_0123456789ABcd for prod"
    out, triggered = redact(text)
    assert triggered is True
    assert "pk_live_0123456789ABcd" not in out
    assert "[REDACTED]" in out


def test_redact_32_hex_string():
    from clipboard_sidekick.redaction import redact
    text = "token deadbeefdeadbeefdeadbeefdeadbeef end"
    out, triggered = redact(text)
    assert triggered is True
    assert "deadbeefdeadbeefdeadbeefdeadbeef" not in out


def test_redact_github_token():
    from clipboard_sidekick.redaction import redact
    text = "ghp_abcdefghij1234567890 leaked"
    out, triggered = redact(text)
    assert triggered is True
    assert "ghp_abcdefghij1234567890" not in out


def test_redact_password_equals():
    from clipboard_sidekick.redaction import redact
    out, triggered = redact("password=hunter2")
    assert triggered is True
    assert out == "password=[REDACTED]"
    assert "hunter2" not in out


def test_redact_password_colon():
    from clipboard_sidekick.redaction import redact
    out, triggered = redact("password: hunter2")
    assert triggered is True
    assert out == "password: [REDACTED]"


def test_redact_password_caps_spaced():
    from clipboard_sidekick.redaction import redact
    out, triggered = redact("PASSWORD = secret123")
    assert triggered is True
    assert out == "PASSWORD = [REDACTED]"
    assert "secret123" not in out


def test_redact_quoted_password_value():
    from clipboard_sidekick.redaction import redact
    out, triggered = redact('passwd: "my secret phrase"')
    assert triggered is True
    assert "my secret phrase" not in out


def test_redact_private_key_block():
    from clipboard_sidekick.redaction import redact
    body = "MIIBlahblahRUNSECRETkeymaterial1234567890"
    text = f"-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----"
    out, triggered = redact(text)
    assert triggered is True
    assert "[REDACTED: PRIVATE KEY]" in out
    assert body not in out


def test_redact_unterminated_private_key():
    from clipboard_sidekick.redaction import redact
    text = "prefix -----BEGIN OPENSSH PRIVATE KEY-----\nsecretkeymaterial\nmore secret"
    out, triggered = redact(text)
    assert triggered is True
    assert "secretkeymaterial" not in out
    assert "more secret" not in out
    assert "prefix" in out


def test_redact_valid_luhn_card():
    from clipboard_sidekick.redaction import redact
    text = "charge card 4111 1111 1111 1111 please"
    out, triggered = redact(text)
    assert triggered is True
    assert "[REDACTED: CARD]" in out
    assert "4111" not in out


def test_invalid_luhn_card_not_redacted():
    from clipboard_sidekick.redaction import redact
    text = "digits 4111 1111 1111 1112 are not a card"
    out, triggered = redact(text)
    assert triggered is False
    assert out == text


def test_normal_sentence_untouched():
    from clipboard_sidekick.redaction import redact
    text = "The quick brown fox jumps over the lazy dog."
    out, triggered = redact(text)
    assert triggered is False
    assert out == text


def test_should_skip_prefetch_true():
    from clipboard_sidekick.redaction import should_skip_prefetch
    assert should_skip_prefetch("sk-1234567890abcdef1234567890abcdef") is True


def test_should_skip_prefetch_false():
    from clipboard_sidekick.redaction import should_skip_prefetch
    assert should_skip_prefetch("just a normal clipboard entry") is False


def test_multi_secret_text_redacts_each():
    from clipboard_sidekick.redaction import redact
    text = "sk-1234567890abcdef1234567890abcdef and password=hunter2 and 4111 1111 1111 1111"
    out, triggered = redact(text)
    assert triggered is True
    assert "sk-1234567890abcdef1234567890abcdef" not in out
    assert "hunter2" not in out
    assert "4111" not in out
    assert out.count("[REDACTED") >= 3


def test_roundtrip_redacted_contains_no_secret_substrings():
    from clipboard_sidekick.redaction import redact
    secrets = [
        "sk-1234567890abcdef1234567890abcdef",
        "pk_live_0123456789ABcd",
        "deadbeefdeadbeefdeadbeefdeadbeef",
        "ghp_abcdefghij1234567890",
        "hunter2",
        "4111 1111 1111 1111",
    ]
    text = "keys: " + " | ".join(
        secrets[:-2] + ["password=hunter2", secrets[-1]]
    )
    out, triggered = redact(text)
    assert triggered is True
    for secret in secrets:
        assert secret not in out


def test_empty_and_whitespace_input():
    from clipboard_sidekick.redaction import redact, should_skip_prefetch
    assert redact("") == ("", False)
    assert redact("   \n\t  ") == ("   \n\t  ", False)
    assert should_skip_prefetch("   ") is False
