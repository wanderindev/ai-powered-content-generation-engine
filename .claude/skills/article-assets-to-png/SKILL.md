---
name: article-assets-to-png
description: Convert Mermaid diagrams and fenced code blocks in Markdown files into PNG images. Uses mermaid.ink for diagrams and Carbonara (Carbon API) for code screenshots. Produces a complete asset folder ready for LinkedIn or other platforms.
---

# Article Assets to PNG

## Trigger

`/article-assets-to-png <markdown_file>` or `/article-assets-to-png <markdown_file> <output_dir>`

Aliases: `/mermaid-to-png` (legacy, still works — processes only mermaid blocks)

## Arguments

- **markdown_file** (required): Path to a Markdown file containing fenced code blocks.
- **output_dir** (optional): Directory to write PNG files into. Defaults to a folder named after the article (see Article Series Workflow below).
- **--include-unlabeled** (optional flag): Also export fenced code blocks with no language identifier. By default these are skipped (they're usually inline output or config snippets not worth screenshotting).

---

## Mermaid.ink API (Diagrams)

The [mermaid.ink](https://mermaid.ink) service renders Mermaid diagrams to images via a simple GET endpoint:

```
https://mermaid.ink/img/<base64-encoded-diagram>
```

- The Mermaid code must be base64-encoded using **URL-safe** encoding (`base64.urlsafe_b64encode` in Python).
- No authentication required.
- Useful query parameters:
  - `?type=png` — explicit (PNG is already the default for `/img/`)
  - `?bgColor=white` — sets a white background instead of transparent, which is important for LinkedIn and other platforms that render transparent PNGs with a dark or checkered background.
- **Important**: Despite the `/img/` path, mermaid.ink may return JPEG data. Always check the response `Content-Type` header and use the correct file extension (`.jpg` or `.png`) to avoid format mismatches that break image viewers.

---

## Carbonara API (Code Screenshots)

[Carbonara](https://carbonara.solopov.dev) is a public API wrapper around [Carbon](https://carbon.now.sh) that renders code snippets into styled PNG screenshots:

```
POST https://carbonara.solopov.dev/api/cook
```

### Request Body (JSON)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | string | yes | The source code to render |
| `language` | string | yes | Language identifier for syntax highlighting |
| `theme` | string | no | Color theme name (default: `"seti"`) |
| `backgroundColor` | string | no | Background color as hex (e.g., `"#282C34"`) |
| `windowControls` | boolean | no | Show macOS-style traffic lights (default: `true`) |
| `paddingVertical` | string | no | Vertical padding (e.g., `"24px"`) |
| `paddingHorizontal` | string | no | Horizontal padding (e.g., `"24px"`) |

### Response

- Returns **binary PNG** directly (Content-Type: `image/png`).
- No authentication required.

### Configuration

- **Recommended theme**: `"one-dark"` — use this consistently across all articles for a uniform look.
- **Recommended settings**: `windowControls: false`, `backgroundColor: "#E0E0E0"` — clean look without macOS chrome, with a light grey frame around the dark code card for good contrast on LinkedIn's white feed.
- Carbon supports most common languages. If an unrecognized language identifier is encountered, **fall back to `"text"` (plaintext)** rather than failing.

### Language Mapping

Some Markdown language identifiers need mapping to Carbon-compatible values:

| Markdown identifier | Carbon language |
|---------------------|----------------|
| `python` | `python` |
| `py` | `python` |
| `javascript` | `javascript` |
| `js` | `javascript` |
| `typescript` | `typescript` |
| `ts` | `typescript` |
| `jsx` | `javascript` |
| `tsx` | `typescript` |
| `bash` | `application/x-sh` |
| `sh` | `application/x-sh` |
| `shell` | `application/x-sh` |
| `json` | `application/json` |
| `sql` | `text/x-sql` |
| `yaml` | `text/x-yaml` |
| `yml` | `text/x-yaml` |
| `html` | `htmlmixed` |
| `css` | `css` |
| *(unrecognized)* | `text` |

---

## Procedure

### 1. Parse All Fenced Code Blocks from the Markdown File

Read the file and extract all fenced code blocks in document order. Each block is captured with its language identifier (if any) and its content.

The regex pattern captures both the language and body:
```
```(\w*)\n(.*?)```
```

Classify each block:
- **Language is `mermaid`** → route to mermaid.ink
- **Language is a recognized code language** (python, js, bash, etc.) → route to Carbonara API
- **No language identifier** (bare ` ``` `) → skip by default, include only if `--include-unlabeled` flag is set

### 2. Determine the Output Directory

If an output directory is provided, use it.

If not, derive it from the filename following the article series convention:
- `docs/wander-in-dev/01-structuring-fastapi-monolith.md` → `docs/wander-in-dev/article-01-assets/`
- `docs/wander-in-dev/02-sqlalchemy-bilingual-content-model.md` → `docs/wander-in-dev/article-02-assets/`

For non-article files, use `<filename>-assets/` in the same directory as the source file.

Create the output directory if it doesn't exist.

### 3. Export Each Block as a PNG

Process blocks in document order, maintaining separate counters for diagrams and code blocks.

**Naming convention:**
- Mermaid diagrams: `diagram-01.jpg` or `diagram-01.png` (extension detected from mermaid.ink response Content-Type)
- Code blocks: `code-01.png`, `code-02.png`, ...

Add a **0.5-second delay** between all API calls (both mermaid.ink and Carbonara).

### 4. Report Results

Display:
- The number of diagrams exported
- The number of code blocks exported
- The number of blocks skipped (unlabeled)
- The output directory path
- The list of all created PNG files in order

---

## Python Implementation

### Mermaid Renderer

```python
import base64
import time
from pathlib import Path

import requests


def mermaid_to_image(mermaid_code: str, output_path_stem: str) -> str:
    """Render a Mermaid diagram via mermaid.ink API.

    Detects actual content type from response and saves with
    the correct extension (.png or .jpg).

    Returns the actual output path used (with correct extension).
    """
    encoded = base64.urlsafe_b64encode(mermaid_code.encode("utf-8")).decode("utf-8")
    url = f"https://mermaid.ink/img/{encoded}?bgColor=white"

    for attempt in range(2):
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")
            if "jpeg" in content_type or "jpg" in content_type:
                ext = ".jpg"
            else:
                ext = ".png"
            actual_path = output_path_stem + ext
            Path(actual_path).write_bytes(resp.content)
            return actual_path
        if attempt == 0:
            time.sleep(1)

    raise RuntimeError(
        f"mermaid.ink returned HTTP {resp.status_code} for {output_path_stem}"
    )
```

### Code Screenshot Renderer

```python
CARBONARA_URL = "https://carbonara.solopov.dev/api/cook"

LANGUAGE_MAP = {
    "python": "python", "py": "python",
    "javascript": "javascript", "js": "javascript", "jsx": "javascript",
    "typescript": "typescript", "ts": "typescript", "tsx": "typescript",
    "bash": "application/x-sh", "sh": "application/x-sh", "shell": "application/x-sh",
    "json": "application/json", "sql": "text/x-sql",
    "yaml": "text/x-yaml", "yml": "text/x-yaml",
    "html": "htmlmixed", "css": "css",
}


def code_to_png(code: str, language: str, output_path: str) -> None:
    """Render a code snippet to PNG via the Carbonara API."""
    carbon_lang = LANGUAGE_MAP.get(language, "text")
    payload = {
        "code": code,
        "language": carbon_lang,
        "theme": "one-dark",
        "backgroundColor": "#E0E0E0",
        "windowControls": False,
        "paddingVertical": "24px",
        "paddingHorizontal": "24px",
    }

    for attempt in range(2):
        resp = requests.post(CARBONARA_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            Path(output_path).write_bytes(resp.content)
            return
        if attempt == 0:
            time.sleep(1)

    raise RuntimeError(
        f"Carbonara API returned HTTP {resp.status_code} for {output_path}"
    )
```

### Block Extraction and Routing

```python
import re


def extract_fenced_blocks(markdown_text: str) -> list[tuple[str, str]]:
    """Extract all fenced code blocks as (language, content) tuples.

    Returns blocks in document order. Language is empty string for
    unlabeled blocks (bare ```).
    """
    return re.findall(r"```(\w*)\n(.*?)```", markdown_text, re.DOTALL)
```

---

## Complete Batch Script

A full working script that processes an article file, exporting both diagrams and code screenshots:

```python
#!/usr/bin/env python3
"""Export Mermaid diagrams and code blocks from a Markdown article to PNG files."""

import base64
import re
import sys
import time
from pathlib import Path

import requests

# --- Configuration ---

CARBONARA_URL = "https://carbonara.solopov.dev/api/cook"

LANGUAGE_MAP = {
    "python": "python", "py": "python",
    "javascript": "javascript", "js": "javascript", "jsx": "javascript",
    "typescript": "typescript", "ts": "typescript", "tsx": "typescript",
    "bash": "application/x-sh", "sh": "application/x-sh", "shell": "application/x-sh",
    "json": "application/json", "sql": "text/x-sql",
    "yaml": "text/x-yaml", "yml": "text/x-yaml",
    "html": "htmlmixed", "css": "css",
}

# --- Renderers ---


def mermaid_to_image(mermaid_code: str, output_path_stem: str) -> str:
    """Returns actual output path with correct extension based on response Content-Type."""
    encoded = base64.urlsafe_b64encode(mermaid_code.encode("utf-8")).decode("utf-8")
    url = f"https://mermaid.ink/img/{encoded}?bgColor=white"

    for attempt in range(2):
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")
            ext = ".jpg" if ("jpeg" in content_type or "jpg" in content_type) else ".png"
            actual_path = output_path_stem + ext
            Path(actual_path).write_bytes(resp.content)
            return actual_path
        if attempt == 0:
            time.sleep(1)

    raise RuntimeError(f"mermaid.ink returned HTTP {resp.status_code} for {output_path_stem}")


def code_to_png(code: str, language: str, output_path: str) -> None:
    carbon_lang = LANGUAGE_MAP.get(language, "text")
    payload = {
        "code": code,
        "language": carbon_lang,
        "theme": "one-dark",
        "backgroundColor": "#E0E0E0",
        "windowControls": False,
        "paddingVertical": "24px",
        "paddingHorizontal": "24px",
    }

    for attempt in range(2):
        resp = requests.post(CARBONARA_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            Path(output_path).write_bytes(resp.content)
            return
        if attempt == 0:
            time.sleep(1)

    raise RuntimeError(f"Carbonara API returned HTTP {resp.status_code} for {output_path}")


# --- Extraction and routing ---


def extract_fenced_blocks(markdown_text: str) -> list[tuple[str, str]]:
    """Extract all fenced code blocks as (language, content) tuples."""
    return re.findall(r"```(\w*)\n(.*?)```", markdown_text, re.DOTALL)


def resolve_output_dir(markdown_path: Path) -> Path:
    match = re.match(r"^(\d{2})-", markdown_path.name)
    if match:
        return markdown_path.parent / f"article-{match.group(1)}-assets"
    return markdown_path.parent / f"{markdown_path.stem}-assets"


def main():
    if len(sys.argv) < 2:
        print("Usage: python article_assets.py <markdown_file> [output_dir] [--include-unlabeled]")
        sys.exit(1)

    include_unlabeled = "--include-unlabeled" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    md_path = Path(args[0])
    if not md_path.exists():
        print(f"File not found: {md_path}")
        sys.exit(1)

    out_dir = Path(args[1]) if len(args) >= 2 else resolve_output_dir(md_path)

    text = md_path.read_text()
    blocks = extract_fenced_blocks(text)

    if not blocks:
        print(f"No fenced code blocks found in {md_path}")
        sys.exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)

    diagram_count = 0
    code_count = 0
    skipped_count = 0
    created = []
    is_first_api_call = True

    for lang, content in blocks:
        content = content.strip()

        if lang == "mermaid":
            diagram_count += 1
            path_stem = str(out_dir / f"diagram-{diagram_count:02d}")
            label = f"diagram-{diagram_count:02d} (mermaid)"
        elif lang == "" and not include_unlabeled:
            skipped_count += 1
            continue
        else:
            code_count += 1
            png_path = out_dir / f"code-{code_count:02d}.png"
            display_lang = lang if lang else "plaintext"
            label = f"code-{code_count:02d}.png ({display_lang})"

        if not is_first_api_call:
            time.sleep(0.5)
        is_first_api_call = False

        print(f"  Exporting {label}...", end=" ", flush=True)
        if lang == "mermaid":
            actual_path = mermaid_to_image(content, path_stem)
            print(f"→ {Path(actual_path).name}")
            created.append(actual_path)
        else:
            code_to_png(content, lang or "plaintext", str(png_path))
            print(f"→ {png_path.name}")
            created.append(str(png_path))

    print(f"\nDone. Output: {out_dir}/")
    print(f"  Diagrams: {diagram_count}")
    print(f"  Code blocks: {code_count}")
    if skipped_count:
        print(f"  Skipped (unlabeled): {skipped_count}")
    print(f"  Total PNGs: {len(created)}")


if __name__ == "__main__":
    main()
```

---

## Article Series Workflow

For the Panama In Context "Wander in Dev" article series, running this script on an article Markdown file produces a complete asset folder with both diagram PNGs and code screenshot PNGs, ready to upload to LinkedIn in order:

```
docs/wander-in-dev/
  01-structuring-fastapi-monolith.md
  article-01-assets/
    diagram-01.jpg          ← mermaid.ink (extension auto-detected)
    diagram-02.jpg          ← mermaid.ink (extension auto-detected)
    code-01.png             ← Carbonara (python)
    code-02.png             ← Carbonara (python)
    code-03.png             ← Carbonara (python)
  02-sqlalchemy-bilingual-content-model.md
  article-02-assets/
    diagram-01.png
    code-01.png
    ...
```

To derive the folder name from the article filename, extract the leading number:
- `01-structuring-fastapi-monolith.md` → `article-01-assets/`
- `10-orchestrating-llms-retrospective.md` → `article-10-assets/`

For non-article files, use `<filename>-assets/` in the same directory as the source file.

---

## Known Limitations

### mermaid.ink
- **Rate limiting**: mermaid.ink is a free public service. Always add a 0.5s delay between requests when batch processing. Don't run parallel requests.
- **Timeouts on complex diagrams**: Diagrams with many nodes can occasionally time out. The script retries once automatically. If both attempts fail, check the diagram syntax or simplify it.
- **Intermittent availability**: The service may go down occasionally. If it's unavailable, the local fallback is `@mermaid-js/mermaid-cli`:
  ```bash
  npx -p @mermaid-js/mermaid-cli mmdc -i diagram.mmd -o diagram.png -b white
  ```
- **Image size**: The API returns diagrams at a fixed resolution. For higher-resolution exports, use `?scale=2` (or `scale=3`) as a query parameter if the API supports it, or use the CLI fallback with `-s 2`.

### Carbonara API
- **Language support**: Carbon supports most common languages via CodeMirror modes. If the Markdown code fence uses an unrecognized language identifier, the script falls back to `"text"` (plaintext) for syntax highlighting rather than failing. The `LANGUAGE_MAP` dict handles common aliases (e.g., `py` → `python`, `sh` → `application/x-sh`, `jsx` → `javascript`).
- **Rate limiting**: Same as mermaid.ink — 0.5s delay between requests. Carbonara is a free public API wrapper; don't hammer it.
- **Theme consistency**: Always use `"one-dark"` with `backgroundColor: "#E0E0E0"` and `windowControls: false` for a uniform look across all articles in the series. Mixing themes makes the asset folder look inconsistent when uploaded to LinkedIn.
- **Long code blocks**: Very long code snippets produce very tall images. Keep code blocks to ~30 lines max in the source Markdown (the write-article skill already enforces this). If a block exceeds 40 lines, consider whether it really needs a screenshot.
- **Carbonara availability**: Carbonara (`carbonara.solopov.dev`) is a third-party hosted wrapper. If it goes down, the fallback is to use [carbon.now.sh](https://carbon.now.sh) manually or install the `carbon-now-cli` npm package locally.