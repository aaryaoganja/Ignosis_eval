"""Quote faithfulness — scoring-spec.md SD-13 (normative; implemented exactly as specified).

norm(s): NFKC -> casefold -> every char whose Unicode category starts with P or S becomes a space ->
collapse whitespace -> strip.
score(q, t): Q = norm(q), T = norm(t); |Q| = 0 -> 0; |Q| <= |T| -> max over i in [0, |T|-|Q|] of
100 * (1 - lev(Q, T[i:i+|Q|]) / |Q|); else max(0, 100 * (1 - lev(Q, T) / |Q|)). lev = Levenshtein over
code points with unit costs.
"""

from __future__ import annotations

import unicodedata


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    s = "".join(" " if unicodedata.category(c)[0] in ("P", "S") else c for c in s)
    return " ".join(s.split()).strip()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def quote_score(quote: str, text: str) -> float:
    q, t = norm(quote), norm(text)
    if not q:
        return 0.0
    if len(q) <= len(t):
        if q in t:
            return 100.0
        best = 0.0
        for i in range(len(t) - len(q) + 1):
            best = max(best, 100.0 * (1 - levenshtein(q, t[i:i + len(q)]) / len(q)))
            if best == 100.0:
                break
        return best
    return max(0.0, 100.0 * (1 - levenshtein(q, t) / len(q)))
