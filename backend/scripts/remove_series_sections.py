#!/usr/bin/env python3
"""
Remove "About this Article" and "Continue Reading" sections from articles.

These sections will be regenerated dynamically after translation with proper
bilingual support and links.

Usage:
    python scripts/remove_series_sections.py --preview
    python scripts/remove_series_sections.py --execute
"""
import argparse
import re
import sys
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
from app.core.config import get_settings


def remove_series_sections(content: str) -> tuple[str, dict]:
    """
    Remove About this Article and Continue Reading sections from content.

    Returns:
        Tuple of (cleaned_content, dict with removal stats)
    """
    if not content:
        return content, {"about_removed": False, "continue_removed": False}

    stats = {"about_removed": False, "continue_removed": False}

    # Remove "About this Article" section
    # Variations:
    #   - **About this Article** or ***About this Article*** (2 or 3 stars)
    #   - May have spaces inside stars: **  About this Article **
    #   - Ends with --- or * * * as separator
    #   - May have extra blank lines before next content
    #   - Next content may start with ## or plain text
    # Pattern: from start of content with **/** About... to separator (--- or * * *)
    about_pattern = r'^\*{2,3}\s*About this Article\s*\*{2,3}.*?(---|\* \* \*)\s*\n+'
    if re.search(about_pattern, content, re.DOTALL | re.MULTILINE):
        content = re.sub(about_pattern, '', content, flags=re.DOTALL | re.MULTILINE)
        stats["about_removed"] = True

    # Remove "Continue Reading" section
    # Variations:
    #   - **Continue Reading** or ***Continue Reading*** (2 or 3 stars)
    #   - Preceded by --- or * * * as separator, OR just blank lines
    # Pattern: from separator (or blank lines) to end of content
    continue_pattern = r'\n+(---|\* \* \*)?\s*\n+\*{2,3}\s*Continue Reading\s*\*{2,3}.*$'
    if re.search(continue_pattern, content, re.DOTALL):
        content = re.sub(continue_pattern, '', content, flags=re.DOTALL)
        stats["continue_removed"] = True

    # Clean up any leading/trailing whitespace
    content = content.strip()

    return content, stats


def get_db_connection():
    """Get database connection."""
    settings = get_settings()
    return psycopg2.connect(settings.database_url)


def preview_changes():
    """Preview what would be removed."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get articles that are part of a series
    cursor.execute("""
        SELECT id, title, content
        FROM articles
        WHERE series_parent_id IS NOT NULL OR id IN (
            SELECT DISTINCT series_parent_id FROM articles WHERE series_parent_id IS NOT NULL
        )
        ORDER BY id
    """)

    articles = cursor.fetchall()
    total_about = 0
    total_continue = 0

    for article_id, title, content in articles:
        if not content:
            continue

        _, stats = remove_series_sections(content)

        if stats["about_removed"] or stats["continue_removed"]:
            print(f"\nArticle {article_id}: {title[:60]}...")
            if stats["about_removed"]:
                print("  - Would remove: About this Article section")
                total_about += 1
            if stats["continue_removed"]:
                print("  - Would remove: Continue Reading section")
                total_continue += 1

    cursor.close()
    conn.close()

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  About this Article sections: {total_about}")
    print(f"  Continue Reading sections: {total_continue}")
    print(f"{'='*60}")


def execute_removal():
    """Execute the removal on the database."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get articles that are part of a series
    cursor.execute("""
        SELECT id, title, content
        FROM articles
        WHERE series_parent_id IS NOT NULL OR id IN (
            SELECT DISTINCT series_parent_id FROM articles WHERE series_parent_id IS NOT NULL
        )
        ORDER BY id
    """)

    articles = cursor.fetchall()
    total_updated = 0
    total_about = 0
    total_continue = 0

    for article_id, title, content in articles:
        if not content:
            continue

        cleaned_content, stats = remove_series_sections(content)

        if stats["about_removed"] or stats["continue_removed"]:
            cursor.execute(
                "UPDATE articles SET content = %s WHERE id = %s",
                (cleaned_content, article_id)
            )
            total_updated += 1
            if stats["about_removed"]:
                total_about += 1
            if stats["continue_removed"]:
                total_continue += 1
            print(f"Updated article {article_id}: {title[:50]}...")

    conn.commit()
    cursor.close()
    conn.close()

    print(f"\n{'='*60}")
    print(f"Done!")
    print(f"  Articles updated: {total_updated}")
    print(f"  About sections removed: {total_about}")
    print(f"  Continue Reading sections removed: {total_continue}")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Remove series sections from articles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/remove_series_sections.py --preview
    python scripts/remove_series_sections.py --execute
        """,
    )
    parser.add_argument(
        "--preview", action="store_true", help="Preview changes without modifying"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Execute removal on database"
    )
    args = parser.parse_args()

    if not any([args.preview, args.execute]):
        parser.print_help()
        return

    if args.preview:
        preview_changes()
    elif args.execute:
        print("This will modify article content in the database.")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() == "yes":
            execute_removal()
        else:
            print("Aborted.")


if __name__ == "__main__":
    main()
