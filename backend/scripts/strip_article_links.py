#!/usr/bin/env python3
"""
Strip all markdown links from Historical Panama articles.
Converts [link text](url) to just "link text" (trimmed).

Usage:
    python scripts/strip_article_links.py --preview    # Preview changes on a few articles
    python scripts/strip_article_links.py --dry-run    # Show what would change
    python scripts/strip_article_links.py --execute    # Actually modify the database
"""
import argparse
import re
import sys
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import SessionLocal
from app.models.article import Article
from app.models.category import Category, Taxonomy

# Historical Panama taxonomy ID
HISTORICAL_PANAMA_TAXONOMY_ID = 1


def strip_all_links(content: str) -> str:
    """
    Remove all markdown links, keeping only the text.

    Handles:
    - [text](url) -> text
    - [ text ](url) -> text (trimmed)
    - [text with (parens)](url) -> text with (parens)

    Args:
        content: Markdown content with potential links

    Returns:
        Content with links converted to plain text
    """
    if not content:
        return content

    # Pattern explanation:
    # \[           - Opening bracket
    # ([^\[\]\n]{1,200})  - Capture group: 1-200 chars that aren't [ or ] or newline
    #                       (excluding [ prevents matching across unclosed brackets)
    # \]           - Closing bracket
    # \(           - Opening paren
    # [^)\n]+      - One or more chars that aren't ) or newline
    # \)           - Closing paren
    #
    # By excluding [ ] and newlines, we avoid matching across
    # paragraph breaks and malformed links
    pattern = r"\[([^\[\]\n]{1,200})\]\([^)\n]+\)"

    def replace_link(match):
        text = match.group(1)
        return text.strip()  # Remove leading/trailing spaces

    return re.sub(pattern, replace_link, content)


def get_historical_panama_articles(session):
    """Get all articles in Historical Panama taxonomy."""
    return (
        session.query(Article)
        .join(Category)
        .join(Taxonomy)
        .filter(Taxonomy.id == HISTORICAL_PANAMA_TAXONOMY_ID)
        .all()
    )


def preview_changes(session, limit=3):
    """Preview link removal on a few articles."""
    articles = get_historical_panama_articles(session)[:limit]

    for article in articles:
        print(f"\n{'='*60}")
        print(f"Article {article.id}: {article.title}")
        print(f"{'='*60}")

        original = article.content or ""

        # Find all links in original
        links = re.findall(r"\[[^\]]+\]\([^)]+\)", original)
        if links:
            print(f"\nFound {len(links)} links:")
            for link in links[:5]:
                # Truncate long links for display
                display = link if len(link) <= 100 else link[:97] + "..."
                print(f"  - {display}")
            if len(links) > 5:
                print(f"  ... and {len(links) - 5} more")

            # Show example transformations
            print("\nExample transformations:")
            for link in links[:3]:
                text = re.match(r"\[([^\]]+)\]", link).group(1).strip()
                print(f"  '{link[:60]}...' -> '{text}'")
        else:
            print("\nNo links found in this article.")


def process_articles(session, dry_run=True):
    """Process all Historical Panama articles."""
    articles = get_historical_panama_articles(session)

    print(f"Found {len(articles)} articles in Historical Panama taxonomy")

    modified_count = 0
    total_links = 0

    for article in articles:
        original = article.content or ""
        modified = strip_all_links(original)

        if original != modified:
            # Count links removed
            links = re.findall(r"\[[^\]]+\]\([^)]+\)", original)
            total_links += len(links)
            modified_count += 1

            if not dry_run:
                article.content = modified

            if dry_run:
                print(f"  Article {article.id}: {len(links)} links to remove")

    if not dry_run:
        session.commit()
        print(f"\nUpdated {modified_count} articles, removed {total_links} links total")
    else:
        print(f"\n[DRY RUN] Would update {modified_count} articles, removing {total_links} links")

    return modified_count


def main():
    parser = argparse.ArgumentParser(
        description="Strip markdown links from Historical Panama articles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/strip_article_links.py --preview
    python scripts/strip_article_links.py --dry-run
    python scripts/strip_article_links.py --execute
        """,
    )
    parser.add_argument(
        "--preview", action="store_true", help="Preview changes on a few articles"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without modifying"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Actually modify the database"
    )
    args = parser.parse_args()

    if not any([args.preview, args.dry_run, args.execute]):
        parser.print_help()
        return

    session = SessionLocal()
    try:
        if args.preview:
            preview_changes(session)
        elif args.dry_run:
            process_articles(session, dry_run=True)
        elif args.execute:
            print("WARNING: This will permanently modify article content in the database.")
            print("Make sure you have a backup before proceeding.")
            confirm = input("\nType 'yes' to confirm: ")
            if confirm.lower() == "yes":
                process_articles(session, dry_run=False)
            else:
                print("Aborted.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
