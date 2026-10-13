---
name: article-to-docx
description: Convert a Markdown article into a formatted Word (.docx) document, replacing mermaid and fenced code blocks with pre-generated PNG/JPG images from the assets folder. Final step in the LinkedIn article publishing workflow.
---

# Article to DOCX

## Trigger

`/article-to-docx <markdown_file>`

## Arguments

- **markdown_file** (required): Path to a Markdown file to convert.

---

## Prerequisites

1. The `article-assets-to-png` skill **must have been run first** on the same Markdown file.
2. An assets folder must exist alongside the Markdown file, following the naming convention:
   - `docs/wander-in-dev/01-structuring-fastapi-monolith.md` → `docs/wander-in-dev/article-01-assets/`
   - For non-article files: `<filename>-assets/` in the same directory.
3. The assets folder must contain files named by the `article-assets-to-png` output convention:
   - `diagram-01.jpg`, `diagram-02.jpg`, ... (mermaid blocks — may be `.jpg` or `.png` depending on mermaid.ink response)
   - `code-01.png`, `code-02.png`, ... (fenced code blocks)

## Dependencies

- `python-docx` — install with `pip install python-docx --break-system-packages` if not already available.

---

## Procedure

### 1. Resolve the Assets Folder

Derive the assets folder path from the Markdown filename:

```python
import re
from pathlib import Path

def resolve_assets_dir(markdown_path: Path) -> Path:
    match = re.match(r"^(\d{2})-", markdown_path.name)
    if match:
        return markdown_path.parent / f"article-{match.group(1)}-assets"
    return markdown_path.parent / f"{markdown_path.stem}-assets"
```

If the folder does not exist, abort with a clear error telling the user to run `/article-assets-to-png` first.

### 2. Validate Block–Asset Parity

Before creating the document, count replaceable blocks in the Markdown and compare against the assets folder contents.

**Counting rules** (must match `article-assets-to-png` behavior):
- ```` ```mermaid ```` blocks → each increments the diagram counter
- Fenced code blocks **with a language identifier** (e.g., ```` ```python ````, ```` ```yaml ````) → each increments the code counter
- Bare ```` ``` ```` blocks (no language) → **skipped**, not counted

**Asset file discovery:**
- Diagram files: glob `diagram-*` with any image extension (`.png`, `.jpg`, `.jpeg`)
- Code files: glob `code-*.png`

```python
import re
from pathlib import Path


def count_blocks(markdown_text: str) -> tuple[int, int, int]:
    """Count mermaid blocks, code blocks, and skipped blocks."""
    blocks = re.findall(r"```(\w*)\n(.*?)```", markdown_text, re.DOTALL)
    diagrams = 0
    codes = 0
    skipped = 0
    for lang, _ in blocks:
        if lang == "mermaid":
            diagrams += 1
        elif lang == "":
            skipped += 1
        else:
            codes += 1
    return diagrams, codes, skipped


def count_assets(assets_dir: Path) -> tuple[int, int]:
    """Count diagram and code image files in the assets folder."""
    diagram_files = sorted(
        p for p in assets_dir.iterdir()
        if p.stem.startswith("diagram-") and p.suffix in (".png", ".jpg", ".jpeg")
    )
    code_files = sorted(
        p for p in assets_dir.iterdir()
        if p.stem.startswith("code-") and p.suffix == ".png"
    )
    return len(diagram_files), len(code_files)
```

If counts do not match, abort with a message like:

```
Validation failed: found 3 mermaid blocks but only 2 diagram-* files in article-01-assets/.
Run /article-assets-to-png first to regenerate assets.
```

### 3. Convert the Markdown to DOCX

Use `python-docx` to build the document. Process the Markdown file **line by line** (and block by block for fenced regions), applying these rules:

#### Element Mapping

| Markdown Element | DOCX Handling |
|------------------|---------------|
| `# Heading` | `document.add_heading(text, level=1)` |
| `## Heading` | `document.add_heading(text, level=2)` |
| `### Heading` | `document.add_heading(text, level=3)` |
| Regular paragraph | `document.add_paragraph(text)` with inline formatting |
| `**bold**` | Run with `run.bold = True` |
| `*italic*` | Run with `run.italic = True` |
| `` `inline code` `` | Run with `run.font.name = "Courier New"` and `run.font.size = Pt(10)` |
| `- item` or `* item` | `document.add_paragraph(text, style="List Bullet")` |
| `1. item` | `document.add_paragraph(text, style="List Number")` |
| `---` (horizontal rule) | Paragraph with bottom border |
| ```` ```mermaid ```` block | Replace with `diagram-NN.png/jpg`, centered, width 6" |
| ```` ```python ```` (etc.) block | Replace with `code-NN.png`, centered, width 6" |
| ```` ``` ```` (no language) | Render as plain text in Courier New (not replaced with image) |
| Markdown table | Log warning, skip |
| `![alt](url)` image | Log warning, skip |
| HTML tags | Strip tags, keep inner text |
| YAML front matter (`---` ... `---`) | Skip entirely |

#### Image Insertion

```python
from docx import Document
from docx.shared import Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH


def add_centered_image(document, image_path: str, width_inches: float = 6.0):
    """Insert an image centered with a blank line after it."""
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    run.add_picture(image_path, width=Inches(width_inches))
    document.add_paragraph()  # spacing after image
```

#### Finding the Right Asset File

Because mermaid.ink may return JPEG or PNG, diagram files can have either extension. Use a lookup function:

```python
def find_asset(assets_dir: Path, prefix: str, number: int) -> Path:
    """Find an asset file by prefix and number, tolerating .png/.jpg/.jpeg extensions."""
    stem = f"{prefix}-{number:02d}"
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = assets_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Asset not found: {stem}.* in {assets_dir}")
```

#### Horizontal Rule

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


def add_horizontal_rule(document):
    """Add a horizontal rule as a paragraph with a bottom border."""
    paragraph = document.add_paragraph()
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "999999")
    pBdr.append(bottom)
    pPr.append(pBdr)
```

#### Inline Formatting

Parse inline Markdown within a paragraph and apply formatting per-run:

```python
import re
from docx.shared import Pt


def add_formatted_runs(paragraph, text: str):
    """Parse inline markdown (bold, italic, inline code) and add as formatted runs."""
    # Pattern matches: **bold**, *italic*, `code`, or plain text
    pattern = re.compile(
        r"(\*\*(.+?)\*\*)"       # bold
        r"|(\*(.+?)\*)"          # italic
        r"|(`(.+?)`)"            # inline code
        r"|([^*`]+)"             # plain text
    )
    for m in pattern.finditer(text):
        if m.group(2):  # bold
            run = paragraph.add_run(m.group(2))
            run.bold = True
        elif m.group(4):  # italic
            run = paragraph.add_run(m.group(4))
            run.italic = True
        elif m.group(6):  # inline code
            run = paragraph.add_run(m.group(6))
            run.font.name = "Courier New"
            run.font.size = Pt(10)
        elif m.group(7):  # plain text
            paragraph.add_run(m.group(7))
```

### 4. Save the Output

Save the `.docx` in the same directory as the source Markdown, with the same base name:

- `docs/wander-in-dev/01-structuring-fastapi-monolith.md` → `docs/wander-in-dev/01-structuring-fastapi-monolith.docx`

### 5. Report Results

Print a summary:

```
Converted: docs/wander-in-dev/01-structuring-fastapi-monolith.md
Output:    docs/wander-in-dev/01-structuring-fastapi-monolith.docx
  Headings:       6
  Paragraphs:     14
  Diagrams:       2
  Code images:    5
  Warnings:       0
```

---

## Complete Working Script

```python
#!/usr/bin/env python3
"""Convert a Markdown article to a formatted Word document with embedded images."""

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


# --- Asset resolution ---


def resolve_assets_dir(markdown_path: Path) -> Path:
    match = re.match(r"^(\d{2})-", markdown_path.name)
    if match:
        return markdown_path.parent / f"article-{match.group(1)}-assets"
    return markdown_path.parent / f"{markdown_path.stem}-assets"


def find_asset(assets_dir: Path, prefix: str, number: int) -> Path:
    stem = f"{prefix}-{number:02d}"
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = assets_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Asset not found: {stem}.* in {assets_dir}")


# --- Validation ---


def count_blocks(markdown_text: str) -> tuple[int, int, int]:
    blocks = re.findall(r"```(\w*)\n(.*?)```", markdown_text, re.DOTALL)
    diagrams = codes = skipped = 0
    for lang, _ in blocks:
        if lang == "mermaid":
            diagrams += 1
        elif lang == "":
            skipped += 1
        else:
            codes += 1
    return diagrams, codes, skipped


def count_assets(assets_dir: Path) -> tuple[int, int]:
    diagram_files = [
        p for p in assets_dir.iterdir()
        if p.stem.startswith("diagram-") and p.suffix in (".png", ".jpg", ".jpeg")
    ]
    code_files = [
        p for p in assets_dir.iterdir()
        if p.stem.startswith("code-") and p.suffix == ".png"
    ]
    return len(diagram_files), len(code_files)


def validate(md_text: str, assets_dir: Path) -> bool:
    expected_diagrams, expected_codes, skipped = count_blocks(md_text)
    actual_diagrams, actual_codes = count_assets(assets_dir)

    ok = True
    if expected_diagrams != actual_diagrams:
        print(
            f"Validation failed: found {expected_diagrams} mermaid block(s) "
            f"but {actual_diagrams} diagram-* file(s) in {assets_dir.name}/."
        )
        ok = False
    if expected_codes != actual_codes:
        print(
            f"Validation failed: found {expected_codes} code block(s) "
            f"but {actual_codes} code-* file(s) in {assets_dir.name}/."
        )
        ok = False
    if ok:
        print(
            f"Validation passed: {expected_diagrams} diagram(s), "
            f"{expected_codes} code block(s), {skipped} skipped."
        )
    return ok


# --- DOCX helpers ---


def add_formatted_runs(paragraph, text: str):
    pattern = re.compile(
        r"(\*\*(.+?)\*\*)"
        r"|(\*(.+?)\*)"
        r"|(`(.+?)`)"
        r"|([^*`]+)"
    )
    for m in pattern.finditer(text):
        if m.group(2):
            run = paragraph.add_run(m.group(2))
            run.bold = True
        elif m.group(4):
            run = paragraph.add_run(m.group(4))
            run.italic = True
        elif m.group(6):
            run = paragraph.add_run(m.group(6))
            run.font.name = "Courier New"
            run.font.size = Pt(10)
        elif m.group(7):
            paragraph.add_run(m.group(7))


def add_centered_image(document, image_path: str, width_inches: float = 6.0):
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    run.add_picture(image_path, width=Inches(width_inches))
    document.add_paragraph()


def add_horizontal_rule(document):
    paragraph = document.add_paragraph()
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "999999")
    pBdr.append(bottom)
    pPr.append(pBdr)


# --- Main converter ---


def convert(md_path: Path, assets_dir: Path):
    text = md_path.read_text()
    lines = text.split("\n")
    doc = Document()

    stats = {"headings": 0, "paragraphs": 0, "diagrams": 0, "codes": 0, "warnings": 0}
    diagram_counter = 0
    code_counter = 0

    i = 0
    total = len(lines)

    # Skip YAML front matter
    if lines[i].strip() == "---":
        i += 1
        while i < total and lines[i].strip() != "---":
            i += 1
        i += 1  # skip closing ---

    while i < total:
        line = lines[i]

        # Fenced code block
        fence_match = re.match(r"^```(\w*)$", line.strip())
        if fence_match:
            lang = fence_match.group(1)
            block_lines = []
            i += 1
            while i < total and lines[i].strip() != "```":
                block_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```

            if lang == "mermaid":
                diagram_counter += 1
                asset = find_asset(assets_dir, "diagram", diagram_counter)
                add_centered_image(doc, str(asset))
                stats["diagrams"] += 1
            elif lang != "":
                code_counter += 1
                asset = find_asset(assets_dir, "code", code_counter)
                add_centered_image(doc, str(asset))
                stats["codes"] += 1
            else:
                # Unlabeled block — render as plain text in monospace
                p = doc.add_paragraph()
                run = p.add_run("\n".join(block_lines))
                run.font.name = "Courier New"
                run.font.size = Pt(9)
                stats["paragraphs"] += 1
            continue

        # Blank line
        if line.strip() == "":
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^---+$", line.strip()) or re.match(r"^\*\*\*+$", line.strip()):
            add_horizontal_rule(doc)
            i += 1
            continue

        # Headings
        heading_match = re.match(r"^(#{1,3})\s+(.*)", line)
        if heading_match:
            level = len(heading_match.group(1))
            doc.add_heading(heading_match.group(2).strip(), level=level)
            stats["headings"] += 1
            i += 1
            continue

        # Markdown image — warn and skip
        img_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", line.strip())
        if img_match:
            print(f"  Warning: skipping Markdown image: ![{img_match.group(1)}]({img_match.group(2)})")
            stats["warnings"] += 1
            i += 1
            continue

        # Table — detect by leading pipe, warn and skip
        if line.strip().startswith("|"):
            if stats.get("_in_table"):
                i += 1
                continue
            print(f"  Warning: skipping Markdown table at line {i + 1}")
            stats["warnings"] += 1
            stats["_in_table"] = True
            i += 1
            continue
        else:
            stats["_in_table"] = False

        # Bullet list
        bullet_match = re.match(r"^[\s]*[-*]\s+(.*)", line)
        if bullet_match:
            p = doc.add_paragraph(style="List Bullet")
            add_formatted_runs(p, bullet_match.group(1).strip())
            i += 1
            continue

        # Numbered list
        num_match = re.match(r"^[\s]*\d+\.\s+(.*)", line)
        if num_match:
            p = doc.add_paragraph(style="List Number")
            add_formatted_runs(p, num_match.group(1).strip())
            i += 1
            continue

        # Strip any HTML tags
        clean_line = re.sub(r"<[^>]+>", "", line)

        # Regular paragraph
        if clean_line.strip():
            p = doc.add_paragraph()
            add_formatted_runs(p, clean_line.strip())
            stats["paragraphs"] += 1

        i += 1

    # Clean up internal tracking key
    stats.pop("_in_table", None)

    # Save
    output_path = md_path.with_suffix(".docx")
    doc.save(str(output_path))
    return output_path, stats


# --- Entry point ---


def main():
    if len(sys.argv) < 2:
        print("Usage: python article_to_docx.py <markdown_file>")
        sys.exit(1)

    md_path = Path(sys.argv[1])
    if not md_path.exists():
        print(f"File not found: {md_path}")
        sys.exit(1)

    assets_dir = resolve_assets_dir(md_path)
    if not assets_dir.exists():
        print(f"Assets folder not found: {assets_dir}")
        print("Run /article-assets-to-png first to generate image assets.")
        sys.exit(1)

    text = md_path.read_text()
    if not validate(text, assets_dir):
        print("Run /article-assets-to-png to regenerate assets.")
        sys.exit(1)

    output_path, stats = convert(md_path, assets_dir)

    print(f"\nConverted: {md_path}")
    print(f"Output:    {output_path}")
    print(f"  Headings:       {stats['headings']}")
    print(f"  Paragraphs:     {stats['paragraphs']}")
    print(f"  Diagrams:       {stats['diagrams']}")
    print(f"  Code images:    {stats['codes']}")
    print(f"  Warnings:       {stats['warnings']}")


if __name__ == "__main__":
    main()
```

---

## Known Limitations

- **Tables**: Markdown tables are not converted to DOCX tables. A warning is logged and the table content is skipped entirely.
- **Nested lists**: All list items are flattened to a single level. Sub-items render at the same indentation as their parents.
- **HTML tags**: Inline HTML within Markdown (e.g., `<br>`, `<sup>`) is stripped. Only the inner text content is kept.
- **Markdown images**: `![alt](url)` syntax is not fetched or inserted. A warning is logged for each occurrence.
- **Diagram file extensions**: mermaid.ink may return JPEG or PNG data. The asset lookup function checks for `.png`, `.jpg`, and `.jpeg` extensions so it works regardless of what `article-assets-to-png` produced.
- **Front matter**: YAML front matter (the `---` delimited block at the top of the file) is skipped entirely and not rendered in the document.
- **Complex inline formatting**: Deeply nested inline formatting (e.g., bold inside italic, or inline code inside bold) may not render correctly. Each formatting type is matched independently.