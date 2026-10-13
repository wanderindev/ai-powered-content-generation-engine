"""Auto-approve high-relevance media candidates by calling the approve endpoint.

Usage (from inside the backend container):
    python -m scripts.auto_approve_candidates --threshold 0.9 --batch-size 20 --pause 60

Or deploy as a one-shot Docker service (see docker-compose.worker.yml).
"""

import argparse
import logging
import time

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "http://backend:8000"


def get_pending_candidates(api_base: str, threshold: float, max_pixels: int = 0, page_size: int = 200):
    """Fetch PENDING candidates with relevance >= threshold and no media_id."""
    all_candidates = []
    skipped_large = 0
    page = 1
    while True:
        resp = httpx.get(
            f"{api_base}/api/v1/media-library/candidates",
            params={
                "status": "PENDING",
                "min_score": threshold,
                "page": page,
                "page_size": page_size,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        # Only include candidates without a CDN thumbnail (not yet approved/uploaded)
        for item in data["items"]:
            if item.get("thumbnail_cdn_url"):
                continue
            # Skip images that are too large (would OOM the server)
            if max_pixels > 0:
                w = item.get("width") or 0
                h = item.get("height") or 0
                if w * h > max_pixels:
                    skipped_large += 1
                    continue
            all_candidates.append(item)
        if page * page_size >= data["total"]:
            break
        page += 1
    if skipped_large:
        logger.info("Skipped %d candidates exceeding max-pixels limit", skipped_large)
    return all_candidates


def approve_candidate(api_base: str, candidate_id: int) -> bool:
    """Call the approve endpoint for a single candidate. Returns True on success."""
    try:
        resp = httpx.post(
            f"{api_base}/api/v1/media-library/candidates/{candidate_id}/approve",
            json={"notes": "Auto-approved by relevance score"},
            timeout=120,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            # Already approved — skip
            logger.info("  Candidate %d already approved, skipping", candidate_id)
            return True
        logger.error("  HTTP %d for candidate %d: %s", e.response.status_code, candidate_id, e.response.text[:200])
        return False
    except httpx.HTTPError as e:
        logger.error("  Request failed for candidate %d: %s", candidate_id, e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Auto-approve high-relevance media candidates")
    parser.add_argument("--threshold", type=float, default=0.9, help="Minimum relevance score (default: 0.9)")
    parser.add_argument("--batch-size", type=int, default=20, help="Candidates per batch (default: 20)")
    parser.add_argument("--pause", type=int, default=60, help="Seconds to pause between batches (default: 60)")
    parser.add_argument("--api-base", type=str, default=DEFAULT_API_BASE, help="Backend API base URL")
    parser.add_argument("--max-pixels", type=int, default=50_000_000,
                        help="Skip images exceeding this pixel count (w*h). Default: 50M (~7000x7000). Set 0 to disable.")
    parser.add_argument("--request-pause", type=int, default=5,
                        help="Seconds to pause between each request (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="List candidates without approving")
    args = parser.parse_args()

    logger.info("Fetching PENDING candidates with relevance >= %.2f ...", args.threshold)
    candidates = get_pending_candidates(args.api_base, args.threshold, max_pixels=args.max_pixels)
    logger.info("Found %d candidates to process", len(candidates))

    if not candidates:
        logger.info("Nothing to do.")
        return

    if args.dry_run:
        for c in candidates:
            logger.info("  [DRY RUN] Would approve: %d — %s (score: %.2f)", c["id"], c["title"][:60], c["relevance_score"] or 0)
        return

    approved = 0
    failed = 0
    total = len(candidates)

    for i, c in enumerate(candidates):
        logger.info("[%d/%d] Approving candidate %d: %s (score: %.2f, %sx%s)",
                     i + 1, total, c["id"], c["title"][:60],
                     c["relevance_score"] or 0,
                     c.get("width", "?"), c.get("height", "?"))
        if approve_candidate(args.api_base, c["id"]):
            approved += 1
        else:
            failed += 1

        # Pause between each request to avoid OOM
        if args.request_pause and (i + 1) < total:
            time.sleep(args.request_pause)

        # Longer pause between batches
        if (i + 1) % args.batch_size == 0 and (i + 1) < total:
            logger.info("Batch complete. Pausing %d seconds...", args.pause)
            time.sleep(args.pause)

    logger.info("Done. Approved: %d, Failed: %d, Total: %d", approved, failed, total)


if __name__ == "__main__":
    main()