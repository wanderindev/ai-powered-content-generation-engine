"""Educational research service — prompt generation and document validation.

The research-prompt builder is **mode-aware** on two independent axes so it
serves both excursion-tied materials and future standalone educational content:

- **Excursion vs. standalone** — keyed on ``suggestion.excursion_key``. With an
  excursion we keep the pre-visit / on-site / guided-tour framing; without one we
  drop all site-visit language and frame the research around the topic itself.
- **Grade-banded vs. single-level** — keyed on ``suggestion.grade_bands``. With
  bands we target them explicitly; with none (the "no grade adaptation" case) we
  target one informed general / educator level.
"""

from app.models.edu_suggestion import EduSuggestion
from app.services import research_common
from app.services.llm import SONNET_MODEL, create_message, first_text
from app.services.research import extract_suggestion_title

# Re-export so callers can import from one place
__all__ = [
    "build_edu_research_prompt",
    "generate_edu_research_prompt",
    "validate_edu_research_document",
    "extract_suggestion_title",
]


def build_edu_research_prompt(suggestion: EduSuggestion) -> str:
    """Build the mode-aware research-prompt template (pure — no AI call).

    The two modes are inferred from the suggestion: an ``excursion_key`` toggles
    the site-visit framing, and a non-empty ``grade_bands`` toggles grade-band
    targeting. The Sonnet tailoring pass lives in ``generate_edu_research_prompt``.
    """
    has_excursion = bool(suggestion.excursion_key)
    has_bands = bool(suggestion.grade_bands)

    sub_topics_list = "\n".join(f"- {t}" for t in (suggestion.sub_topics or []))
    grade_bands_str = ", ".join(suggestion.grade_bands or [])

    # CONTEXT — site-visit framing only when there is an excursion.
    if has_excursion:
        context = (
            "This is research for an academic excursion program. The resulting "
            "materials will be used to create pre-visit study guides, guided-tour "
            "talking points, and post-visit assessments for students visiting "
            "historical and cultural sites in Panama City."
        )
    else:
        context = (
            "This is research for an educational content program about Panama. The "
            "resulting material will be used to create a study guide and related "
            "teaching materials on this topic. It is NOT tied to a site visit, so "
            "focus on the subject itself rather than on what students would observe "
            "on location."
        )

    # RESEARCH FOCUS — grade bands only when defined.
    focus_lines = [
        f"Title: {suggestion.title}",
        "",
        suggestion.description or "",
        "",
        "Key areas to investigate:",
        sub_topics_list,
    ]
    if has_bands:
        focus_lines += ["", f"Target grade bands: {grade_bands_str}"]
    research_focus = "\n".join(focus_lines)

    # REQUIREMENTS — a common backbone plus mode-specific lines.
    requirements = [
        "- Prioritize primary and scholarly secondary sources (academic journals,",
        "  books by recognized historians or subject experts, official documentation)",
        "- Distinguish between well-documented facts and areas of genuine debate",
        "- Include specific dates, names, and figures where available",
        "- Include 3-5 worthwhile primary-source quotations with attribution",
        "- Provide a list of 10-15 key vocabulary terms with brief definitions",
        "- Address every key area above in its own clearly-headed section",
        "- Provide full source citations so claims can be verified",
        '- Include a formal "## References" or "## Sources" section at the end',
        "  listing all works cited (this is required for automated processing)",
        "- Target approximately 5000-8000 words of substantive, narrative prose",
    ]
    if has_bands:
        requirements.append(
            "- Write at a level accessible to educators preparing materials for the "
            f"specified grade bands ({grade_bands_str}) — depth and accuracy they can "
            "adapt per band, not pre-simplified text"
        )
    else:
        requirements.append(
            "- Write at an informed general / educator level — depth and accuracy an "
            "editor can adapt, not pre-simplified text"
        )
    if has_excursion:
        requirements.append(
            "- Include practical detail about the site(s) where relevant (what students "
            "can observe, notable artifacts, physical layout) and tie facts to "
            "observable evidence at the site"
        )

    return f"""REF: edu_suggestion_{suggestion.id}

Research the topic described below for an educational platform that provides
study materials for schools in Panama.

CONTEXT:
{context}

RESEARCH FOCUS:
{research_focus}

REQUIREMENTS:
{chr(10).join(requirements)}"""


def generate_edu_research_prompt(suggestion: EduSuggestion) -> str:
    """Use Sonnet to tailor the mode-aware research prompt for a suggestion."""
    template = build_edu_research_prompt(suggestion)

    subject = "site" if suggestion.excursion_key else "topic"
    instructions = [
        f"Below is a research prompt template for educational materials about a "
        f"Panama {subject}. Tailor it to be more effective:",
        "- Adjust source guidance to match the subject (colonial history, natural "
        "science, religion, engineering, etc.)",
        "- Refine investigation areas based on how specific the sub-topics are",
        "- Keep the REF line, CONTEXT, RESEARCH FOCUS, and REQUIREMENTS structure",
        "- Keep it as a prompt (instructions for a researcher), not an article",
        "- Ensure the educational focus is maintained — this research will be "
        "adapted for student learning",
    ]
    if not suggestion.excursion_key:
        instructions.append(
            "- This topic is NOT a site visit — do not add pre-visit, on-site, or "
            "guided-tour framing"
        )

    message = create_message(
        feature="edu_research_prompt",
        model=SONNET_MODEL,
        max_tokens=14_000,
        output_config={"effort": "high"},
        messages=[
            {
                "role": "user",
                "content": "\n".join(instructions) + f"\n\nTEMPLATE:\n{template}",
            }
        ],
    )

    return first_text(message)


def validate_edu_research_document(content: str, suggestion: EduSuggestion) -> dict:
    return research_common.validate_research_document(content, suggestion.sub_topics or [])
