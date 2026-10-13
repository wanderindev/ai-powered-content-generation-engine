"""Crawl Wikimedia Commons for historical figures related to Panama.

Usage (inside backend container):
    python -m scripts.crawl_historical_figures
    python -m scripts.crawl_historical_figures --dry-run
"""

import argparse
import logging

from app.core.database import SessionLocal
from app.services.wikimedia import WikimediaService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Panama-specific figures — safe to crawl at depth 2
PANAMA_FIGURES_DEPTH_2 = [
    "Vasco Nunez de Balboa",
    "Pedro Arias Davila",
    "Rodrigo de Bastidas",
    "Gonzalo Fernandez de Oviedo",
    "Tomas Herrera",
    "Manuel Amador Guerrero",
    "Omar Torrijos",
    "Philippe Bunau-Varilla",
    "George Washington Goethals",
    "William Crawford Gorgas",
    "John Frank Stevens",
    "Juan de la Cosa",
]

# Broader figures — depth 1 to get direct images without wandering too far
BROADER_FIGURES_DEPTH_1 = [
    "Ferdinand de Lesseps",
    "Francisco Pizarro",
    "Diego de Almagro",
]

# Very broad categories — depth 0 only (direct files in the category)
BROAD_CATEGORIES_DEPTH_0 = [
    "Conquistadors",
    "Simon Bolivar",
    "Theodore Roosevelt",
    "Jimmy Carter",
    "Spanish colonization of the Americas",
]


def main():
    parser = argparse.ArgumentParser(description="Crawl Wikimedia for historical figures")
    parser.add_argument("--dry-run", action="store_true", help="List categories without crawling")
    parser.add_argument("--max-per-category", type=int, default=100)
    parser.add_argument("--rate-limit", type=float, default=1.5, help="Seconds between API requests")
    args = parser.parse_args()

    groups = [
        ("Depth 2 — Panama-specific figures", PANAMA_FIGURES_DEPTH_2, 2),
        ("Depth 1 — Broader figures", BROADER_FIGURES_DEPTH_1, 1),
        ("Depth 0 — Very broad categories", BROAD_CATEGORIES_DEPTH_0, 0),
    ]

    if args.dry_run:
        for label, categories, depth in groups:
            logger.info("=== %s (max_depth=%d) ===", label, depth)
            for cat in categories:
                logger.info("  %s", cat)
        total = sum(len(cats) for _, cats, _ in groups)
        logger.info("Total: %d categories across 3 depth groups", total)
        return

    service = WikimediaService(rate_limit=args.rate_limit)
    db = SessionLocal()

    try:
        grand_total = {"discovered": 0, "new": 0, "updated": 0, "skipped": 0}

        for label, categories, depth in groups:
            logger.info("=== %s (max_depth=%d) ===", label, depth)
            stats = service.crawl_categories(
                db=db,
                seed_categories=categories,
                max_depth=depth,
                max_per_category=args.max_per_category,
            )
            logger.info("  Results: %s", stats)
            for key in grand_total:
                grand_total[key] += stats.get(key, 0)

        logger.info("=== GRAND TOTAL ===")
        logger.info("  Discovered: %d, New: %d, Updated: %d, Skipped: %d",
                     grand_total["discovered"], grand_total["new"],
                     grand_total["updated"], grand_total["skipped"])
    finally:
        db.close()


if __name__ == "__main__":
    main()