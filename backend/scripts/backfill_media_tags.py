"""Assign tags to media records using Claude Haiku.

Reads media metadata (joined with media_candidates), fetches existing tags from the DB,
asks Haiku to select relevant existing tags and suggest new ones, then creates associations
via the media_tags table.

Usage (from inside the backend container):
    python -m scripts.backfill_media_tags
    python -m scripts.backfill_media_tags --batch-size 10 --max-batches 5
    python -m scripts.backfill_media_tags --dry-run

TAG NAMING RULES (enforced by the LLM prompt):
1. Proper capitalization for proper nouns (e.g., "Gran Colombia", "Panama Canal")
2. Lowercase for common terms (e.g., "colonial architecture", "migration")
3. Singular forms when possible (e.g., "canal treaty" not "canal treaties")
4. Avoid overly broad terms (e.g., "spanish colonization" not "spain")
5. Keep tags concise — preferably 1-3 words
6. No special characters except hyphens for compound terms (e.g., "trans-isthmian railroad")
7. Use established terminology from the field
"""

import argparse
import json
import logging
import re
from datetime import datetime, timezone

import anthropic
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.article import Article
from app.models.media import Media, media_tags
from app.models.media_candidate import MediaCandidate
from app.models.tag import Tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PROMPT = """You are tagging images for a website about Panama's history, geography, and culture.

CRITICAL: You MUST use existing tags whenever they are relevant. Do NOT create a new tag if an
existing tag covers the same concept. Creating duplicates or near-duplicates is the worst mistake
you can make. Only suggest a new tag when NO existing tag captures an important aspect of the image.

EXISTING TAGS — use these by their ID:
{existing_tags}

RULES FOR NEW TAGS (only when absolutely necessary):
1. Proper capitalization for proper nouns (e.g., "Gran Colombia", "Panama Canal")
2. Lowercase for common terms (e.g., "colonial architecture", "migration")
3. Singular forms (e.g., "canal treaty" not "canal treaties")
4. Avoid overly broad terms (e.g., "spanish colonization" not "spain")
5. Concise: 1-3 words
6. No special characters except hyphens for compound terms
7. Use established historical terminology
8. Do NOT create tags that are plural/singular variants of existing tags
9. Do NOT create tags that add/remove "Panama" or "colonial" to an existing tag
10. Do NOT create tags for standalone years or dates

TAGGING GUIDELINES:
- Assign 3-7 tags per image
- Tags should cover: historical period, key figures, locations, events, themes, image type
- Only tag the primary subject, not passing details
- Balance broad thematic tags with specific content tags
- STRONGLY prefer existing tags — aim for 0-1 new tags per batch of images

Return a JSON array. Each element:
- "id" (integer): media ID
- "existing_tag_ids" (array of integers): IDs of existing tags to assign
- "new_tags" (array of strings): new tag names (ONLY if no existing tag fits)

Images to tag:
"""


def normalize_tag(name: str) -> str:
    """Normalize a tag name for fuzzy comparison.

    Strips hyphens, collapses whitespace, lowercases, and removes trailing 's'
    for basic singular/plural matching.
    """
    s = name.lower().strip()
    s = re.sub(r"[-_]", " ", s)
    s = re.sub(r"\s+", " ", s)
    # Simple singular: strip trailing 's' unless the word is <= 3 chars
    if len(s) > 3 and s.endswith("s") and not s.endswith("ss"):
        s = s[:-1]
    return s


def build_normalized_index(tags: list[Tag]) -> dict[str, Tag]:
    """Build a normalized-name -> Tag mapping for fuzzy matching."""
    index = {}
    for t in tags:
        key = normalize_tag(t.name)
        # Keep the first tag found for each normalized form
        if key not in index:
            index[key] = t
    return index


def build_item_text(media: Media, candidate: MediaCandidate | None) -> str:
    """Build a text description of a media item for the LLM."""
    parts = [f"[ID={media.id}] Title: {media.title or 'N/A'}"]
    if candidate:
        if candidate.description:
            parts.append(f"Description: {candidate.description[:300]}")
        if candidate.source_categories:
            parts.append(f"Categories: {', '.join(candidate.source_categories[:8])}")
        if candidate.tags:
            parts.append(f"Source tags: {', '.join(candidate.tags[:10])}")
        if candidate.relevance_notes:
            parts.append(f"Relevance: {candidate.relevance_notes[:200]}")
    if media.alt_text:
        parts.append(f"Alt text: {media.alt_text}")
    parts.append(f"Source: {media.source}")
    return " | ".join(parts)


def parse_response(response_text: str) -> list[dict]:
    """Extract JSON array from LLM response."""
    text = response_text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def find_fuzzy_match(tag_name: str, norm_index: dict[str, Tag]) -> Tag | None:
    """Find an existing tag that matches the normalized form of tag_name."""
    return norm_index.get(normalize_tag(tag_name))


def main():
    parser = argparse.ArgumentParser(description="Assign tags to media records using Haiku")
    parser.add_argument("--batch-size", type=int, default=15, help="Items per batch (default: 15)")
    parser.add_argument("--max-batches", type=int, default=None, help="Max batches (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Show results without writing to DB")
    args = parser.parse_args()

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    db = SessionLocal()

    try:
        # Load all existing tags for the prompt
        all_tags = db.execute(select(Tag)).scalars().all()
        tag_map = {t.id: t for t in all_tags}
        norm_index = build_normalized_index(all_tags)
        existing_tags_text = ", ".join(
            f'[{t.id}] "{t.name}"' for t in sorted(all_tags, key=lambda t: t.name)
        )
        if not existing_tags_text:
            existing_tags_text = "(none yet)"

        batches_done = 0
        total_tagged = 0
        total_new_tags = 0
        total_fuzzy_matches = 0

        while True:
            if args.max_batches and batches_done >= args.max_batches:
                break

            # Fetch media records that have no tags yet
            # Skip article feature images — they belong to articles, not the media library
            feature_image_ids = select(Article.feature_image_id).where(
                Article.feature_image_id.isnot(None)
            ).scalar_subquery()
            subq = select(media_tags.c.media_id).distinct().subquery()
            query = (
                select(Media, MediaCandidate)
                .outerjoin(MediaCandidate, MediaCandidate.media_id == Media.id)
                .where(Media.id.notin_(select(subq.c.media_id)))
                .where(Media.id.notin_(feature_image_ids))
                .limit(args.batch_size)
            )
            rows = db.execute(query).all()

            if not rows:
                logger.info("No more untagged media records.")
                break

            logger.info("Batch %d: processing %d media records", batches_done + 1, len(rows))

            if args.dry_run:
                for media, candidate in rows:
                    logger.info("  [DRY RUN] Would tag: %d — %s", media.id, (media.title or "")[:60])
                batches_done += 1
                continue

            items_text = "\n".join(
                build_item_text(media, candidate) for media, candidate in rows
            )

            prompt_text = PROMPT.format(existing_tags=existing_tags_text) + items_text

            try:
                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=4096,
                    messages=[
                        {"role": "user", "content": prompt_text}
                    ],
                )

                results = parse_response(message.content[0].text)
                media_map = {media.id: media for media, _ in rows}

                for item in results:
                    mid = item.get("id")
                    if mid not in media_map:
                        continue

                    m = media_map[mid]
                    assigned_tags = []

                    # Assign existing tags
                    for tid in item.get("existing_tag_ids", []):
                        if tid in tag_map:
                            assigned_tags.append(tag_map[tid])

                    # Create new tags (with fuzzy dedup)
                    for tag_name in item.get("new_tags", []):
                        tag_name = tag_name.strip()[:50]
                        if not tag_name:
                            continue

                        # 1. Exact case-insensitive DB check
                        existing = db.execute(
                            select(Tag).where(func.lower(Tag.name) == tag_name.lower())
                        ).scalar_one_or_none()
                        if existing:
                            assigned_tags.append(existing)
                            continue

                        # 2. Fuzzy normalized match against all known tags
                        fuzzy = find_fuzzy_match(tag_name, norm_index)
                        if fuzzy:
                            logger.info(
                                "  Fuzzy match: '%s' -> existing '%s' (id=%d)",
                                tag_name, fuzzy.name, fuzzy.id,
                            )
                            assigned_tags.append(fuzzy)
                            total_fuzzy_matches += 1
                            continue

                        # 3. Genuinely new tag
                        now = datetime.now(timezone.utc)
                        new_tag = Tag(name=tag_name, status="APPROVED", created_at=now, updated_at=now)
                        db.add(new_tag)
                        db.flush()
                        tag_map[new_tag.id] = new_tag
                        norm_index[normalize_tag(tag_name)] = new_tag
                        assigned_tags.append(new_tag)
                        total_new_tags += 1
                        logger.info("  Created new tag: '%s' (id=%d)", tag_name, new_tag.id)

                    # Deduplicate — Haiku may return the same tag twice
                    seen_ids = set()
                    unique_tags = []
                    for tg in assigned_tags:
                        if tg.id not in seen_ids:
                            seen_ids.add(tg.id)
                            unique_tags.append(tg)
                    m.tags = unique_tags
                    total_tagged += 1
                    logger.info(
                        "  Tagged media %d with %d tags: %s",
                        mid, len(unique_tags),
                        ", ".join(t.name for t in unique_tags),
                    )

                db.commit()

                # Refresh existing_tags_text for next batch (includes newly created tags)
                all_tags = db.execute(select(Tag)).scalars().all()
                tag_map = {t.id: t for t in all_tags}
                norm_index = build_normalized_index(all_tags)
                existing_tags_text = ", ".join(
                    f'[{t.id}] "{t.name}"' for t in sorted(all_tags, key=lambda t: t.name)
                )

            except Exception as e:
                logger.error("Error processing batch %d: %s", batches_done + 1, e)
                db.rollback()
                break

            batches_done += 1

        logger.info(
            "Done. Tagged %d media, created %d new tags, %d fuzzy matches prevented, in %d batches.",
            total_tagged, total_new_tags, total_fuzzy_matches, batches_done,
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()