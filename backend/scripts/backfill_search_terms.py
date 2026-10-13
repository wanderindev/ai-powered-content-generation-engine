"""Backfill search_terms and alt_text for approved media using Claude Haiku.

Joins media with media_candidates to get rich metadata (description, source_categories,
tags, relevance_notes), sends it to Haiku, and updates media.search_terms and media.alt_text.

Usage (from inside the backend container):
    python -m scripts.backfill_search_terms
    python -m scripts.backfill_search_terms --batch-size 10 --max-batches 5
    python -m scripts.backfill_search_terms --dry-run
"""

import argparse
import json
import logging
import re

import anthropic
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.article import Article
from app.models.media import Media
from app.models.media_candidate import MediaCandidate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PROMPT = """You are generating bilingual search terms and alt text for images on a website
about Panama's history, geography, and culture.

For each image below, return:
1. **search_terms**: A comma-separated list of 10-20 search keywords in BOTH English and Spanish.
   Include terms for: the subject matter, historical period, location, people depicted,
   type of image (photo, engraving, map, painting, etc.), and relevant themes.
   Example: "Panama Canal, Canal de Panamá, construction, construcción, Culebra Cut, excavation,
   steam shovel, 1910s, engineering, ingeniería"

2. **alt_text**: A concise, descriptive alt text (max 200 chars) in English that describes
   what the image shows. Good alt text helps visually impaired users and improves SEO.
   Example: "Black and white photograph of workers operating steam shovels during the Culebra Cut excavation, Panama Canal, circa 1910"

Respond with a JSON array. Each element must have:
- "id" (integer): the media ID
- "search_terms" (string): comma-separated bilingual keywords
- "alt_text" (string): descriptive alt text, max 200 characters

Images to process:
"""


def build_item_text(media: Media, candidate: MediaCandidate | None) -> str:
    """Build a text description of a media item for the LLM."""
    parts = [f"[ID={media.id}] Title: {media.title or 'N/A'}"]
    if candidate:
        if candidate.description:
            parts.append(f"Description: {candidate.description[:300]}")
        if candidate.source_categories:
            parts.append(f"Categories: {', '.join(candidate.source_categories[:8])}")
        if candidate.tags:
            parts.append(f"Tags: {', '.join(candidate.tags[:10])}")
        if candidate.relevance_notes:
            parts.append(f"Relevance: {candidate.relevance_notes[:200]}")
    parts.append(f"Source: {media.source}")
    return " | ".join(parts)


def extract_json_text(response_text: str) -> str:
    """Strip markdown code fences from LLM response."""
    text = response_text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def parse_response(response_text: str) -> list[dict]:
    """Extract JSON array from LLM response, with fallback repair."""
    text = extract_json_text(response_text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: extract individual JSON objects via regex
        objects = []
        for match in re.finditer(r'\{[^{}]*"id"\s*:\s*(\d+)[^{}]*\}', text):
            try:
                objects.append(json.loads(match.group(0)))
            except json.JSONDecodeError:
                continue
        if objects:
            logger.warning("JSON repair: extracted %d objects via regex fallback", len(objects))
            return objects
        raise


def main():
    parser = argparse.ArgumentParser(description="Backfill search_terms and alt_text for media")
    parser.add_argument("--batch-size", type=int, default=20, help="Items per Haiku batch (default: 20)")
    parser.add_argument("--max-batches", type=int, default=None, help="Max batches to process (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without calling Haiku")
    args = parser.parse_args()

    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    db = SessionLocal()

    try:
        batches_done = 0
        total_updated = 0

        while True:
            if args.max_batches and batches_done >= args.max_batches:
                break

            # Fetch media records missing search_terms, joined with candidates
            # Skip article feature images — they belong to articles, not the media library
            feature_image_ids = select(Article.feature_image_id).where(
                Article.feature_image_id.isnot(None)
            ).scalar_subquery()
            query = (
                select(Media, MediaCandidate)
                .outerjoin(MediaCandidate, MediaCandidate.media_id == Media.id)
                .where(Media.search_terms.is_(None))
                .where(Media.id.notin_(feature_image_ids))
                .limit(args.batch_size)
            )
            rows = db.execute(query).all()

            if not rows:
                logger.info("No more media records to process.")
                break

            logger.info("Batch %d: processing %d media records", batches_done + 1, len(rows))

            if args.dry_run:
                for media, candidate in rows:
                    logger.info("  [DRY RUN] Would process: %d — %s", media.id, (media.title or "")[:60])
                batches_done += 1
                continue

            # Build prompt
            items_text = "\n".join(
                build_item_text(media, candidate) for media, candidate in rows
            )

            try:
                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=4096,
                    messages=[
                        {"role": "user", "content": PROMPT + items_text}
                    ],
                )

                results = parse_response(message.content[0].text)
                media_map = {media.id: media for media, _ in rows}

                for item in results:
                    mid = item.get("id")
                    if mid in media_map:
                        m = media_map[mid]
                        m.search_terms = item.get("search_terms", "")
                        new_alt = item.get("alt_text", "")
                        if new_alt:
                            m.alt_text = new_alt[:255]
                        total_updated += 1
                        logger.info("  Updated media %d: %s", mid, (m.title or "")[:50])

                db.commit()

            except Exception as e:
                logger.error("Error processing batch %d: %s", batches_done + 1, e)
                db.rollback()
                # Mark these items with empty search_terms so we don't retry them forever
                for media, _ in rows:
                    media.search_terms = ""
                db.commit()
                logger.info("  Marked %d items with empty search_terms to skip", len(rows))

            batches_done += 1

        logger.info("Done. Updated %d media records in %d batches.", total_updated, batches_done)

    finally:
        db.close()


if __name__ == "__main__":
    main()