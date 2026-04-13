"""Analyze a job posting using Claude API to extract structured requirements."""

import json
import re
from pathlib import Path
from urllib.request import Request, urlopen

import anthropic
import yaml
from ddgs import DDGS

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
    """Send job posting to Claude Sonnet for structured analysis. Returns parsed JSON."""
    client = anthropic.Anthropic()
    system_prompt = _load_prompt("analyze")

    # Inject candidate profile from master_cv.yaml
    master_cv = _load_master_cv()
    candidate_profile = _build_candidate_profile(master_cv)
    system_prompt = system_prompt.replace("{candidate_profile}", candidate_profile)

    response = client.messages.create(
        model="claude-sonnet-4-6",
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


def _search_company(company: str, domain: str) -> str:
    """Search for company info using DuckDuckGo. Returns snippets or empty string."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(f"{company} {domain} company", max_results=5))
        if not results:
            return ""
        snippets = []
        for r in results:
            snippets.append(f"- {r['title']}: {r['body']}")
        return "\n".join(snippets)
    except Exception:
        return ""


def research_company(analysis: dict, master_cv: dict) -> dict:
    """Research a company and suggest a personal angle.

    Searches the web for company info first, then passes results to Haiku
    for synthesis with the candidate profile.

    Returns ``{"summary": str, "verified": bool, "personal_angle": str}``
    or empty dict on failure.
    """
    company = analysis.get("company", "")
    domain = analysis.get("domain", "")
    role_title = analysis.get("role_title", "")
    if not company:
        return {}

    # Step 1: Web search for real company data
    search_results = _search_company(company, domain)

    notes = _load_personal_notes()
    profile = _build_personal_profile(master_cv, notes)

    # Step 2: Haiku synthesizes search results + posting + candidate profile
    search_section = ""
    if search_results:
        search_section = (
            "You have web search results about this company (see below). "
            "Use these as your PRIMARY source for the company summary. "
            "Set verified to true when the search results confirm concrete "
            "facts about the company.\n\n"
        )
    else:
        search_section = (
            "No web search results were found for this company. "
            "You must rely on the job posting analysis only. "
            "Set verified to false and prefix the summary with "
            "'Based on the job posting: '.\n\n"
        )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=(
            "You research a company for a job applicant. You receive a company/role, "
            "a job posting analysis, web search results (if available), and a rich "
            "candidate profile.\n\n"
            f"{search_section}"
            "Return a JSON object with three fields:\n\n"
            "1. \"summary\": 2-4 sentences about the company. What they do, their "
            "products/mission, tech stack if known, and what makes this role "
            "interesting. Be specific. Only state facts confirmed by the search "
            "results or the job posting text.\n\n"
            "2. \"verified\": true if web search results provided concrete facts "
            "about this company, false if you are inferring from the job posting "
            "text alone. This is critical for preventing hallucination downstream.\n\n"
            "3. \"personal_angle\": 1-2 sentences suggesting a personal connection "
            "between who the candidate IS and what this company DOES. Draw from "
            "passion projects, personality traits, or anecdotes. NEVER use the "
            "pattern 'Your X aligns with their Y.' Good angles sound like something "
            "the candidate would say over coffee. If you can't find a genuine "
            "connection, say so rather than forcing one. The angle must be grounded "
            "in the candidate profile provided, not fabricated.\n\n"
            "IMPORTANT: Do NOT fabricate company history, founding year, product "
            "names, or revenue figures. Only state what is confirmed by the search "
            "results or directly stated in the job posting.\n\n"
            "NEVER use em dashes.\n"
            "Return ONLY valid JSON, no other text."
        ),
        messages=[{"role": "user", "content": (
            f"Company: {company}\n"
            f"Domain: {domain}\n"
            f"Role: {role_title}\n\n"
            + (f"## Web Search Results\n{search_results}\n\n" if search_results else "")
            + f"## Job Posting Analysis\n```json\n"
            f"{json.dumps(analysis, indent=2)}\n```\n\n"
            f"Who this person is:\n{profile}"
        )}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


def _fetch_url_text(url: str) -> str:
    """Fetch a URL and extract visible text from HTML. Returns empty string on failure."""
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
    except Exception:
        return ""

    # Strip script/style blocks, then tags
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:8000]


def research_company_url(company_name: str, url: str | None = None) -> dict:
    """Research a company via web search and optional URL fetch.

    Returns ``{"company_info": str, "url_text": str}`` with raw research material.
    """
    # DuckDuckGo searches for breadth
    queries = [
        f"{company_name} company services technology",
        f"{company_name} careers hiring tech stack",
        f"{company_name} AI machine learning products",
    ]
    all_snippets = []
    for query in queries:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
            for r in results:
                snippet = f"- {r['title']}: {r['body']}"
                if snippet not in all_snippets:
                    all_snippets.append(snippet)
        except Exception:
            pass

    # Fetch URL if provided
    url_text = ""
    if url:
        url_text = _fetch_url_text(url)

    return {
        "company_info": "\n".join(all_snippets),
        "url_text": url_text,
    }


def synthesize_outreach_analysis(
    company_name: str,
    research: dict,
    master_cv: dict,
) -> dict:
    """Synthesize a job posting analysis from company research for cold outreach.

    Returns the same analysis dict format as ``analyze_posting`` so the
    existing CV generation pipeline works unchanged.
    """
    candidate_profile = _build_candidate_profile(master_cv)
    skills_block = yaml.dump(
        master_cv.get("skills", []),
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=(
            "You are helping a job applicant prepare a speculative/cold outreach to a company. "
            "You receive research about the company (web search results and/or website text) "
            "and the candidate's profile.\n\n"
            "Your job is to synthesize a realistic job posting analysis as if the company "
            "had posted a role that best matches the candidate's strengths AND the company's "
            "needs. This analysis will drive CV tailoring downstream.\n\n"
            "Return a JSON object with exactly these fields:\n"
            "{\n"
            '  "role_title": "the ideal role title for this candidate at this company",\n'
            '  "company": "company name",\n'
            '  "company_slug": "lowercase-hyphenated",\n'
            '  "must_have_skills": ["skills the company clearly needs based on research"],\n'
            '  "nice_to_have_skills": ["additional relevant skills"],\n'
            '  "experience_years": "estimated requirement or null",\n'
            '  "key_responsibilities": ["3-5 responsibilities this role would involve"],\n'
            '  "domain": "industry/domain",\n'
            '  "role_type": "ml | fullstack | hybrid | backend | frontend | other",\n'
            '  "is_recruiter": false,\n'
            '  "seniority": "junior | mid | senior | lead | staff",\n'
            '  "culture_signals": ["cultural indicators from research"],\n'
            '  "requires_chinese": false,\n'
            '  "red_flags": [],\n'
            '  "tone": "formal | conversational | startup-casual",\n'
            '  "fit_score": 1-10,\n'
            '  "fit_recommendation": "apply | consider | skip",\n'
            '  "fit_reasoning": "one sentence explaining the score",\n'
            '  "required_applicant_info": [],\n'
            '  "company_summary": "2-4 sentences about what the company does, their products, tech, and mission"\n'
            "}\n\n"
            "IMPORTANT:\n"
            "- The must_have_skills should reflect what the company ACTUALLY works with, "
            "not a wishlist. Ground them in the research.\n"
            "- The role_title should be realistic for this company, not generic.\n"
            "- Set fit_score based on how well the candidate matches this company's domain.\n"
            "- The company_summary must only contain facts confirmed by the research. "
            "Do NOT fabricate products, revenue, or founding dates.\n"
            "NEVER use em dashes.\n"
            "Return ONLY valid JSON, no other text."
        ),
        messages=[{"role": "user", "content": (
            f"## Company: {company_name}\n\n"
            + (f"## Web Search Results\n{research['company_info']}\n\n"
               if research.get("company_info") else "")
            + (f"## Company Website Text\n{research['url_text'][:4000]}\n\n"
               if research.get("url_text") else "")
            + f"## Candidate Profile\n{candidate_profile}\n\n"
            f"## Candidate Skills\n```yaml\n{skills_block}```"
        )}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw)


def analyze_skill_growth(analysis: dict, master_cv: dict) -> list[dict]:
    """Identify skill gaps from a posting and produce ~1h/day learning plans.

    Asks Sonnet to compare the posting's required/preferred competencies to the
    candidate's current skills, sort the gaps by impact on client attractiveness,
    and emit actionable learning schedules. Returns a list of skill dicts ranked
    by impact (most important first).
    """
    candidate_profile = _build_candidate_profile(master_cv)
    current_skills = master_cv.get("skills", [])
    skills_block = yaml.dump(current_skills, default_flow_style=False, allow_unicode=True, sort_keys=False)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=(
            "You are a career development coach for a software engineer. You receive "
            "a job posting analysis and the candidate's current skills/profile. Your "
            "job is to identify fields, technologies, or competencies the candidate "
            "should study to become more attractive to clients posting roles like "
            "this one.\n\n"
            "Steps:\n"
            "1. Extract every distinct skill, tool, methodology, or domain area the "
            "posting expects (required AND nice-to-have).\n"
            "2. Cross-reference against the candidate's current skills. Drop anything "
            "they already have at a working level.\n"
            "3. Sort the remaining gaps by impact on overall employability (not just "
            "this single role): foundational/high-leverage skills first, niche or "
            "low-transfer ones last.\n"
            "4. For each gap, produce a learning schedule the candidate can follow "
            "at roughly 1 hour per day, 5 days per week (so ~5h per week). The plan "
            "is allocated one topic per week, so each skill spans one or more whole "
            "weeks. Steps must be concrete: name specific resources (official docs, "
            "well-known books, free courses, small build-it-yourself projects). "
            "Avoid vague advice like 'read about X'.\n\n"
            "Return ONLY a JSON array. Each element is an object with:\n"
            '  "name": canonical skill name (short, e.g. "Kubernetes", "Rust", '
            '"Event-driven architecture"),\n'
            '  "why": one sentence on why this matters for employability,\n'
            '  "weeks": integer number of weeks (1 to 6),\n'
            '  "tasks": array of weekly task groups, one per week. Each is an '
            'object: {"week": 1, "items": ["day 1: ...", "day 2: ...", ...]} with '
            "5 daily items of ~1h each.\n\n"
            "Order the array from highest to lowest impact. Use canonical names so "
            "the same skill from different postings can be deduped. Return AT MOST "
            "5 skills, the highest-impact ones only.\n\n"
            "NEVER use em dashes. Use commas, colons, or parentheses instead.\n"
            "Return ONLY valid JSON, no markdown fences or prose."
        ),
        messages=[{"role": "user", "content": (
            f"## Job posting analysis\n```json\n{json.dumps(analysis, indent=2)}\n```\n\n"
            f"## Candidate profile\n{candidate_profile}\n\n"
            f"## Candidate current skills\n```yaml\n{skills_block}```"
        )}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Truncation safety net: trim to the last complete object inside the array.
        if raw.lstrip().startswith("["):
            end = raw.rfind("}")
            if end != -1:
                return json.loads(raw[: end + 1] + "]")
        raise