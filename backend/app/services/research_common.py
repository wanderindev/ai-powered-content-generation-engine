"""Shared research-document validation logic for blog and edu pipelines."""

import json
import re

from fastapi import HTTPException, UploadFile

from app.services.llm import HAIKU_MODEL, create_message, first_text

# Upload size caps (#89). Research documents are markdown text targeting
# 5000-8000 words (< ~1 MB); 5 MB is a generous ceiling. Material uploads are
# binaries — a podcast .m4a can legitimately be tens of MB.
MAX_RESEARCH_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_MATERIAL_UPLOAD_BYTES = 75 * 1024 * 1024


async def read_upload_text(file: UploadFile, max_bytes: int = MAX_RESEARCH_UPLOAD_BYTES) -> str:
    """Read an async UploadFile as UTF-8 text, enforcing a size cap.

    Reads at most ``max_bytes + 1`` so an oversized upload is rejected without
    fully buffering it. Raises HTTP 413 on oversize and HTTP 400 on non-UTF-8.
    """
    raw = await file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (limit {max_bytes // (1024 * 1024)} MB).",
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as err:
        raise HTTPException(
            status_code=400,
            detail="File must be valid UTF-8 text (e.g. a .md or .txt document).",
        ) from err


def read_upload_bytes(file: UploadFile, max_bytes: int = MAX_MATERIAL_UPLOAD_BYTES) -> bytes:
    """Read a sync UploadFile's bytes, enforcing a size cap.

    Reads at most ``max_bytes + 1`` so an oversized upload is rejected without
    fully buffering it. Raises HTTP 413 on oversize and HTTP 400 on empty file.
    """
    raw = file.file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (limit {max_bytes // (1024 * 1024)} MB).",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    return raw


def validate_research_document(
    content: str,
    sub_topics: list[str] | None,
) -> dict:
    """Validate a research document: regex for word count and references,
    Haiku for sub-topic coverage. Auto-fixes missing references.

    Returns a dict with: valid, word_count, content (possibly fixed),
    and details about each check.
    """
    sub_topics = sub_topics or []

    word_count = len(content.split())

    if word_count < 4000:
        return {
            "valid": False,
            "word_count": word_count,
            "content": content,
            "checks": {
                "word_count": False,
                "references": True,
                "subtopic_coverage": True,
            },
        }

    # References check — deterministic regex (scans entire document)
    refs_pattern = re.compile(
        r"^#{1,3}\s*(Sources|References|Bibliography|Works Cited|Citations)",
        re.IGNORECASE | re.MULTILINE,
    )
    refs_ok = bool(refs_pattern.search(content))

    # Sub-topic coverage — use Haiku for semantic matching
    sub_topics_summary = "\n".join(
        f"- {t.split(':')[0].strip() if ':' in t else t}" for t in sub_topics
    )

    if not sub_topics:
        # No sub-topics defined — skip the check
        subtopics_found = 0
        subtopics_total = 0
        subtopic_ok = True
    else:
        message = create_message(
            feature="research_validate_subtopics",
            model=HAIKU_MODEL,
            max_tokens=2000,  # was 256 before thinking (#610)
            output_config={"effort": "medium"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are a quality checker for research documents. "
                        "How many of the following expected sub-topics are "
                        "substantively addressed in this document?\n\n"
                        "EXPECTED SUB-TOPICS:\n" + sub_topics_summary + "\n\n"
                        "Respond with ONLY valid JSON, no other text:\n"
                        '{"subtopics_found": <number>, '
                        '"subtopics_total": <number>}\n\n'
                        "DOCUMENT (first 15000 chars):\n" + content[:15000]
                    ),
                }
            ],
        )

        raw_response = first_text(message).strip()
        # Strip markdown code fences if present
        if raw_response.startswith("```"):
            raw_response = re.sub(r"^```\w*\n?", "", raw_response)
            raw_response = re.sub(r"\n?```$", "", raw_response).strip()

        try:
            result = json.loads(raw_response)
        except (json.JSONDecodeError, IndexError):
            result = {}

        subtopics_found = result.get("subtopics_found", 0)
        subtopics_total = result.get("subtopics_total", 0)
        subtopic_ok = subtopics_found >= 2

    final_content = content

    # Auto-fix: generate a references section if citations exist but
    # no dedicated section was found
    if not refs_ok:
        fixed = _generate_references_section(content)
        if fixed:
            final_content = fixed
            refs_ok = True

    return {
        "valid": refs_ok and subtopic_ok,
        "word_count": word_count,
        "content": final_content,
        "checks": {
            "word_count": True,
            "references": refs_ok,
            "subtopic_coverage": {
                "passed": subtopic_ok,
                "found": subtopics_found,
                "total": subtopics_total,
            },
        },
    }


def _generate_references_section(content: str) -> str | None:
    """Ask Haiku to compile a References section from inline citations."""
    message = create_message(
        feature="research_references",
        model=HAIKU_MODEL,
        # Thinking headroom at medium effort (#610); 8000 also streams.
        max_tokens=8000,
        output_config={"effort": "medium"},
        messages=[
            {
                "role": "user",
                "content": (
                    "The following research document has inline citations but no "
                    "dedicated References section. Extract ALL cited works from the "
                    "document and produce a properly formatted markdown References "
                    "section.\n\n"
                    "Rules:\n"
                    "- Output ONLY the references section, starting with '## References'\n"
                    "- List every unique source cited in the document\n"
                    "- Use a consistent citation format (author, year, title, publisher)\n"
                    "- Do NOT add sources that aren't cited in the document\n"
                    "- Do NOT output anything before '## References'\n\n"
                    "DOCUMENT:\n" + content
                ),
            }
        ],
    )

    refs_text = first_text(message).strip()
    if refs_text.startswith("## References") or refs_text.startswith("## Sources"):
        return content.rstrip() + "\n\n---\n\n" + refs_text
    return None
