import pytest
from clipboard_sidekick.router import classify

LONG_TRACEBACK = (
    "Traceback (most recent call last):\n"
    + '  File "app.py", line 42, in <module>\n    result = compute(items)\n' * 12
    + "ZeroDivisionError: division by zero\n"
)
LONG_URL = "https://example.com/" + "a" * 500
LONG_CODE = (
    "def compute_totals(records):\n"
    "    total = 0\n"
    "    count = 0\n"
    "    for record in records:\n"
    "        price = record.get('price', 0) * record.get('quantity', 0)\n"
    "        total += price\n"
    "        count += 1\n"
    "        if total > 1000:\n"
    "            total = total * 0.9\n"
    "    return total, count\n"
    "\n"
    "def format_report(total, count):\n"
    "    lines = []\n"
    "    lines.append('items: ' + str(count))\n"
    "    lines.append('sum: ' + str(total))\n"
    "    return lines\n"
)
LONG_PROSE = (
    "the quick brown fox jumps over the lazy dog while the autumn wind moves "
    "through the high grass and the sun drops behind the ridge leaving the valley "
    "in soft golden shadow as the first stars appear above the far hills and the "
    "nightjar begins its quiet call from somewhere deep in the bracken near the "
    "brook where the water runs clear and cold over smooth grey stones and the "
    "old willow leans out and trails its fingers in the current drifting a few "
    "yellow leaves along with the current until they catch on the roots of the "
    "willow and turn slowly round and round before slipping free and continuing "
    "downstream past the meadow where the house stands with its lamps already "
    "lit against the coming dark and the whole scene holds itself very still as "
    "if the evening were holding its breath before the owl calls from the barn "
    "loft and the fox pads silently across the frost covered field toward the "
    "woods on the far side where the deer are sleeping in a sheltered hollow "
    "and the whole valley settles into the deep blue hush of early autumn "
    "before the first cold snap arrives and brings the year toward winter with "
    "its long nights and short bright days and the quiet work of preparing for "
    "the colder months ahead with wood stacked high beside the door and preserves "
    "lined in the cool cellar and warm blankets ready on the beds upstairs"
)

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
    # Regression: >400-char inputs with error/url/code signals must not hit `long`
    (LONG_TRACEBACK, "error"),
    (LONG_URL, "url"),
    (LONG_CODE, "code"),
    (LONG_PROSE, "long"),
]


def test_long_fixtures_exceed_threshold():
    for fixture in (LONG_TRACEBACK, LONG_URL, LONG_CODE, LONG_PROSE):
        assert len(fixture) > 400


@pytest.mark.parametrize("text,expected", CASES)
def test_classify(text, expected):
    assert classify(text) == expected
