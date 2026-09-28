"""Lexicon matching engine — reads profile.yaml › lexicons.*.terms ONLY (never unreviewed seeds, B-04).

Matching is on SD-13-normalized tokens. A hit is negated when a negation term occurs within
±profile.thresholds.negation_window_tokens tokens of the hit in the same turn (frozen-contract §6.2).
While lexicon review is pending, every `terms` list is empty and nothing matches.
"""

from __future__ import annotations

from dataclasses import dataclass

from ignosis_eval.contracts.evidence import norm


def tokens(text: str) -> list[str]:
    return norm(text).split()


def _occurrences(toks: list[str], term: str) -> list[tuple[int, int]]:
    tt = tokens(term)
    if not tt:
        return []
    n = len(tt)
    return [(i, i + n) for i in range(len(toks) - n + 1) if toks[i:i + n] == tt]


@dataclass(frozen=True)
class LexiconHit:
    category: str
    term: str
    start: int  # token span [start, end)
    end: int
    negated: bool

    @property
    def normalized_quote(self) -> str:
        return " ".join(tokens(self.term))


def is_negated(toks: list[str], start: int, end: int, negation_terms: list[str], window: int) -> bool:
    lo, hi = start - window, end - 1 + window
    for neg in negation_terms:
        for ns, ne in _occurrences(toks, neg):
            if ns <= hi and ne - 1 >= lo and not (ns >= start and ne <= end):
                return True
    return False


def scan(text: str, categories: dict[str, list[str]], negation_terms: list[str], window: int) -> list[LexiconHit]:
    toks = tokens(text)
    hits: list[LexiconHit] = []
    for cat, terms in categories.items():
        for term in terms:
            for s, e in _occurrences(toks, term):
                hits.append(LexiconHit(cat, term, s, e, is_negated(toks, s, e, negation_terms, window)))
    return sorted(hits, key=lambda h: (h.start, h.category, h.term))
