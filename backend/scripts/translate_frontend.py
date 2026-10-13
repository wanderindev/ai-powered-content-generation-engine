#!/usr/bin/env python3
"""
Translate missing keys in frontend translation.json files.

Compares EN and ES translation files, translates any keys that
exist in EN but not in ES.

Usage:
    docker-compose exec backend python scripts/translate_frontend.py --preview
    docker-compose exec backend python scripts/translate_frontend.py --execute

The script reads from:
    /app/../frontend/public/locales/en/translation.json
And updates:
    /app/../frontend/public/locales/es/translation.json
"""
import argparse
import json
import sys
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import deepl

from app.core.config import get_settings

# Paths to translation files (relative to backend container)
EN_PATH = Path(__file__).parent.parent.parent / "frontend/public/locales/en/translation.json"
ES_PATH = Path(__file__).parent.parent.parent / "frontend/public/locales/es/translation.json"


def load_json(path: Path) -> dict:
    """Load JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    """Save JSON file with proper formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
        f.write("\n")  # Trailing newline


def find_missing_keys(
    en_dict: dict,
    es_dict: dict,
    prefix: str = "",
) -> list[tuple[str, str]]:
    """
    Recursively find keys in EN that are missing or empty in ES.

    Returns list of (dot-notation key path, english value) tuples.
    """
    missing = []

    for key, value in en_dict.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, dict):
            # Recurse into nested objects
            es_value = es_dict.get(key, {})
            if not isinstance(es_value, dict):
                es_value = {}
            missing.extend(find_missing_keys(value, es_value, full_key))
        else:
            # Check if key is missing or empty in ES
            es_value = es_dict.get(key)
            if es_value is None or (isinstance(es_value, str) and not es_value.strip()):
                missing.append((full_key, value))

    return missing


def set_nested_value(d: dict, key_path: str, value: str) -> None:
    """Set a value in a nested dict using dot notation."""
    keys = key_path.split(".")
    current = d

    # Navigate to the parent of the final key
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]

    # Set the final value
    current[keys[-1]] = value


def translate_keys(
    missing_keys: list[tuple[str, str]],
    translator: deepl.Translator,
) -> list[tuple[str, str, str]]:
    """
    Translate missing keys using DeepL.

    Returns list of (key_path, original, translated) tuples.
    """
    results = []

    for key_path, en_value in missing_keys:
        if not en_value or not isinstance(en_value, str):
            continue

        try:
            result = translator.translate_text(
                en_value,
                target_lang="ES-419",  # Latin American Spanish
                formality="default",
            )
            results.append((key_path, en_value, result.text))
        except Exception as e:
            print(f"  Error translating '{key_path}': {e}")

    return results


def preview_changes(en_data: dict, es_data: dict) -> None:
    """Show what keys would be translated."""
    missing = find_missing_keys(en_data, es_data)

    if not missing:
        print("No missing keys found. ES translation is complete.")
        return

    print(f"Found {len(missing)} keys to translate:\n")

    for key_path, en_value in missing[:20]:  # Show first 20
        # Truncate long values for display
        display_value = en_value[:60] + "..." if len(en_value) > 60 else en_value
        print(f"  {key_path}")
        print(f"    EN: {display_value}")
        print()

    if len(missing) > 20:
        print(f"  ... and {len(missing) - 20} more keys")


def execute_translation(en_data: dict, es_data: dict, dry_run: bool = False) -> None:
    """Translate and update ES translation file."""
    settings = get_settings()

    if not settings.deepl_api:
        print("ERROR: DEEPL_API environment variable not set.")
        sys.exit(1)

    translator = deepl.Translator(settings.deepl_api)

    missing = find_missing_keys(en_data, es_data)

    if not missing:
        print("No missing keys found. ES translation is complete.")
        return

    print(f"Translating {len(missing)} keys...")

    translated = translate_keys(missing, translator)

    print(f"\nSuccessfully translated {len(translated)} keys.")

    if dry_run:
        print("\n[DRY RUN] Would update the following:")
        for key_path, en_value, es_value in translated[:10]:
            print(f"\n  {key_path}")
            print(f"    EN: {en_value[:50]}...")
            print(f"    ES: {es_value[:50]}...")
        if len(translated) > 10:
            print(f"\n  ... and {len(translated) - 10} more")
        return

    # Update ES data with translations
    for key_path, _, es_value in translated:
        set_nested_value(es_data, key_path, es_value)

    # Save updated ES file
    save_json(ES_PATH, es_data)
    print(f"\nUpdated {ES_PATH}")


def main():
    parser = argparse.ArgumentParser(
        description="Translate missing frontend translation keys",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/translate_frontend.py --preview
    python scripts/translate_frontend.py --dry-run
    python scripts/translate_frontend.py --execute
        """,
    )
    parser.add_argument(
        "--preview", action="store_true", help="Show missing keys without translating"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Translate but don't save changes"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Translate and save changes"
    )
    args = parser.parse_args()

    if not any([args.preview, args.dry_run, args.execute]):
        parser.print_help()
        return

    # Check file paths
    if not EN_PATH.exists():
        print(f"ERROR: English translation file not found: {EN_PATH}")
        sys.exit(1)

    if not ES_PATH.exists():
        print(f"ERROR: Spanish translation file not found: {ES_PATH}")
        sys.exit(1)

    en_data = load_json(EN_PATH)
    es_data = load_json(ES_PATH)

    print(f"EN file: {EN_PATH}")
    print(f"ES file: {ES_PATH}")
    print()

    if args.preview:
        preview_changes(en_data, es_data)
    elif args.dry_run:
        execute_translation(en_data, es_data, dry_run=True)
    elif args.execute:
        print("WARNING: This will modify the Spanish translation file.")
        confirm = input("Type 'yes' to confirm: ")
        if confirm.lower() == "yes":
            execute_translation(en_data, es_data, dry_run=False)
        else:
            print("Aborted.")


if __name__ == "__main__":
    main()
