"""
Pydantic schemas for the eval harness.

These are the data-transfer types used by run.py, scorer.py, and any tooling
that processes golden-set data.  They are intentionally kept independent of the
backend app models so the eval harness can be run without the full app stack.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class GoldenItem(BaseModel):
    """A single hand-labelled action item expected from a transcript."""

    task: str = Field(..., description="Canonical imperative task description.")
    owner: str = Field(
        ...,
        description="Named person or 'Unassigned'.",
    )
    priority: str = Field(
        ...,
        description="High | Medium | Low",
    )
    due_date: Optional[date] = Field(
        None,
        description=(
            "Resolved calendar date when one can be inferred; None when only "
            "relative language is present without a resolvable anchor."
        ),
    )
    context_keywords: List[str] = Field(
        default_factory=list,
        description=(
            "Short phrases that must appear (loosely) in the extracted context "
            "field to confirm the model cited the right evidence."
        ),
    )


class GoldenTranscript(BaseModel):
    """
    A single entry in the golden set.

    The transcript text is stored in a companion .txt file and loaded at
    runtime; it is NOT embedded in the JSON to keep diffs readable.
    """

    id: str = Field(..., description="Unique slug, e.g. 'clean_standup_01'.")
    description: str = Field(..., description="Human-readable summary of what this case tests.")
    transcript_text: str = Field(
        default="",
        description=(
            "Populated at runtime by loading the companion .txt file.  "
            "Leave empty in the .json on disk."
        ),
    )
    expected_items: List[GoldenItem] = Field(
        default_factory=list,
        description="Ordered list of action items the extractor must find.",
    )
    expected_empty: bool = Field(
        False,
        description="True when the transcript should produce zero action items.",
    )


class ExtractionResult(BaseModel):
    """
    A single action item as returned by the extraction service / Claude API.

    Field names mirror extraction_v1.txt so the parser does not need a
    translation layer.
    """

    task: str
    owner: str
    priority: str
    due_date: Optional[str] = Field(
        None,
        alias="due",
        description="Raw timeframe text as returned by the model.",
    )
    context: str
    confidence: float = Field(..., ge=0.0, le=1.0)

    class Config:
        populate_by_name = True


class EvalMetrics(BaseModel):
    """Per-transcript evaluation metrics."""

    transcript_id: str
    transcript_description: str

    # Core IR metrics
    precision: float = Field(..., ge=0.0, le=1.0)
    recall: float = Field(..., ge=0.0, le=1.0)
    f1: float = Field(..., ge=0.0, le=1.0)

    # Field-level accuracy (computed only over matched pairs)
    owner_accuracy: float = Field(..., ge=0.0, le=1.0)
    due_date_accuracy: float = Field(..., ge=0.0, le=1.0)

    # Special-case correctness
    empty_case_correct: bool = Field(
        True,
        description=(
            "True when expected_empty=True and the extractor returned nothing, "
            "or when expected_empty=False and the extractor returned something. "
            "Always True for non-empty cases (the signal is in recall/precision)."
        ),
    )

    # Counts useful for debugging
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0


class SuiteReport(BaseModel):
    """Aggregated results for a full eval suite run."""

    metrics: List[EvalMetrics] = Field(default_factory=list)

    # Macro averages
    avg_precision: float = Field(0.0, ge=0.0, le=1.0)
    avg_recall: float = Field(0.0, ge=0.0, le=1.0)
    avg_f1: float = Field(0.0, ge=0.0, le=1.0)

    # Gate
    threshold: float = Field(0.85, description="Minimum avg_f1 required to pass.")
    passed: bool = Field(False, description="True when avg_f1 >= threshold.")
