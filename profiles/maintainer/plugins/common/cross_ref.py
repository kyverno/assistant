"""PR cross-reference extractor for dependency graph resolution.

Identifies explicit dependency declarations (e.g. 'depends on #72', 'blocked by #73',
'stacked on #74', 'parent #75', 'Depends-On: #76') declaring hard merge-order prerequisites,
while rejecting negations ('no longer depends on #72', 'Depends on #72, but not on #73'),
prose mentions ('fixes the parent #72 selector bug', 'replaces the old handler from #72'),
and cross-repo references to foreign repositories.
"""

from __future__ import annotations

import re
from typing import List, Set

# Matches reference tokens:
# 1. Full URLs: https://github.com/owner/repo/(pull|issues)/123
# 2. Repo-qualified: owner/repo#123
# 3. Bare numbers: #123
_REF_PATTERN = re.compile(
    r"(?:https?://github\.com/([\w.-]+/[\w.-]+)/(?:pull|issues)/(\d+))"
    r"|(?:([\w.-]+/[\w.-]+)#(\d+))"
    r"|(?:#(\d+))",
    re.IGNORECASE,
)

# Strips inline markdown formatting decorators.
_MARKDOWN_STRIP = re.compile(r"[*_`~]")

# Anchored keywords for hard dependency declarations ("must merge first").
# Anchored at the start of a line or clause, with optional bullet/quote markers and
# optional subject pronouns/nouns.
_KEYWORD_START_PATTERN = re.compile(
    r"^[\s\-*>\d.)\[\]:]*"
    r"(?:(?:this\s+(?:pr|change|commit)|it|we)\s+(?:also\s+|currently\s+)?)?"
    r"(?:"
    r"depends?-on|"
    r"depends?\s+on|"
    r"blocked\s+by|"
    r"requires?|"
    r"requiring|"
    r"stacks?\s+on(?:\s+top\s+of)?|"
    r"stacked\s+on(?:\s+top\s+of)?|"
    r"parent(?:\s+(?:pr|issue))?"
    r")\s*:?\s*",
    re.IGNORECASE,
)

# Negations appearing before the keyword in the same clause.
_NEGATION_BEFORE_PATTERN = re.compile(
    r"\b("
    r"not|"
    r"no\s+longer|"
    r"never|"
    r"unrelated\s+to|"
    r"without|"
    r"isn'?t|"
    r"aren'?t|"
    r"doesn'?t|"
    r"don'?t|"
    r"didn'?t|"
    r"won'?t"
    r")\b",
    re.IGNORECASE,
)

# Connectors between reference tokens in a list (commas, slashes, ampersands, and/or, whitespace).
_CONNECTOR_PATTERN = re.compile(r"^(?:[\s,\/&\-*]|\b(?:and|or)\b)+", re.IGNORECASE)


def _split_into_clauses(text: str) -> List[str]:
    """Splits body text into bounded clauses and sentences."""
    # 1. Strip HTML comments.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # 2. Protect markdown links [text](url) so URL parentheses are not split.
    link_placeholders: List[str] = []

    def _link_repl(match: re.Match) -> str:
        idx = len(link_placeholders)
        link_placeholders.append(match.group(0))
        return f"__MD_LINK_{idx}__"

    text = re.sub(r"\[[^\]]*\]\([^)]+\)", _link_repl, text)

    # 3. Collapse multiline keyword declarations (e.g. "Blocked by:\n#72" or "Depends on:\n- #72\n- #73").
    lines = text.splitlines()
    joined_lines: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        m = _KEYWORD_START_PATTERN.match(_MARKDOWN_STRIP.sub("", stripped))
        # If the line starts with a keyword and has no reference on the same line, consume subsequent reference lines.
        if m and m.end() >= len(_MARKDOWN_STRIP.sub("", stripped)):
            accum = [line]
            i += 1
            while i < len(lines) and lines[i].strip():
                next_line = lines[i].strip()
                if next_line.startswith("# ") or next_line.startswith("## "):
                    break
                if re.search(r"#\d+|https?://", next_line):
                    accum.append(lines[i])
                    i += 1
                else:
                    break
            joined_lines.append(" ".join(accum))
        else:
            joined_lines.append(line)
            i += 1

    clauses: List[str] = []

    # 4. Extract parenthetical expressions as separate clauses and delimit them in the outer text.
    def _paren_extractor(match: re.Match) -> str:
        inner = match.group(1).strip()
        if inner:
            clauses.append(inner)
        return " ; "

    processed_text = "\n".join(joined_lines)
    processed_text = re.sub(r"\(([^)]+)\)", _paren_extractor, processed_text)

    # 5. Split on line breaks, semicolons, sentence endings, and contrastive conjunctions (e.g., ", but / however").
    raw_clauses = re.split(
        r"(?:\r?\n)+"
        r"|;\s*"
        r"|(?<=[.!?])\s+"
        r"|,\s*(?:but|however)\s+"
        r"|\b(?:but|however)\s+",
        processed_text,
        flags=re.IGNORECASE,
    )

    for rc in raw_clauses:
        rc = rc.strip()
        if rc:
            clauses.append(rc)

    # 6. Restore markdown links.
    restored: List[str] = []
    for c in clauses:
        for idx, original in enumerate(link_placeholders):
            c = c.replace(f"__MD_LINK_{idx}__", original)
        restored.append(c)

    return restored


def _extract_references_from_text(text: str, target_repo: str) -> Set[int]:
    """Takes only the direct reference tokens in text, stopping at non-reference prose."""
    refs: Set[int] = set()
    pos = 0
    target_repo = target_repo.lower()

    while pos < len(text):
        m_conn = _CONNECTOR_PATTERN.match(text[pos:])
        if m_conn:
            pos += m_conn.end()
            if pos >= len(text):
                break

        # Stop if an intra-list negation begins (e.g. ", but not on #73" or "not #73").
        if re.match(r"^not\b", text[pos:], re.IGNORECASE):
            break

        m_ref = _REF_PATTERN.match(text[pos:])
        if m_ref:
            url_repo, url_num, qual_repo, qual_num, bare_num = m_ref.groups()
            if url_num:
                if url_repo.lower() == target_repo:
                    refs.add(int(url_num))
            elif qual_num:
                if qual_repo.lower() == target_repo:
                    refs.add(int(qual_num))
            elif bare_num:
                refs.add(int(bare_num))
            pos += m_ref.end()
        else:
            # Reached prose/non-reference text (e.g. "for duplicates").
            break

    return refs


def _extract_from_clause(clause: str, target_repo: str) -> Set[int]:
    """Evaluates whether a clause declares a dependency and extracts its target references."""
    cleaned = _MARKDOWN_STRIP.sub("", clause).strip()
    if not cleaned:
        return set()

    m_start = _KEYWORD_START_PATTERN.match(cleaned)
    if not m_start:
        return set()

    before_kw = cleaned[:m_start.start()]
    if _NEGATION_BEFORE_PATTERN.search(before_kw):
        return set()

    after_kw = cleaned[m_start.end():]
    return _extract_references_from_text(after_kw, target_repo)


def extract_cross_references(text: str, target_repo: str = "kyverno/kyverno") -> Set[int]:
    """Extracts all PR/issue numbers that this PR depends on or is blocked by.

    Args:
        text: Raw PR description or body text.
        target_repo: The target repository (default: 'kyverno/kyverno') used to
            qualify bare '#123' references and filter full URLs or qualified references.

    Returns:
        A set of integer PR/issue numbers in target_repo declared as dependencies.
    """
    if not text:
        return set()

    clauses = _split_into_clauses(text)
    results: Set[int] = set()
    for clause in clauses:
        results.update(_extract_from_clause(clause, target_repo))
    return results
