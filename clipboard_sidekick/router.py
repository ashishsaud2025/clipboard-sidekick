"""Rules-based content router for copied clipboard text.

classify() maps a string to one of: word, error, code, url, long, number,
other. Deterministic and rule-based, no model calls, no network, no ML.

Routing order is significant:
    1. error , tracebacks, exceptions, HTTP 4xx/5xx, file.ext:line[:col]
    2. url   , contains http(s)://
    3. code  , brackets, keywords, indentation heuristics
    4. long  , over _LONG_THRESHOLD characters
    5. number, number/unit or date-like text
    6. word  , 1-3 term-charset tokens, no code symbols
    7. other , fallback

error / url / code are checked before the length cutoff so that realistic
long inputs, e.g. multi-line tracebacks, route to their dedicated handler.

`word` implements the plain rule: 1-3 whitespace-separated tokens whose
characters are not code symbols. Multi-word plain-lowercase phrases such as
"machine learning" and "just hello there" are `word`; prose longer than 3
tokens falls through to `other`.
"""

import re

PROMPT_FOR: dict[str, str] = {
    "word": "word.txt",
    "error": "error.txt",
    "code": "code.txt",
    "url": "url.txt",
    "long": "long.txt",
    "number": "number.txt",
    "other": "fallback.txt",
}

_LONG_THRESHOLD = 400  # characters; longer copies get a summary, not a definition

_URL_RE = re.compile(r"https?://", re.IGNORECASE)

# Error patterns
_ERROR_PATTERNS = [
    re.compile(r"traceback", re.IGNORECASE),                  # Python tracebacks
    re.compile(r"\bException\b"),                             # Exception (case-sensitive, avoids "exceptional")
    re.compile(r"\b[A-Z]\w*Error\b"),                         # ValueError, TypeError, KeyError, ...
    re.compile(r"\b[A-Z]\w*Exception\b"),                     # HttpException, RuntimeException, ...
    re.compile(r"error\s*:", re.IGNORECASE),                  # "error: ..."
    re.compile(r"ERR!"),                                      # rust/cargo-style "ERR!"
    re.compile(r"HTTP[/_-]?\s*[45]\d\d\b", re.IGNORECASE),    # HTTP 4xx/5xx
    # file.ext:line or file.ext:line:col (app.py:42, main.rs:10:5)
    re.compile(
        r"\b[\w./\\-]+\.(?:py|js|jsx|ts|tsx|mjs|cjs|java|kt|scala|rb|go|rs|c|cc|cpp|h|hpp|cs|php|swift|sh|ps1|bat|sql|ya?ml|toml|json|xml|html|css):\d+(?::\d+)?",
        re.IGNORECASE,
    ),
    re.compile(r"\bline\s+\d+", re.IGNORECASE),               # "line 42", "line 42, column 10"
    re.compile(r"\b(?:at|col(?:umn)?)\s+\d{1,5}:\d{1,5}\b", re.IGNORECASE),  # "at 42:10", "col 42:10"
]


def _looks_like_error(text: str) -> bool:
    return any(p.search(text) for p in _ERROR_PATTERNS)


# Code heuristics
_CODE_INDENT_RE = re.compile(r"(?:^|\n)[ \t]{2,}\S")
_CODE_BRACKET_RE = re.compile(r"[{}\[\]()]")
_CODE_OPERATOR_RE = re.compile(r"=>|->|::|===|!==|==|!=|:=|&&|\|\|")
_CODE_KEYWORD_RE = re.compile(
    r"\b(?:def|class|function|func|fn|import|from|return|const|let|var|lambda|yield|"
    r"async|await|try|except|catch|finally|throw|new|public|private|static|void|"
    r"export|extends|implements|package|namespace|include|require|elif|struct|"
    r"enum|impl|pub|mod|interface)\b"
)
_CODE_SEMICOLON_RE = re.compile(r";[ \t]*(?:\n|$)")
_CODE_LINE_START_RE = re.compile(
    r"(?:^|\n)\s*(?:def|class|import|from|return|const|let|var|function|fn|pub|use|mod|"
    r"struct|impl|enum|package|include|require|export)\b"
)  # a code keyword leading a line (kept case-sensitive: "From:" is an email header, not code)


def _looks_like_code(text: str) -> bool:
    if _CODE_INDENT_RE.search(text):  # indented block, e.g. "def foo():\n    return 1"
        return True
    if _CODE_LINE_START_RE.search(text):
        return True
    has_keyword = bool(_CODE_KEYWORD_RE.search(text))
    has_bracket = bool(_CODE_BRACKET_RE.search(text))
    has_operator = bool(_CODE_OPERATOR_RE.search(text))
    if has_keyword and (has_bracket or has_operator):
        return True
    if has_bracket and has_operator:
        return True
    if _CODE_SEMICOLON_RE.search(text):  # trailing statement terminator
        return True
    return False


# Number / date patterns
# Plain number with optional unit/percent/degree sign: "42", "42 kg", "3.14", "1,000.5 USD"
_NUMBER_RE = re.compile(
    r"^\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:[%°a-zA-Zµ]{1,10})?\s*$"
)
_DATE_ISO_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s*$")          # 2026-11-05
_DATE_SLASH_RE = re.compile(r"^\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\s*$")  # 5/11/2026, 05-11-26
_DATE_MONTH_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b",
    re.IGNORECASE,
)                                                                # jan 5 2026, January 5, 2026


def _looks_like_number(text: str) -> bool:
    return bool(
        _NUMBER_RE.match(text)
        or _DATE_ISO_RE.match(text)
        or _DATE_SLASH_RE.match(text)
        or _DATE_MONTH_RE.search(text)
    )


# Word pattern
# Alphanumeric-leading tokens; ".", "-", "_", "+", "#", "/" are allowed inside a
# token (C++, Node.js, TCP/IP). Anything else, punctuation, emoji, "!", is a
# code symbol or non-word character and disqualifies the text as a `word`.
_WORD_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+.#/-]*$")


def _looks_like_word(text: str) -> bool:
    tokens = text.split()
    if not (1 <= len(tokens) <= 3):
        return False
    return all(_WORD_TOKEN_RE.match(t) for t in tokens)


def classify(text: str) -> str:
    """Classify copied text into word, error, code, url, long, number, or other."""
    if _looks_like_error(text):
        return "error"
    if _URL_RE.search(text):
        return "url"
    if _looks_like_code(text):
        return "code"
    if len(text) > _LONG_THRESHOLD:
        return "long"
    if _looks_like_number(text):
        return "number"
    if _looks_like_word(text):
        return "word"
    return "other"
