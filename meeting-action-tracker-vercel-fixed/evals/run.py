#!/usr/bin/env python3
"""
Eval harness entry point.

Usage
-----
    python -m evals.run --suite golden --threshold 0.85 --verbose
    python -m evals.run --suite golden --model claude-haiku-4-5

Environment variables
---------------------
    ANTHROPIC_API_KEY   Required.  Anthropic secret key.
    EXTRACTION_MODEL    Default model when --model is omitted.

Exit codes
----------
    0  Suite passed (avg F1 >= threshold).
    1  Suite failed or error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Path setup: allow running from the repo root without installing the package.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------------------------------------------------------------------------
# Imports (deferred so path is set first)
# ---------------------------------------------------------------------------
from evals.schemas import (  # noqa: E402
    EvalMetrics,
    ExtractionResult,
    GoldenItem,
    GoldenTranscript,
    SuiteReport,
)
from evals.scorer import compute_metrics  # noqa: E402

# ---------------------------------------------------------------------------
# Optional: use the backend extraction service when available
# ---------------------------------------------------------------------------
try:
    from app.services.extraction import extract_action_items as _backend_extract  # type: ignore

    _HAS_BACKEND = True
except ImportError:
    _HAS_BACKEND = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GOLDEN_SET_DIR = Path(__file__).resolve().parent / "golden_set"
DEFAULT_MODEL = "claude-sonnet-4-6"
PROMPT_PATH = _REPO_ROOT / "backend" / "prompts" / "extraction_v1.txt"


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _load_prompt_template() -> str:
    """Load extraction_v1.txt and return its contents."""
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Prompt not found at {PROMPT_PATH}. "
            "Run from the repo root or set PROMPT_PATH."
        )
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_prompt(transcript_text: str, template: str) -> str:
    return template.replace("{{TRANSCRIPT_TEXT}}", transcript_text)


def _call_anthropic(prompt: str, model: str) -> str:
    """
    Call the Anthropic Messages API and return the raw text content.

    Raises
    ------
    RuntimeError  when the API key is missing or the call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set.  "
            "Export it before running the eval suite."
        )

    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is not installed.  "
            "Run: pip install anthropic"
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _parse_extraction_response(raw: str) -> List[ExtractionResult]:
    """
    Parse the JSON array returned by the model.

    Strips code fences if present, validates each element against
    ExtractionResult, and silently skips malformed elements (same behaviour
    as the backend extraction service).
    """
    text = raw.strip()
    # Strip optional markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop first and last fence line
        inner = lines[1:] if lines[0].startswith("```") else lines
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model returned invalid JSON: {exc}\n\nRaw output:\n{raw[:500]}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array, got: {type(data).__name__}")

    results: List[ExtractionResult] = []
    for i, item in enumerate(data):
        try:
            results.append(ExtractionResult.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] Skipping malformed item {i}: {exc}", file=sys.stderr)
    return results


def extract_transcript(transcript_text: str, model: str, prompt_template: str) -> List[ExtractionResult]:
    """
    Extract action items from a transcript.

    Uses the backend service when available; otherwise calls the Anthropic API
    directly with the extraction_v1 prompt.
    """
    if _HAS_BACKEND:
        raw_items = _backend_extract(transcript_text, model=model)
        # backend returns dicts; coerce to ExtractionResult
        return [ExtractionResult.model_validate(item) for item in raw_items]

    prompt = _build_prompt(transcript_text, prompt_template)
    raw = _call_anthropic(prompt, model)
    return _parse_extraction_response(raw)


# ---------------------------------------------------------------------------
# Golden-set loading
# ---------------------------------------------------------------------------


def _load_golden_transcript(json_path: Path) -> Optional[GoldenTranscript]:
    """
    Load a GoldenTranscript from its .json file and companion .txt file.

    Returns None and prints a warning when either file is missing or malformed.
    """
    txt_path = json_path.with_suffix(".txt")
    if not txt_path.exists():
        print(f"[warn] No companion .txt for {json_path.name} — skipping.", file=sys.stderr)
        return None

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[warn] Cannot parse {json_path.name}: {exc} — skipping.", file=sys.stderr)
        return None

    # transcript_text is stored in the .txt file, not the .json
    data["transcript_text"] = txt_path.read_text(encoding="utf-8")

    try:
        return GoldenTranscript.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Schema error in {json_path.name}: {exc} — skipping.", file=sys.stderr)
        return None


def load_suite(suite: str) -> List[GoldenTranscript]:
    """
    Load all golden transcripts whose ID starts with ``suite`` (or all when
    suite == 'golden').
    """
    if not GOLDEN_SET_DIR.exists():
        raise FileNotFoundError(f"Golden-set directory not found: {GOLDEN_SET_DIR}")

    transcripts: List[GoldenTranscript] = []
    for json_path in sorted(GOLDEN_SET_DIR.glob("*.json")):
        stem = json_path.stem
        if suite == "golden" or stem.startswith(suite):
            gt = _load_golden_transcript(json_path)
            if gt is not None:
                transcripts.append(gt)

    if not transcripts:
        raise RuntimeError(
            f"No golden transcripts found for suite '{suite}' in {GOLDEN_SET_DIR}."
        )
    return transcripts


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_COL_W = {
    "id": 26,
    "prec": 7,
    "rec": 7,
    "f1": 7,
    "own": 7,
    "date": 7,
    "empty": 7,
    "tp": 4,
    "fp": 4,
    "fn": 4,
}


def _row(m: EvalMetrics) -> str:
    empty_flag = "OK" if m.empty_case_correct else "FAIL"
    return (
        f"{m.transcript_id:<{_COL_W['id']}}"
        f"{m.precision:>{_COL_W['prec']}.3f}"
        f"{m.recall:>{_COL_W['rec']}.3f}"
        f"{m.f1:>{_COL_W['f1']}.3f}"
        f"{m.owner_accuracy:>{_COL_W['own']}.3f}"
        f"{m.due_date_accuracy:>{_COL_W['date']}.3f}"
        f"{empty_flag:>{_COL_W['empty']}}"
        f"{m.true_positives:>{_COL_W['tp']}}"
        f"{m.false_positives:>{_COL_W['fp']}}"
        f"{m.false_negatives:>{_COL_W['fn']}}"
    )


def _header() -> str:
    return (
        f"{'Transcript ID':<{_COL_W['id']}}"
        f"{'Prec':>{_COL_W['prec']}}"
        f"{'Rec':>{_COL_W['rec']}}"
        f"{'F1':>{_COL_W['f1']}}"
        f"{'OwnAcc':>{_COL_W['own']}}"
        f"{'DtAcc':>{_COL_W['date']}}"
        f"{'Empty':>{_COL_W['empty']}}"
        f"{'TP':>{_COL_W['tp']}}"
        f"{'FP':>{_COL_W['fp']}}"
        f"{'FN':>{_COL_W['fn']}}"
    )


def print_report(report: SuiteReport, verbose: bool = False) -> None:
    sep = "-" * (sum(_COL_W.values()) + len(_COL_W) - 1)
    print()
    print("=" * len(sep))
    print("  EVAL SUITE REPORT")
    print("=" * len(sep))
    print(_header())
    print(sep)
    for m in report.metrics:
        print(_row(m))
        if verbose:
            print(f"    {textwrap.shorten(m.transcript_description, width=90)}")
    print(sep)
    print(
        f"{'AVERAGES':<{_COL_W['id']}}"
        f"{report.avg_precision:>{_COL_W['prec']}.3f}"
        f"{report.avg_recall:>{_COL_W['rec']}.3f}"
        f"{report.avg_f1:>{_COL_W['f1']}.3f}"
    )
    print()
    status = "PASSED" if report.passed else "FAILED"
    gate_msg = (
        f"Gate: avg F1 {report.avg_f1:.3f} "
        f"{'>=': <2} threshold {report.threshold:.2f} -> {status}"
    )
    print(gate_msg)
    print()


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def aggregate(metrics: List[EvalMetrics], threshold: float) -> SuiteReport:
    if not metrics:
        return SuiteReport(threshold=threshold)
    n = len(metrics)
    avg_p = sum(m.precision for m in metrics) / n
    avg_r = sum(m.recall for m in metrics) / n
    avg_f1 = sum(m.f1 for m in metrics) / n
    return SuiteReport(
        metrics=metrics,
        avg_precision=round(avg_p, 4),
        avg_recall=round(avg_r, 4),
        avg_f1=round(avg_f1, 4),
        threshold=threshold,
        passed=avg_f1 >= threshold,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the extraction eval suite against the golden set.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples
            --------
              python -m evals.run
              python -m evals.run --suite golden --threshold 0.9 --verbose
              python -m evals.run --suite clean_standup --model claude-haiku-4-5
            """
        ),
    )
    parser.add_argument(
        "--suite",
        default="golden",
        help=(
            "Which subset of the golden set to run.  "
            "'golden' (default) runs all files.  "
            "Any other value is used as an ID prefix filter."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.85,
        help="Minimum average F1 required to exit 0 (default: 0.85).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("EXTRACTION_MODEL", DEFAULT_MODEL),
        help=(
            f"Anthropic model to use (default: $EXTRACTION_MODEL or {DEFAULT_MODEL})."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print transcript description under each result row.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    print(f"Loading suite '{args.suite}' from {GOLDEN_SET_DIR} ...")
    try:
        transcripts = load_suite(args.suite)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(f"Loaded {len(transcripts)} transcript(s).  Model: {args.model}")

    try:
        prompt_template = _load_prompt_template()
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    all_metrics: List[EvalMetrics] = []

    for i, gt in enumerate(transcripts, 1):
        print(f"  [{i}/{len(transcripts)}] {gt.id} ...", end=" ", flush=True)
        try:
            extracted = extract_transcript(gt.transcript_text, args.model, prompt_template)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: {exc}", file=sys.stderr)
            # Treat extraction failure as zero items found
            extracted = []

        metrics = compute_metrics(gt, extracted)
        all_metrics.append(metrics)
        print(f"F1={metrics.f1:.3f}  P={metrics.precision:.3f}  R={metrics.recall:.3f}")

    report = aggregate(all_metrics, args.threshold)
    print_report(report, verbose=args.verbose)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
