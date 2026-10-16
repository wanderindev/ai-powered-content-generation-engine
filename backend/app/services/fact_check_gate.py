"""Fact-check gates on the edu pipeline's spending and publishing steps (#462).

Epic #459 gave every edu artifact a fact-check state; this is what that state is
*for*. Without a gate the pipeline will happily spend ElevenLabs credits on a
narration script it knows asserts something false, and put the result in front of
a classroom in Diego's voice.

Two tiers, chosen with the operator (2026-08-10):

* **INACCURATE blocks everything.** A claim the grounding tier judged
  contradicted by the source research is a known error. There is no step worth
  taking on content that carries one, internal or public.
* **Unresolved claims** (``NEEDS_DEEP_RESEARCH``, ``DISPUTED``) block only the
  **public** steps. They are open questions, not known errors, and the
  deep-research queue can take hours — hard-blocking generation behind it would
  stall the whole pipeline. Internal steps proceed; the admin UI warns first.

``CORRECTED`` is explicitly *not* blocking: it is the terminal green state for an
INACCURATE claim whose fix was applied to the content (#387). The claim is still
wrong in the world — the content no longer asserts it.

**Never fact-checked is not blocked.** A material with no claim rows publishes
after an explicit confirm in the admin UI, matching how the blog pipeline treats
an unchecked article (``factCheckWarning`` warns, it does not block). Enforcing
otherwise here would diverge the two pipelines and strand existing material.

The soft warnings live in the frontend precisely because they are meant to be
overridable; a backend "warning" would need an override flag, and an override
flag is just a block that everyone learns to bypass. What the backend refuses,
it refuses outright.
"""

from __future__ import annotations

import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.enums import FactCheckSubject, FactCheckVerdict
from app.models.fact_check import FactCheckClaim

logger = logging.getLogger(__name__)

#: Internal steps — cost money or compute, but nothing leaves the building.
STEP_INTERNAL = "internal"
#: Public steps — reach teachers, or Diego's channel. Irreversible in practice.
STEP_PUBLIC = "public"

#: A known error. Blocks every gated step.
_BLOCKING_ALWAYS = (FactCheckVerdict.INACCURATE.value,)
#: Open questions. Block only the public steps.
_BLOCKING_PUBLIC_ONLY = (
    FactCheckVerdict.NEEDS_DEEP_RESEARCH.value,
    FactCheckVerdict.DISPUTED.value,
)


class FactCheckGateError(RuntimeError):
    """A gated step was attempted on content with blocking claims.

    Carries the per-verdict counts so the API layer can say which, and how many.
    """

    def __init__(self, message: str, counts: dict[str, int]):
        self.counts = counts
        super().__init__(message)


def material_verdict_counts(db: Session, material_id: int) -> dict[str, int]:
    """Per-verdict claim counts for one edu material. Empty when never checked."""
    rows = (
        db.query(FactCheckClaim.verdict, func.count(FactCheckClaim.id))
        .filter(
            FactCheckClaim.subject_type == FactCheckSubject.EDU_MATERIAL.value,
            FactCheckClaim.subject_id == material_id,
        )
        .group_by(FactCheckClaim.verdict)
        .all()
    )
    return {verdict: count for verdict, count in rows}


def blocking_verdicts(step: str) -> tuple[str, ...]:
    """Which verdicts block *step*."""
    if step == STEP_PUBLIC:
        return _BLOCKING_ALWAYS + _BLOCKING_PUBLIC_ONLY
    return _BLOCKING_ALWAYS


def enforce_material_gate(db: Session, material_id: int, step: str, *, action: str) -> None:
    """Raise :class:`FactCheckGateError` if *material_id* may not take *step*.

    *action* names the operation in the error text so the operator is told what
    was refused, not just that something was. A material with no claims passes —
    see the module docstring on why "never checked" is not a block.
    """
    counts = material_verdict_counts(db, material_id)
    if not counts:
        return

    offending = {
        verdict: counts[verdict] for verdict in blocking_verdicts(step) if counts.get(verdict)
    }
    if not offending:
        return

    detail = ", ".join(f"{count} {verdict}" for verdict, count in sorted(offending.items()))
    if step == STEP_PUBLIC:
        remedy = (
            "Resolve them in the fact-check modal (apply corrections, or work the "
            "deep-research queue) before publishing."
        )
    else:
        remedy = "Apply the corrections in the fact-check modal first."
    logger.info("Fact-check gate refused %s on edu material %d: %s", action, material_id, detail)
    raise FactCheckGateError(
        f"Cannot {action}: this material has {detail} fact-check claim(s). {remedy}",
        counts,
    )


def invalidate_material_claims(db: Session, material_id: int) -> int:
    """Delete a material's fact-check claims because its text was replaced.

    A gate is only as trustworthy as the freshness of what it reads. Claims
    describe specific sentences; once the guide is regenerated or the deck's
    narration rewritten, those sentences may not exist and the verdicts describe
    nothing. Keeping them would let the gate block on an error that was already
    written away — or, worse, pass a regenerated material on the strength of an
    older run's VERIFIED rows.

    Flushes but does not commit; the caller owns the transaction. Returns the
    number of rows removed.
    """
    removed = (
        db.query(FactCheckClaim)
        .filter(
            FactCheckClaim.subject_type == FactCheckSubject.EDU_MATERIAL.value,
            FactCheckClaim.subject_id == material_id,
        )
        .delete(synchronize_session=False)
    )
    if removed:
        logger.info(
            "Invalidated %d fact-check claim(s) for edu material %d (content replaced)",
            removed,
            material_id,
        )
    db.flush()
    return removed
