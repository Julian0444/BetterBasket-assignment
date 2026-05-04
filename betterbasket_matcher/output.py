"""Output writing and validation for the BetterBasket matching pipeline (Phase 7).

Two writers and one validator:

- write_matches:        emit matches.csv with the locked header
                        ``item_id_A,item_id_B``.
- write_matches_audit:  emit matches_audit.csv with the locked 9-column header
                        in order. One row per valid A item processed.
- validate_matches_csv: verify header / numeric / membership / dedup /
                        required-pair / min_rows constraints. Returns a
                        ValidationResult; never raises on data violations.

No I/O on stdin; no orchestration; no scoring. Pipeline owns the
PipelineResult shape and feeds these helpers.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Mapping, Set, Union

from betterbasket_matcher.io import is_numeric_id


# ---------------------------------------------------------------------------
# Audit header (locked)
# ---------------------------------------------------------------------------

AUDIT_HEADER: tuple = (
    "item_id_A",
    "item_id_B",
    "score",
    "retrieval_score",
    "top1_top2_margin",
    "source",
    "decision",
    "reason",
    "llm_confidence",
)

MATCHES_HEADER: tuple = ("item_id_A", "item_id_B")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    row_count: int = 0


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_matches(path: Union[str, Path], matches: Iterable) -> None:
    """Write the accepted matches to ``path`` with header item_id_A,item_id_B."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(MATCHES_HEADER)
        for pair in matches:
            a, b = pair[0], pair[1]
            writer.writerow([a, b])


def write_matches_audit(path: Union[str, Path], audit_rows: Iterable[Mapping]) -> None:
    """Write the audit CSV with the exact 9-column header in order."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(AUDIT_HEADER)
        for row in audit_rows:
            writer.writerow([row.get(col, "") for col in AUDIT_HEADER])


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_matches_csv(
    path: Union[str, Path],
    valid_ids_a: Set[str],
    valid_ids_b: Set[str],
    min_rows: int,
    required_pairs: Mapping[str, str],
) -> ValidationResult:
    """Validate a matches.csv against the contract.

    Checks (independent — all collected; the function never raises on data):

    1. Header equals ``item_id_A,item_id_B`` exactly.
    2. Each id is numeric.
    3. Each id is a member of the supplied ``valid_ids_a`` / ``valid_ids_b``.
    4. No duplicate ``item_id_A`` (even if the duplicate row is byte-identical).
    5. Every key in ``required_pairs`` appears in the file mapped to the
       expected B; otherwise emit a clear "expected X but got Y" error.
    6. Row count is >= ``min_rows``.

    The function always honors the ``min_rows`` it is given; the CLI's
    ``--allow-under-min-rows`` flag is a CLI concern only.
    """
    errors: List[str] = []
    row_count = 0
    seen_a: dict = {}  # item_id_A -> first item_id_B seen
    pairs_found: dict = {}  # item_id_A -> item_id_B (final-most-recent for required-pair check)

    p = Path(path)
    if not p.exists():
        errors.append(f"file not found: {p}")
        return ValidationResult(ok=False, errors=errors, row_count=0)

    with p.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            errors.append("file is empty (no header)")
            return ValidationResult(ok=False, errors=errors, row_count=0)

        if header != list(MATCHES_HEADER):
            errors.append(
                f"invalid header: expected {list(MATCHES_HEADER)}, got {header}"
            )
            # Continue scanning rows so we can also surface other problems.

        for line_no, row in enumerate(reader, start=2):
            if len(row) != 2:
                errors.append(
                    f"line {line_no}: expected 2 columns, got {len(row)} ({row!r})"
                )
                continue
            a, b = row[0], row[1]
            row_count += 1

            if not is_numeric_id(a):
                errors.append(f"line {line_no}: non-numeric item_id_A {a!r}")
            elif a not in valid_ids_a:
                errors.append(
                    f"line {line_no}: item_id_A {a} not in validated A id set"
                )

            if not is_numeric_id(b):
                errors.append(f"line {line_no}: non-numeric item_id_B {b!r}")
            elif b not in valid_ids_b:
                errors.append(
                    f"line {line_no}: item_id_B {b} not in validated B id set"
                )

            if a in seen_a:
                errors.append(
                    f"line {line_no}: duplicate item_id_A {a} "
                    f"(first seen mapped to {seen_a[a]}, now {b})"
                )
            else:
                seen_a[a] = b
            pairs_found[a] = b

    if row_count < min_rows:
        errors.append(
            f"row_count {row_count} below min_rows {min_rows}"
        )

    for a, expected_b in required_pairs.items():
        if a not in pairs_found:
            errors.append(
                f"required pair missing: item_id_A {a} expected {expected_b} but A absent"
            )
            continue
        actual_b = pairs_found[a]
        if actual_b != expected_b:
            errors.append(
                f"required pair mismatch: item_id_A {a} expected {expected_b} but got {actual_b}"
            )

    return ValidationResult(ok=not errors, errors=errors, row_count=row_count)
