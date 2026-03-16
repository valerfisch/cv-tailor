"""Analyze a job posting using Claude API to extract structured requirements."""

import json
from pathlib import Path

import anthropic
import yaml

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
MASTER_CV_PATH = Path(__file__).parent.parent / "master_cv.yaml"
PERSONAL_NOTES_PATH = Path(__file__).parent.parent / "personal_notes.yaml"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text()


def _load_master_cv() -> dict:
    return yaml.safe_load(MASTER_CV_PATH.read_text())


def _load_personal_notes() -> dict:
    if PERSONAL_NOTES_PATH.exists():
        return yaml.safe_load(PERSONAL_NOTES_PATH.read_text()) or {}
    return {}


def _build_candidate_profile(master_cv: dict) -> str:
    """Build a candidate profile summary from master_cv.yaml for the analyze prompt."""
    personal = master_cv.get("personal", {})
    experience = master_cv.get("experience", [])
    education = master_cv.get("education", [])
    languages = personal.get("languages", [])
    location = personal.get("contact", {}).get("location", "Unknown")
    visa = personal.get("visa", "")

    # Summarize experience
    exp_lines = []
    for exp in experience:
        company = exp.get("company", "")
        dates = exp.get("dates", "")
        title = exp.get("title", "")
        exp_lines.append(f"- {title} at {company} ({dates})")

    # Summarize education
    edu_lines = []
    for edu in education:
        degree = edu.get("degree", "")
        school = edu.get("school", "")
        details = edu.get("details", "")
        edu_lines.append(f"- {degree} from {school}" + (f" ({details})" if details else ""))

    # Language summary
    lang_names = [lang.split("(")[0].strip() for lang in languages]

    profile = f"- Based in {location}"
    if visa:
        profile += f" ({visa})"
    profile += "\n"
    profile += f"- Languages: {', '.join(languages)}\n"
    profile += "- Experience:\n" + "\n".join(f"  {line}" for line in exp_lines) + "\n"
    profile += "- Education:\n" + "\n".join(f"  {line}" for line in edu_lines)

    return profile


def analyze_posting(posting_text: str) -> dict:
    """Send job posting to Claude haiku for structured analysis. Returns parsed JSON."""
    client = anthropic.Anthropic()
    system_prompt = _load_prompt("analyze")

    # Inject candidate profile from master_cv.yaml
    master_cv = _load_master_cv()
    candidate_profile = _build_candidate_profile(master_cv)
    system_prompt = system_prompt.replace("{candidate_profile}", candidate_profile)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": posting_text}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)


def _derive_domains_worked(master_cv: dict) -> list[str]:
    """Derive domains worked from experience entries."""
    domains = []
    for exp in master_cv.get("experience", []):
        company = exp.get("company", "")
        domain_tags = exp.get("tags", [])
        if company:
            domains.append(company)
    return domains


def research_company_angle(analysis: dict, master_cv: dict) -> str | None:
    """Use Claude to suggest a personal connection angle based on company + candidate.

    Returns a 1-2 sentence suggestion, or None on failure.
    """
    company = analysis.get("company", "")
    domain = analysis.get("domain", "")
    role_title = analysis.get("role_title", "")
    if not company:
        return None

    # Gather candidate personal info for angle matching
    personal = master_cv.get("personal", {})
    interests = personal.get("interests", [])
    languages = personal.get("languages", [])

    # Derive domains from experience or load from personal_notes.yaml
    notes = _load_personal_notes()
    domains_worked = notes.get("domains_worked", _derive_domains_worked(master_cv))

    # Build candidate background from personal notes anecdotes
    anecdote_lines = []
    for anecdote in notes.get("anecdotes", []):
        anecdote_lines.append(f"- {anecdote.get('text', '')}")

    # Add interests as background lines
    background_lines = [
        f"- Interests: {', '.join(interests)}",
        f"- Languages: {', '.join(languages)}",
        f"- Domains worked in: {', '.join(domains_worked)}",
    ] + anecdote_lines

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=(
            "You are helping a job applicant find a genuine personal connection to a company "
            "for their cover letter. Based on what you know about the company and the candidate's "
            "background, suggest ONE concrete angle (1-2 sentences) they could use. "
            "It should feel authentic, not generic. If the company's values, mission, or tech stack "
            "connect to something specific about the candidate, highlight that. "
            "If you don't know enough about the company, say so briefly and suggest a generic "
            "domain-based angle instead. Return ONLY the suggestion text, nothing else."
        ),
        messages=[{"role": "user", "content": (
            f"Company: {company}\n"
            f"Domain: {domain}\n"
            f"Role: {role_title}\n\n"
            f"Candidate background:\n"
            + "\n".join(background_lines)
        )}],
    )
    return response.content[0].text.strip()