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


def _build_personal_profile(master_cv: dict, notes: dict) -> str:
    """Build a rich candidate personality profile for angle generation."""
    personal = master_cv.get("personal", {})
    sections = []

    # Philosophy / what drives them
    philosophy = notes.get("philosophy")
    if philosophy:
        sections.append(f"Core drive: {philosophy}")

    # Personality traits
    traits = notes.get("traits", [])
    if traits:
        sections.append("Personality:\n" + "\n".join(f"- {t}" for t in traits))

    # Passion projects (the real stuff, not CV bullets)
    passion_projects = notes.get("passion_projects", [])
    if passion_projects:
        lines = []
        for p in passion_projects:
            lines.append(f"- {p.get('what', '')}: {p.get('why', '')}")
        sections.append("What excites them outside work:\n" + "\n".join(lines))

    # Domain anecdotes
    anecdotes = notes.get("anecdotes", [])
    if anecdotes:
        lines = []
        for a in anecdotes:
            lines.append(f"- [{a.get('domain', '')}] {a.get('text', '')}")
        sections.append("Personal anecdotes:\n" + "\n".join(lines))

    # Basic facts
    languages = personal.get("languages", [])
    domains_worked = notes.get("domains_worked", _derive_domains_worked(master_cv))
    sections.append(
        f"Languages: {', '.join(languages)}\n"
        f"Domains worked in: {', '.join(domains_worked)}"
    )

    return "\n\n".join(sections)


def research_company(analysis: dict, master_cv: dict) -> dict:
    """Research a company and suggest a personal angle in one Haiku call.

    Returns ``{"summary": str, "personal_angle": str}`` or empty dict on failure.
    The summary covers what the company does, its mission/products, and what
    makes the role interesting. The personal angle connects the candidate's
    personality to the company.
    """
    company = analysis.get("company", "")
    domain = analysis.get("domain", "")
    role_title = analysis.get("role_title", "")
    if not company:
        return {}

    notes = _load_personal_notes()
    profile = _build_personal_profile(master_cv, notes)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=(
            "You research a company for a job applicant. You receive a company/role, "
            "a job posting analysis, and a rich candidate profile.\n\n"
            "Return a JSON object with two fields:\n\n"
            "1. \"summary\": 2-4 sentences about the company. What they do, their "
            "products/mission, tech stack if known, and what makes this role "
            "interesting. Be specific. If you don't know the company, say so and "
            "summarize what can be inferred from the job posting analysis.\n\n"
            "2. \"personal_angle\": 1-2 sentences suggesting a personal connection "
            "between who the candidate IS and what this company DOES. Draw from "
            "passion projects, personality traits, or anecdotes. NEVER use the "
            "pattern 'Your X aligns with their Y.' Good angles sound like something "
            "the candidate would say over coffee. If you can't find a genuine "
            "connection, say so rather than forcing one.\n\n"
            "NEVER use em dashes.\n"
            "Return ONLY valid JSON, no other text."
        ),
        messages=[{"role": "user", "content": (
            f"Company: {company}\n"
            f"Domain: {domain}\n"
            f"Role: {role_title}\n\n"
            f"## Job Posting Analysis\n```json\n"
            f"{json.dumps(analysis, indent=2)}\n```\n\n"
            f"Who this person is:\n{profile}"
        )}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)