"""Sequence and boot-ID evaluation (C1 identity and ordering).

Compares an incoming message's ``(boot_id, sequence)`` against the source
registry's last-seen values. A changed boot ID legitimizes a sequence reset;
within one boot, equal sequences are duplicates, lower sequences are
out-of-order late arrivals (retained in history, never advancing the
counter), and jumps report the gap size. The database's partial unique index
on ``(source_id, source_boot_id, sequence)`` remains the final duplicate
authority — this assessment drives flags and metrics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SequenceAssessment:
    treat_as_duplicate: bool = False
    boot_reset: bool = False
    gap_before: int | None = None
    out_of_order: bool = False
    advance: bool = True  # should the registry's last (boot, sequence) move to this message?

    def arrival_notes(self) -> dict:
        notes: dict = {}
        if self.boot_reset:
            notes["boot_reset"] = True
        if self.gap_before:
            notes["gap_before"] = self.gap_before
        if self.out_of_order:
            notes["out_of_order"] = True
        return notes


def assess_sequence(
    last_sequence: int | None,
    last_boot_id: str | None,
    sequence: int | None,
    boot_id: str | None,
) -> SequenceAssessment:
    if sequence is None:
        return SequenceAssessment(advance=False)

    if last_sequence is None:
        return SequenceAssessment()

    if boot_id != last_boot_id:
        # New (or newly-absent) boot domain: sequences are not comparable.
        return SequenceAssessment(boot_reset=last_boot_id is not None or boot_id is not None)

    if sequence == last_sequence:
        return SequenceAssessment(treat_as_duplicate=True, advance=False)
    if sequence < last_sequence:
        return SequenceAssessment(out_of_order=True, advance=False)

    gap = sequence - last_sequence - 1
    return SequenceAssessment(gap_before=gap if gap > 0 else None)
