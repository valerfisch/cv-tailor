"""Generate tailored CV content and cover letter using Claude API."""

import json
from pathlib import Path

import anthropic
import yaml

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
MASTER_CV_PATH = Path(__file__).parent.parent / "master_cv.yaml"
REWRITE_DIR = Path(__file__).parent.parent / "cvs"  # where CV - * - REWRITE.md files live

_REWRITE_FILES = {
    "ml": "CV - Machine Learning Engineer - REWRITE.md",
    "fullstack": "CV - Senior Full-Stack Developer - REWRITE.md",
}


def _load_rewrite_baseline(profile_type: str) -> str | None:
    """Load the curated rewrite CV for the given profile type."""
    filename = _REWRITE_FILES.get(profile_type)
    if not filename:
        # For hybrid, include both
        parts = []
        for key, fname in _REWRITE_FILES.items():
            path = REWRITE_DIR / fname
            if path.exists():
                parts.append(f"### {key.upper()} baseline\n{path.read_text()}")
        return "\n\n".join(parts) if parts else None

    path = REWRITE_DIR / filename
    if path.exists():
        return path.read_text()
    return None


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text()


def _load_master_cv() -> dict:
    return yaml.safe_load(MASTER_CV_PATH.read_text())


def _call_claude(system: str, user_content: str, model: str = "claude-sonnet-4-20250514") -> dict:
    """Call Claude and parse JSON response."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


def generate_tailored_cv(analysis: dict, user_prefs: dict) -> dict:
    """Generate tailored CV content based on posting analysis and user preferences."""
    master_cv = _load_master_cv()
    system_prompt = _load_prompt("tailor")

    user_content = (
        f"## Job Posting Analysis\n```json\n{json.dumps(analysis, indent=2)}\n```\n\n"
        f"## Master CV Data\n```yaml\n{yaml.dump(master_cv, default_flow_style=False)}\n```\n\n"
        f"## User Preferences\n```json\n{json.dumps(user_prefs, indent=2)}\n```"
    )

    # Include curated rewrite CV as a baseline reference
    baseline = _load_rewrite_baseline(user_prefs.get("profile_type", "hybrid"))
    if baseline:
        user_content += (
            f"\n\n## Curated Baseline CV\n"
            f"The following is a manually curated CV for this profile type. "
            f"Use it as a baseline — preserve its structure, bullet points, and phrasing "
            f"as much as possible. Only adapt or swap content where the job posting "
            f"clearly requires different emphasis.\n\n"
            f"```markdown\n{baseline}\n```"
        )

    return _call_claude(system_prompt, user_content)


def score_content_relevance(
    content: dict[str, list[dict]],
    analysis: dict,
) -> dict[str, list[dict]]:
    """Score multiple content categories by relevance to a role using Claude Haiku.

    *content* maps category names (e.g. ``"soft_skills"``, ``"experience"``,
    ``"highlight_blocks"``) to lists of dicts that each contain at least a
    ``"text"`` field.

    Returns the same structure with an added ``relevance`` field (1-10) on
    every item, each list sorted highest-first.
    """
    # Filter out empty categories
    content = {k: v for k, v in content.items() if v}
    if not content:
        return {}

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=(
            "You score CV/cover-letter content by relevance to a job posting. "
            "You receive named categories of items and a job posting analysis. "
            "Return a JSON object where each key matches an input category and "
            "its value is an array of {\"index\": <0-based>, \"relevance\": <1-10>}. "
            "10 = critical for this role, 1 = irrelevant. "
            "Return ONLY valid JSON, no other text."
        ),
        messages=[{"role": "user", "content": (
            f"## Content to Score\n```json\n{json.dumps(content, indent=2)}\n```\n\n"
            f"## Job Posting Analysis\n```json\n{json.dumps(analysis, indent=2)}\n```"
        )}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    scores_by_category = json.loads(raw)

    result = {}
    for category, items in content.items():
        score_list = scores_by_category.get(category, [])
        score_map = {s["index"]: s["relevance"] for s in score_list}
        scored = [{**item, "relevance": score_map.get(i, 5)} for i, item in enumerate(items)]
        scored.sort(key=lambda s: s["relevance"], reverse=True)
        result[category] = scored
    return result


def generate_cover_letter(analysis: dict, tailored_cv: dict, user_prefs: dict) -> dict:
    """Generate a cover letter based on posting analysis and tailored CV."""
    system_prompt = _load_prompt("cover_letter")

    # Inject candidate name into prompt template
    master_cv = _load_master_cv()
    candidate_name = master_cv.get("personal", {}).get("name", "the candidate")
    system_prompt = system_prompt.replace("{candidate_name}", candidate_name)

    # Collect all scorable content
    cl_config = master_cv.get("cover_letter", {})
    to_score: dict[str, list[dict]] = {}

    soft_skills = cl_config.get("soft_skills", [])
    if soft_skills:
        to_score["soft_skills"] = soft_skills

    highlight_blocks = cl_config.get("highlight_blocks", [])
    if highlight_blocks:
        to_score["highlight_blocks"] = highlight_blocks

    # Extract experience bullets from tailored CV
    exp_bullets = []
    for exp in tailored_cv.get("experience", []):
        for bullet in exp.get("bullets", []):
            text = bullet if isinstance(bullet, str) else bullet.get("text", "")
            if text:
                exp_bullets.append({"text": text, "source": exp.get("company", "")})
    if exp_bullets:
        to_score["experience"] = exp_bullets

    scored = score_content_relevance(to_score, analysis)

    user_content = (
        f"## Job Posting Analysis\n```json\n{json.dumps(analysis, indent=2)}\n```\n\n"
        f"## Tailored CV Content\n```json\n{json.dumps(tailored_cv, indent=2)}\n```\n\n"
        f"## User Preferences\n```json\n{json.dumps(user_prefs, indent=2)}\n```"
    )

    if scored:
        user_content += (
            f"\n\n## Content Relevance (scored and sorted by relevance to this role)\n"
            f"```json\n{json.dumps(scored, indent=2)}\n```"
        )

    return _call_claude(system_prompt, user_content)


def regenerate_paragraph(
    paragraphs: list[str],
    index: int,
    feedback: str | None,
    analysis: dict,
    tailored_cv: dict,
    user_prefs: dict,
) -> str:
    """Regenerate a single cover letter paragraph using Claude."""
    master_cv = _load_master_cv()
    candidate_name = master_cv.get("personal", {}).get("name", "the candidate")

    system_prompt = (
        f"You are rewriting ONE paragraph of a cover letter for {candidate_name}. "
        "You will receive the full cover letter for context, plus which paragraph to rewrite. "
        "Match the voice and style of the surrounding paragraphs. "
        "Do NOT hallucinate skills, experiences, or metrics that are not in the CV data provided. "
        "Every claim must be backed by the CV content. "
        "NEVER use em dashes to insert mid-sentence asides or clauses. Use separate sentences instead. "
        "You MUST write a substantially different paragraph, not return the same text. "
        "Return ONLY the replacement paragraph text, no JSON, no quotes, no explanation."
    )

    numbered = "\n\n".join(
        f"[Paragraph {i + 1}]{' ← REWRITE THIS' if i == index else ''}:\n{p}"
        for i, p in enumerate(paragraphs)
    )

    cl_config = master_cv.get("cover_letter", {})
    to_score: dict[str, list[dict]] = {}

    soft_skills = cl_config.get("soft_skills", [])
    if soft_skills:
        to_score["soft_skills"] = soft_skills

    exp_bullets = []
    for exp in tailored_cv.get("experience", []):
        for bullet in exp.get("bullets", []):
            text = bullet if isinstance(bullet, str) else bullet.get("text", "")
            if text:
                exp_bullets.append({"text": text, "source": exp.get("company", "")})
    if exp_bullets:
        to_score["experience"] = exp_bullets

    scored = score_content_relevance(to_score, analysis)

    user_content = (
        f"## Full Cover Letter\n{numbered}\n\n"
        f"## Job Posting Analysis\n```json\n{json.dumps(analysis, indent=2)}\n```\n\n"
        f"## CV Data\n```json\n{json.dumps(tailored_cv, indent=2)}\n```\n\n"
        f"## User Preferences\n```json\n{json.dumps(user_prefs, indent=2)}\n```\n\n"
    )

    if scored:
        user_content += (
            f"## Content Relevance (scored and sorted by relevance to this role)\n"
            f"```json\n{json.dumps(scored, indent=2)}\n```\n\n"
        )

    if feedback:
        user_content += f"## User Feedback\n{feedback}\n\n"

    user_content += (
        f"Rewrite paragraph {index + 1}. "
        f"The new version MUST be meaningfully different from the original — "
        f"restructure, rephrase, and change emphasis. "
        f"Return ONLY the new paragraph text."
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        temperature=0.8,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()
