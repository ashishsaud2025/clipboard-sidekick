import pytest
from clipboard_sidekick.router import classify

CASES = [
    # Core routing cases
    ("python", "word"),
    ("HTTP 404 Not Found", "error"),
    ("Traceback (most recent call last):", "error"),
    ("def foo():\n    return 1", "code"),
    ("https://example.com/page", "url"),
    ("A" * 500, "long"),
    ("42 kg", "number"),
    ("jan 5 2026", "number"),
    ("just hello there", "word"),
    ("machine learning", "word"),
    ("large language model", "word"),
    # Additional coverage: ERR! token, line:col pattern, HTTP 500
    ("cargo build failed: ERR! unresolved import", "error"),
    ("app.py:42:10: invalid syntax", "error"),
    ("HTTP 500 Internal Server Error", "error"),
    # Additional coverage: URL, ISO date, fallback edge cases
    ("https://github.com/psf/requests", "url"),
    ("2026-11-05", "number"),
    ("🎉🔥💯", "other"),
    ("completely unrelated prose sentence with many words", "other"),
    # Extra headroom for the >=15 requirement
    ("ValueError: invalid literal for int() with base 10", "error"),
    ("import os\nimport sys", "code"),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_classify(text, expected):
    assert classify(text) == expected
