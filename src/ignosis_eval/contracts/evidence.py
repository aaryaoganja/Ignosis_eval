"""Evidence verification against the normalized input (automatically measurable faithfulness).

An evidence item is *faithful* when everything it claims can be checked against the input the evaluator
actually received:

  transcript  every cited turn exists; the quote (if any) occurs, after normalization, in the
              concatenated text of the cited turns; the speaker (if any) matches one cited turn;
              timestamps (if any) fall within the cited turns' span (+/- tolerance).
  audio       the input carries audio; the span lies inside the audio duration (+ tolerance).
  metadata    the referenced field is present in the input.

This checks *grounding*, not relevance: a faithful quote can still be the wrong evidence for a defect
(that is measured by evidence completeness against gold).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ignosis_eval.contracts.canonical_input import CanonicalInput
from ignosis_eval.contracts.enums import EvidenceModality
from ignosis_eval.contracts.evaluation_record import EvidenceItem

TIMESTAMP_TOLERANCE_MS = 500


def normalize_text(s: str) -> str:
    """NFKC + casefold; punctuation and symbols become spaces; whitespace collapsed."""
    s = unicodedata.normalize("NFKC", s).casefold()
    chars = [" " if unicodedata.category(c)[0] in ("P", "S") else c for c in s]
    return " ".join("".join(chars).split())


@dataclass(frozen=True)
class EvidenceCheck:
    faithful: bool
    reason: str | None = None


def check_evidence(ev: EvidenceItem, inp: CanonicalInput, tolerance_ms: int = TIMESTAMP_TOLERANCE_MS) -> EvidenceCheck:
    if ev.modality is EvidenceModality.TRANSCRIPT:
        turns = inp.turn_map()
        if not turns:
            return EvidenceCheck(False, "transcript evidence but input has no transcript")
        missing = [t for t in ev.turn_ids if t not in turns]
        if missing:
            return EvidenceCheck(False, f"unknown turn ids {missing}")
        cited = [turns[t] for t in ev.turn_ids]
        if ev.quote is not None:
            q = normalize_text(ev.quote)
            if not q:
                return EvidenceCheck(False, "empty quote")
            hay = normalize_text(" ".join(t.text for t in cited))
            if q not in hay:
                return EvidenceCheck(False, "quote not found in cited turns")
        if ev.speaker is not None and all(t.speaker != ev.speaker for t in cited):
            return EvidenceCheck(False, "speaker does not match any cited turn")
        if ev.start_ms is not None or ev.end_ms is not None:
            starts = [t.start_ms for t in cited if t.start_ms is not None]
            ends = [t.end_ms for t in cited if t.end_ms is not None]
            if not starts or not ends:
                return EvidenceCheck(False, "timestamps claimed but cited turns carry none")
            lo, hi = min(starts) - tolerance_ms, max(ends) + tolerance_ms
            for v in (ev.start_ms, ev.end_ms):
                if v is not None and not (lo <= v <= hi):
                    return EvidenceCheck(False, "timestamps outside cited turns' span")
        return EvidenceCheck(True)

    if ev.modality is EvidenceModality.AUDIO:
        if inp.audio is None:
            return EvidenceCheck(False, "audio evidence but input has no audio")
        assert ev.start_ms is not None and ev.end_ms is not None  # enforced by EvidenceItem
        if ev.end_ms > inp.audio.duration_ms + tolerance_ms:
            return EvidenceCheck(False, "audio span beyond audio duration")
        if ev.turn_ids:
            turns = inp.turn_map()
            missing = [t for t in ev.turn_ids if t not in turns]
            if missing:
                return EvidenceCheck(False, f"unknown turn ids {missing}")
        return EvidenceCheck(True)

    # METADATA
    if ev.metadata_field == "call_start_ts" and inp.call_start_ts is None:
        return EvidenceCheck(False, "call_start_ts cited but not present in input")
    return EvidenceCheck(True)
