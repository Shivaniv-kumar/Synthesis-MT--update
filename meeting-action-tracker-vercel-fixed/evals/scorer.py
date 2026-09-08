"""
Matching and scoring logic for the eval harness.

Intentionally free of I/O and Anthropic API calls so it can be unit-tested
independently.
"""

from __future__ import annotations

import difflib
from datetime import date
from typing import List, Optional, Tuple

from evals.schemas import EvalMetrics, ExtractionResult, GoldenItem, GoldenTranscript


# ---------------------------------------------------------------------------
# Low-level similarity helpers
# ---------------------------------------------------------------------------


def _seq_ratio(a: str, b: str) -> float:
    """Case-insensitive SequenceMatcher similarity ratio."""
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _owner_matches(extracted_owner: str, golden_owner: str) -> bool:
    """
    Return True when the extracted owner is a plausible match for the golden
    owner.  Uses a fuzzy ratio threshold of 0.8, or exact match after
    lower-casing.  'Unassigned' only matches 'Unassigned'.
    """
    if extracted_owner.lower() == "unassigned" and golden_owner.lower() == "unassigned":
        return True
    if extracted_owner.lower() == "unassigned" or golden_owner.lower() == "unassigned":
        return False
    return _seq_ratio(extracted_owner, golden_owner) > 0.8


def _priority_matches(extracted_priority: str, golden_priority: str) -> bool:
    return extracted_priority.strip().lower() == golden_priority.strip().lower()


def _parse_due_date(raw: Optional[str]) -> Optional[date]:
    """
    Attempt to parse a due date string (YYYY-MM-DD) from the extracted result.
    Returns None on failure or when the input is empty/None.
    """
    if not raw:
        return None
    raw = raw.strip()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Core matching
# ---------------------------------------------------------------------------


def _item_matches(extracted: ExtractionResult, golden: GoldenItem) -> bool:
    """
    Return True when ``extracted`` is considered a match for ``golden``.

    A match is defined as EITHER:
      - Task text similarity > 0.7  (same work being described)
    OR
      - Owner AND priority both match exactly  (same assignee + urgency signal
        even if phrased differently)
    """
    task_sim = _seq_ratio(extracted.task, golden.task)
    if task_sim > 0.7:
        return True
    owner_ok = _owner_matches(extracted.owner, golden.owner)
    priority_ok = _priority_matches(extracted.priority, golden.priority)
    return owner_ok and priority_ok


def match_items(
    extracted: List[ExtractionResult],
    golden: List[GoldenItem],
) -> Tuple[List[Tuple[ExtractionResult, GoldenItem]], List[ExtractionResult], List[GoldenItem]]:
    """
    Greedily match extracted items to golden items.

    Returns
    -------
    true_positives : list of (extracted, golden) pairs
        Each extracted item matched to exactly one golden item.
    false_positives : list[ExtractionResult]
        Extracted items that could not be matched to any golden item.
    false_negatives : list[GoldenItem]
        Golden items that were not matched by any extracted item.
    """
    unmatched_golden = list(golden)
    true_positives: List[Tuple[ExtractionResult, GoldenItem]] = []
    false_positives: List[ExtractionResult] = []

    for ext in extracted:
        best_golden: Optional[GoldenItem] = None
        best_score = -1.0

        for g in unmatched_golden:
            # Use task similarity as the primary ranking signal for the greedy
            # assignment even though the match condition also has the OR branch.
            score = _seq_ratio(ext.task, g.task)
            if _item_matches(ext, g) and score > best_score:
                best_score = score
                best_golden = g

        if best_golden is not None:
            true_positives.append((ext, best_golden))
            unmatched_golden.remove(best_golden)
        else:
            false_positives.append(ext)

    false_negatives = unmatched_golden
    return true_positives, false_positives, false_negatives


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


def compute_metrics(
    transcript: GoldenTranscript,
    extracted: List[ExtractionResult],
) -> EvalMetrics:
    """
    Score a single transcript and return an EvalMetrics instance.

    Handles the empty-case (expected_empty=True) separately so that a
    correctly empty extraction does not inflate false-negative counts.
    """
    golden_items = transcript.expected_items
    expected_empty = transcript.expected_empty

    # --- empty-case handling ---
    if expected_empty:
        empty_case_correct = len(extracted) == 0
        # If the model correctly returned nothing, perfect score; otherwise
        # every extracted item is a false positive with zero recall denominator.
        tp_count = 0
        fp_count = len(extracted)
        fn_count = 0
        precision = 1.0 if len(extracted) == 0 else 0.0
        recall = 1.0  # nothing to recall; we define recall as 1.0 when expected_empty
        owner_acc = 1.0
        due_acc = 1.0
    else:
        empty_case_correct = True  # not an empty-case transcript
        true_positives, false_positives, false_negatives = match_items(extracted, golden_items)
        tp_count = len(true_positives)
        fp_count = len(false_positives)
        fn_count = len(false_negatives)

        precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 1.0
        recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 1.0

        # Owner accuracy over matched pairs
        owner_correct = sum(
            1 for ext, gld in true_positives if _owner_matches(ext.owner, gld.owner)
        )
        owner_acc = owner_correct / tp_count if tp_count > 0 else 1.0

        # Due-date accuracy: only evaluated when golden has a resolved date
        dated_pairs = [
            (ext, gld) for ext, gld in true_positives if gld.due_date is not None
        ]
        if dated_pairs:
            date_correct = 0
            for ext, gld in dated_pairs:
                extracted_date = _parse_due_date(ext.due_date)
                if extracted_date == gld.due_date:
                    date_correct += 1
            due_acc = date_correct / len(dated_pairs)
        else:
            due_acc = 1.0  # no dated items to evaluate

    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return EvalMetrics(
        transcript_id=transcript.id,
        transcript_description=transcript.description,
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        owner_accuracy=round(owner_acc, 4),
        due_date_accuracy=round(due_acc, 4),
        empty_case_correct=empty_case_correct,
        true_positives=tp_count,
        false_positives=fp_count,
        false_negatives=fn_count,
    )
