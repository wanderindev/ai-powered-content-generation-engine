"""LLM-based relevance scoring for media candidates."""

import json
import logging
from collections.abc import Callable
from typing import Any

import anthropic
import sentry_sdk
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.media_candidate import CandidateStatus, MediaCandidate
from app.services.llm import HAIKU_MODEL, create_message, first_text

logger = logging.getLogger(__name__)

# Progress callback signature: (units_done, units_total, current_unit).
ProgressCallback = Callable[[int, int, str], None]

SCORING_PROMPT = """You are evaluating images from Wikimedia Commons for relevance to a website
about Panama's history, geography, and culture. The website covers topics like:
- Panama Canal construction and history
- Colonial era (Spanish conquest, Portobelo, Nombre de Dios)
- Pre-Columbian civilizations and archaeological sites
- Independence movements and political history
- Historical figures (Balboa, Morgan, Lesseps, Goethals, etc.)
- Geography, flora, and fauna of Panama
- Cultural heritage, traditions, architecture

For each image below, score its relevance from 0.0 to 1.0:
- 0.0-0.2: Not relevant to Panama (wrong country, generic stock image)
- 0.3-0.4: Tangentially related (general Caribbean/Latin American content)
- 0.5-0.6: Somewhat relevant (related topic but not Panama-specific)
- 0.7-0.8: Relevant (clearly about Panama or its history)
- 0.9-1.0: Highly relevant (iconic Panama imagery, important historical documents)

Respond with a JSON array. Each element must have: "id" (integer), "score" (float), "reason" (string, max 100 chars).

Images to evaluate:
"""


def score_candidates(
    db: Session,
    batch_size: int = 30,
    max_batches: int | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """
    Score unscored PENDING candidates using Claude Haiku.

    ``progress_callback(scored, total, current)`` is invoked after each committed
    batch so a background-job runner can report progress (#277). ``total`` is the
    count of unscored candidates at the start of the run. It is optional — direct
    callers (scripts/tests) can omit it.

    Returns stats about the scoring run.
    """
    stats = {"candidates_scored": 0, "batches_processed": 0, "batches_skipped": 0}
    batches_done = 0

    # Snapshot of how many candidates need scoring — the denominator for progress.
    total_unscored = (
        db.execute(
            select(func.count(MediaCandidate.id)).where(
                MediaCandidate.status == CandidateStatus.PENDING.value,
                MediaCandidate.relevance_score.is_(None),
            )
        ).scalar()
        or 0
    )
    # Tracks batches that were rolled back and skipped (transient/malformed
    # failures). Their rows still match `relevance_score IS NULL`, so without an
    # offset the same rows would be re-selected forever after a `continue`.
    skipped_batches = 0

    while True:
        if max_batches and batches_done >= max_batches:
            break

        # Fetch next batch of unscored candidates. Skip over batches that
        # previously failed-and-were-rolled-back so we don't re-fetch the same
        # unscored rows and spin forever after a `continue`.
        candidates = (
            db.execute(
                select(MediaCandidate)
                .where(
                    MediaCandidate.status == CandidateStatus.PENDING.value,
                    MediaCandidate.relevance_score.is_(None),
                )
                .offset(skipped_batches * batch_size)
                .limit(batch_size)
            )
            .scalars()
            .all()
        )

        if not candidates:
            break

        # Build the prompt with candidate metadata
        items_text = "\n".join(
            f"[ID={c.id}] Title: {c.title}"
            f" | Description: {(c.description or 'N/A')[:200]}"
            f" | Categories: {', '.join(c.source_categories[:5]) if c.source_categories else 'N/A'}"
            for c in candidates
        )

        try:
            message = create_message(
                feature="media_scoring",
                model=HAIKU_MODEL,
                max_tokens=6000,  # thinking headroom at medium effort (#610)
                output_config={"effort": "medium"},
                messages=[
                    {
                        "role": "user",
                        "content": SCORING_PROMPT + items_text,
                    }
                ],
            )

            response_text = first_text(message).strip()
            # Extract JSON from response (handle markdown code blocks)
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            scores = json.loads(response_text)

            # Apply scores to candidates
            candidate_map = {c.id: c for c in candidates}
            for item in scores:
                cid = item.get("id")
                if cid in candidate_map:
                    candidate_map[cid].relevance_score = round(
                        max(0.0, min(1.0, float(item["score"]))), 2
                    )
                    candidate_map[cid].relevance_notes = item.get("reason", "")[:500]
                    stats["candidates_scored"] += 1

            db.commit()
            batches_done += 1
            stats["batches_processed"] = batches_done

            if progress_callback is not None:
                progress_callback(
                    stats["candidates_scored"], total_unscored, f"batch {batches_done}"
                )

        except (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
        ) as e:
            # Transient — the next batch would likely succeed. Skip this one and
            # continue; the rolled-back candidates remain unscored.
            logger.warning(
                "Transient error scoring batch %d, skipping: %s",
                batches_done + skipped_batches + 1,
                e,
            )
            db.rollback()
            skipped_batches += 1
            stats["batches_skipped"] = skipped_batches
            continue
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            # Malformed LLM response for this specific batch; the next batch is
            # independent. (json.JSONDecodeError subclasses ValueError.)
            logger.error(
                "Malformed LLM response for batch %d, skipping: %s",
                batches_done + skipped_batches + 1,
                e,
            )
            sentry_sdk.capture_exception(e)
            db.rollback()
            skipped_batches += 1
            stats["batches_skipped"] = skipped_batches
            continue
        except anthropic.APIError as e:
            # Auth, permission, etc. — terminal; no point continuing.
            logger.error(
                "Anthropic API error in batch %d: %s",
                batches_done + skipped_batches + 1,
                e,
            )
            sentry_sdk.capture_exception(e)
            db.rollback()
            break
        # Any other exception propagates — it signals a real bug.

    return stats
