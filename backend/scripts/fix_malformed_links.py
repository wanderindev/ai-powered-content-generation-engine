#!/usr/bin/env python3
"""
Fix malformed markdown links in Historical Panama articles.

Handles links with missing opening bracket like:
    "text Name](url)" -> "text Name"

The name is extracted from the URL slug and used to identify what to keep.
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


def slug_to_name(slug: str) -> str:
    """
    Convert a URL slug to a probable name.

    Examples:
        'vasco-nunez-de-balboa' -> 'Vasco Núñez de Balboa'
        'panama-city' -> 'Panama City'
        'francisco-pizarro' -> 'Francisco Pizarro'
    """
    # Common accent mappings for Spanish names
    accent_map = {
        "nunez": "Núñez",
        "nino": "Niño",
        "rios": "Ríos",
        "davila": "Dávila",
        "gonzalez": "González",
        "perez": "Pérez",
        "garcia": "García",
        "martinez": "Martínez",
        "rodriguez": "Rodríguez",
        "andres": "Andrés",
        "maria": "María",
        "jose": "José",
        "darien": "Darién",
    }

    words = slug.split("-")
    result = []

    for word in words:
        # Check accent map first
        if word.lower() in accent_map:
            result.append(accent_map[word.lower()])
        # Keep lowercase for common Spanish prepositions
        elif word.lower() in ("de", "del", "la", "el", "los", "las", "y"):
            result.append(word.lower())
        else:
            result.append(word.capitalize())

    return " ".join(result)


def fix_malformed_links(content: str) -> tuple[str, list[str]]:
    """
    Fix malformed links with missing opening bracket.

    Pattern: "text Name](url)" where there's no "[" before Name

    Args:
        content: Markdown content with potential malformed links

    Returns:
        Tuple of (fixed_content, list_of_fixes_made)
    """
    if not content:
        return content, []

    fixes = []

    # Pattern to find malformed links: word(s)](url)
    # We look for: optional spaces + words + ](/en/... or ](http...
    # The key is that it's NOT preceded by [
    pattern = r"([A-Za-zÁÉÍÓÚáéíóúñÑ][A-Za-zÁÉÍÓÚáéíóúñÑ\s]+)\]\((/en/[^)]+|https?://[^)]+)\)"

    def replace_malformed(match):
        full_match = match.group(0)
        text_before = match.group(1)
        url = match.group(2)

        # Check if this is preceded by [ (which would make it a valid link)
        start_pos = match.start()
        if start_pos > 0:
            # Look back for [ in the preceding text
            preceding = content[max(0, start_pos - 200) : start_pos]
            # Count brackets - if we have an unclosed [, this is valid
            open_brackets = preceding.count("[") - preceding.count("]")
            if open_brackets > 0:
                # This is part of a valid (possibly nested) link, skip
                return full_match

        # Extract the expected name from the URL
        if url.startswith("/en/"):
            # Get the last part of the path (the slug)
            slug = url.rstrip("/").split("/")[-1]
            expected_name = slug_to_name(slug)
        else:
            # For full URLs, try to extract similarly
            path = url.split("/")[-1].split("?")[0]
            expected_name = slug_to_name(path)

        # Find where the name starts in text_before
        # The name should appear at the end of text_before
        text_lower = text_before.lower()
        name_lower = expected_name.lower()

        # Try to find the name in the text
        # We want to find the name and remove only the ](url) part,
        # keeping everything before and including the name
        if name_lower in text_lower:
            # Find the last occurrence
            idx = text_lower.rfind(name_lower)
            # The prefix is everything before the name
            prefix = text_before[:idx]
            # The replacement removes ](url) and keeps prefix + proper name
            fixes.append(f"'{text_before.strip()}]({url[:30]}...)' -> '{prefix}{expected_name}'")
            return prefix + expected_name
        else:
            # Can't find exact match, try partial match on last word
            name_parts = expected_name.split()
            last_name = name_parts[-1].lower() if name_parts else ""

            if last_name and last_name in text_lower:
                # Find where the last name appears and work backwards
                idx = text_lower.rfind(last_name)
                # Go back to find the start of the full name
                name_start = idx
                for part in reversed(name_parts[:-1]):
                    search_area = text_lower[:name_start]
                    part_idx = search_area.rfind(part.lower())
                    if part_idx >= 0 and name_start - part_idx < 50:
                        name_start = part_idx

                prefix = text_before[:name_start]
                fixes.append(f"'{text_before.strip()}]({url[:30]}...)' -> '{prefix}{expected_name}'")
                return prefix + expected_name

            # Last resort: just remove the ](url) part, keeping all text
            fixes.append(f"'{text_before.strip()}]({url[:30]}...)' -> '{text_before}'")
            return text_before

    fixed_content = re.sub(pattern, replace_malformed, content)
    return fixed_content, fixes


def get_historical_panama_articles(session):
    """Get all articles in Historical Panama taxonomy."""
    return (
        session.query(Article)
        .join(Category)
        .join(Taxonomy)
        .filter(Taxonomy.id == HISTORICAL_PANAMA_TAXONOMY_ID)
        .all()
    )


def preview_changes(session, limit=5):
    """Preview malformed link fixes on articles."""
    articles = get_historical_panama_articles(session)

    found_any = False
    count = 0
    for article in articles:
        if count >= limit:
            break

        original = article.content or ""
        fixed, fixes = fix_malformed_links(original)

        if fixes:
            found_any = True
            count += 1
            print(f"\n{'='*60}")
            print(f"Article {article.id}: {article.title}")
            print(f"{'='*60}")
            print(f"\nFound {len(fixes)} malformed links:")
            for fix in fixes:
                print(f"  {fix}")

    if not found_any:
        print("No malformed links found.")


def process_articles(session, dry_run=True):
    """Process all Historical Panama articles for malformed links."""
    articles = get_historical_panama_articles(session)

    print(f"Scanning {len(articles)} articles in Historical Panama taxonomy")

    modified_count = 0
    total_fixes = 0

    for article in articles:
        original = article.content or ""
        fixed, fixes = fix_malformed_links(original)

        if fixes:
            total_fixes += len(fixes)
            modified_count += 1

            if dry_run:
                print(f"  Article {article.id}: {len(fixes)} malformed links to fix")
                for fix in fixes:
                    print(f"    {fix}")
            else:
                article.content = fixed

    if not dry_run:
        session.commit()
        print(f"\nUpdated {modified_count} articles, fixed {total_fixes} malformed links")
    else:
        print(f"\n[DRY RUN] Would update {modified_count} articles, fixing {total_fixes} malformed links")

    return modified_count


def main():
    parser = argparse.ArgumentParser(
        description="Fix malformed markdown links in Historical Panama articles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/fix_malformed_links.py --preview
    python scripts/fix_malformed_links.py --dry-run
    python scripts/fix_malformed_links.py --execute
        """,
    )
    parser.add_argument(
        "--preview", action="store_true", help="Preview fixes on a few articles"
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
