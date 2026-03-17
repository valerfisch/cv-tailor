"""Interactive Q&A flow for CV tailoring decisions."""

from pathlib import Path

import questionary
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

_MOTIVATIONS_PATH = Path(__file__).parent.parent / "motivations.yaml"
_MASTER_CV_PATH = Path(__file__).parent.parent / "master_cv.yaml"


def _load_motivations() -> dict:
    if _MOTIVATIONS_PATH.exists():
        return yaml.safe_load(_MOTIVATIONS_PATH.read_text()) or {}
    return {}


def _save_motivation(domain: str, text: str) -> None:
    motivations = _load_motivations()
    motivations[domain.lower().strip()] = text
    _MOTIVATIONS_PATH.write_text(
        yaml.dump(motivations, default_flow_style=False, allow_unicode=True, sort_keys=True)
    )


def _load_master_cv() -> dict:
    return yaml.safe_load(_MASTER_CV_PATH.read_text())


def _load_highlight_blocks() -> list[dict]:
    """Load highlight block definitions from master_cv.yaml cover_letter config."""
    master = _load_master_cv()
    return master.get("cover_letter", {}).get("highlight_blocks", [])


def _load_thesis_angles() -> list[dict]:
    """Load thesis angle choices from master_cv.yaml cover_letter config."""
    master = _load_master_cv()
    return master.get("cover_letter", {}).get("thesis_angles", [])


# Keywords used to recommend a thesis framing angle
_THESIS_ANGLE_KEYWORDS: dict[str, list[str]] = {
    "ml_research": [
        "machine learning", "deep learning", "neural network", "model architecture",
        "research", "algorithm", "computer vision", "nlp", "natural language",
        "transformer", "gan", "loss function", "training", "fine-tun",
        "reinforcement learning", "generative", "diffusion", "llm",
        "foundation model", "multimodal",
    ],
    "production": [
        "deploy", "production", "ci/cd", "mlops", "infrastructure",
        "kubernetes", "docker", "cloud", "aws", "azure", "gcp",
        "pipeline", "integration", "legacy", "microservice", "api",
        "endpoint", "containeriz", "devops", "reliability",
    ],
    "process_optimization": [
        "automat", "workflow", "optimization", "efficien", "process",
        "manufacturing", "simulation", "digital twin", "industry 4.0",
        "operational", "throughput", "cost reduction", "lean", "six sigma",
        "continuous improvement",
    ],
    "data": [
        "data engineer", "data analy", "data scien", "dataset", "etl",
        "data pipeline", "data quality", "evaluation", "experiment",
        "ablation", "benchmark", "analytics", "sql", "data processing",
        "visualization", "dashboard", "bi ", "reporting",
    ],
}


def _auto_select_highlights(analysis: dict, highlight_blocks: list[dict]) -> set[str]:
    """Return highlight block IDs that match the posting analysis."""
    # Build a single searchable text blob from analysis fields
    parts = []
    for key in ("must_have_skills", "nice_to_have_skills", "key_responsibilities"):
        val = analysis.get(key, [])
        if isinstance(val, list):
            parts.extend(val)
        elif isinstance(val, str):
            parts.append(val)
    for key in ("domain", "role_type", "role_title", "tone"):
        val = analysis.get(key, "")
        if val:
            parts.append(val)
    for key in ("culture_signals", "red_flags"):
        val = analysis.get(key, [])
        if isinstance(val, list):
            parts.extend(val)

    blob = " ".join(parts).lower()

    matched = set()
    for block in highlight_blocks:
        block_id = block.get("id", "")
        keywords = block.get("keywords", [])
        if any(kw in blob for kw in keywords):
            matched.add(block_id)
    return matched


def _recommend_thesis_angle(analysis: dict) -> str:
    """Pick the best thesis framing angle based on posting keywords."""
    parts = []
    for key in ("must_have_skills", "nice_to_have_skills", "key_responsibilities"):
        val = analysis.get(key, [])
        if isinstance(val, list):
            parts.extend(val)
        elif isinstance(val, str):
            parts.append(val)
    for key in ("domain", "role_type", "role_title", "tone"):
        val = analysis.get(key, "")
        if val:
            parts.append(val)

    blob = " ".join(parts).lower()

    scores = {}
    for angle, keywords in _THESIS_ANGLE_KEYWORDS.items():
        scores[angle] = sum(1 for kw in keywords if kw in blob)

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "ml_research"


def display_analysis(analysis: dict) -> None:
    """Pretty-print the job posting analysis."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()

    table.add_row("Role", analysis.get("role_title", "Unknown"))
    company_display = analysis.get("company", "Unknown")
    if analysis.get("is_recruiter"):
        company_display += " [yellow](recruitment agency)[/yellow]"
    table.add_row("Company", company_display)
    table.add_row("Seniority", analysis.get("seniority", "Unknown"))
    table.add_row("Type", analysis.get("role_type", "Unknown"))
    table.add_row("Must-have", ", ".join(analysis.get("must_have_skills", [])))
    table.add_row("Nice-to-have", ", ".join(analysis.get("nice_to_have_skills", [])))

    if analysis.get("experience_years"):
        table.add_row("Exp. required", analysis["experience_years"])

    console.print(Panel(table, title="Position Analysis", border_style="green"))

    if analysis.get("requires_chinese"):
        console.print()
        console.print(
            "  [bold red]BLOCKER: Chinese language (Cantonese/Mandarin) required[/bold red]"
        )

    red_flags = analysis.get("red_flags", [])
    if red_flags:
        console.print()
        for flag in red_flags:
            console.print(f"  [yellow]![/yellow] {flag}")

    # Required applicant info
    req_info = analysis.get("required_applicant_info", [])
    if req_info:
        console.print()
        console.print("  [bold cyan]Required from applicant:[/bold cyan]")
        for item in req_info:
            val = item.get("value_from_posting")
            suffix = f" [dim]({val})[/dim]" if val else ""
            console.print(f"  [cyan]>[/cyan] {item.get('label', item.get('type', '?'))}{suffix}")

    # Fit assessment
    score = analysis.get("fit_score")
    rec = analysis.get("fit_recommendation", "")
    reasoning = analysis.get("fit_reasoning", "")
    if score is not None:
        color = {"apply": "green", "consider": "yellow", "skip": "red"}.get(rec, "white")
        console.print()
        console.print(
            f"  [{color}]Fit: {score}/10 — {rec.upper()}[/{color}]"
            + (f"  {reasoning}" if reasoning else "")
        )
    console.print()


def get_required_info(analysis: dict) -> list[dict]:
    """Prompt user for values that the posting explicitly requires from applicants.

    Returns list of ``{"type": str, "label": str, "value": str}`` dicts,
    only for items where the user provided a non-empty answer.
    """
    items = analysis.get("required_applicant_info", [])
    if not items:
        return []

    console.print("[bold]The posting asks you to provide:[/bold]")
    results = []
    for item in items:
        label = item.get("label", item.get("type", "info"))
        default = item.get("value_from_posting") or ""
        value = questionary.text(
            f"  {label}:",
            default=default,
        ).ask()
        if value is None:
            raise KeyboardInterrupt
        value = value.strip()
        if value:
            results.append({
                "type": item.get("type", "other"),
                "label": label,
                "value": value,
            })
    console.print()
    return results


def _collect_known_skills(master: dict) -> set[str]:
    """Build a lowercase set of all skills/tech mentioned in the master CV."""
    known = set()
    # Skill items
    for cat in master.get("skills", []):
        for item in cat.get("items", []):
            known.add(item.lower())
    # Tech from experience bullets
    for exp in master.get("experience", []):
        for bullet in exp.get("bullets", []):
            text = bullet if isinstance(bullet, str) else bullet.get("text", "")
            known.add(text.lower())
    # Tech from projects
    for proj in master.get("projects", []):
        if proj.get("tech"):
            for t in proj["tech"].split(","):
                known.add(t.strip().lower())
    return known


def _skill_is_known(skill: str, known: set[str]) -> bool:
    """Check if a posting skill is covered by any known skill (substring match)."""
    sl = skill.lower()
    for k in known:
        if sl in k or k in sl:
            return True
    return False


def check_skill_gaps(analysis: dict, master: dict) -> list[dict]:
    """Identify must-have skills not in the master CV, ask user about them.

    Returns list of {"skill": str, "category": str, "description": str} for
    skills to add.
    """
    known = _collect_known_skills(master)
    must_have = analysis.get("must_have_skills", [])

    gaps = [s for s in must_have if not _skill_is_known(s, known)]
    if not gaps:
        return []

    console.print()
    console.print(
        f"[bold]Skill gaps:[/bold] {len(gaps)} required skill(s) not in your master CV:"
    )
    for g in gaps:
        console.print(f"  [red]-[/red] {g}")
    console.print()

    additions = []
    categories = [cat["category"] for cat in master.get("skills", [])]
    for skill in gaps:
        answer = questionary.text(
            f"Experience with {skill}? (describe briefly, or Enter to skip)",
        ).ask()
        if answer is None:
            raise KeyboardInterrupt
        if not answer.strip():
            continue

        # Pick which category to add to (with cancel option)
        choices = categories + ["Cancel (skip this skill)"]
        category = questionary.select(
            f"Add '{skill}' to which skill category?",
            choices=choices,
        ).ask()
        if category is None:
            raise KeyboardInterrupt
        if category.startswith("Cancel"):
            continue

        additions.append({
            "skill": skill,
            "category": category,
            "description": answer.strip(),
        })

    return additions


def get_cv_preferences(analysis: dict) -> dict:
    """Ask CV-related preferences. Returns dict with profile_type, framing,
    include_projects, include_cover_letter."""
    prefs = {}

    # 1. CV profile type
    role_type = analysis.get("role_type", "hybrid")
    choices = [
        questionary.Choice("ML-focused", value="ml"),
        questionary.Choice("Full-stack focused", value="fullstack"),
        questionary.Choice("Hybrid (ML + Full-stack)", value="hybrid"),
    ]
    if role_type in ("ml", "fullstack"):
        recommended = role_type
    else:
        recommended = "hybrid"
    choices.sort(key=lambda c: c.value != recommended)
    choices[0].title = f"{choices[0].title} (recommended)"

    prefs["profile_type"] = questionary.select(
        "CV profile type?",
        choices=choices,
    ).ask()
    if prefs["profile_type"] is None:
        raise KeyboardInterrupt

    # 2. Framing strategy — adapts based on profile type
    profile = prefs["profile_type"]
    if profile == "fullstack":
        # Full-stack focus: ask how to frame ML/thesis experience
        framing_choices = [
            questionary.Choice(
                "As data-driven engineering (ML informs better systems)",
                value="data_driven",
            ),
            questionary.Choice(
                "As R&D capability (prototyping, experimentation, model deployment)",
                value="rd_capability",
            ),
            questionary.Choice(
                "Minimize it (keep focus on full-stack, mention ML briefly)",
                value="minimize_ml",
            ),
        ]
        rec_framing = "data_driven"
        framing_question = "How should we frame your ML/thesis experience?"
    else:
        # ML or hybrid focus: ask how to frame full-stack experience
        framing_choices = [
            questionary.Choice(
                "As ML-adjacent (pipelines, deployment, 3D, production)",
                value="ml_adjacent",
            ),
            questionary.Choice(
                "As core strength (you're a builder who also does ML)",
                value="core_strength",
            ),
            questionary.Choice(
                "Minimize it (focus on ML/research only)",
                value="minimize",
            ),
        ]
        if role_type in ("fullstack", "frontend", "backend"):
            rec_framing = "core_strength"
        else:
            rec_framing = "ml_adjacent"
        framing_question = "How should we frame your full-stack experience?"

    framing_choices.sort(key=lambda c: c.value != rec_framing)
    framing_choices[0].title = f"{framing_choices[0].title} (recommended)"

    prefs["framing"] = questionary.select(
        framing_question,
        choices=framing_choices,
    ).ask()
    if prefs["framing"] is None:
        raise KeyboardInterrupt

    # 3. Include projects section?
    prefs["include_projects"] = questionary.confirm(
        "Include research projects section?",
        default=True,
    ).ask()
    if prefs["include_projects"] is None:
        raise KeyboardInterrupt

    # 4. Cover letter?
    prefs["include_cover_letter"] = questionary.confirm(
        "Generate a cover letter?",
        default=True,
    ).ask()
    if prefs["include_cover_letter"] is None:
        raise KeyboardInterrupt

    return prefs


def get_cl_preferences(
    analysis: dict,
    *,
    research: dict | None = None,
) -> dict:
    """Ask cover letter preferences. Returns dict with CL-specific keys."""
    prefs = {}
    research = research or {}

    prefs["cover_letter_tone"] = questionary.select(
        "Cover letter tone?",
        choices=[
            questionary.Choice("Formal", value="formal"),
            questionary.Choice("Conversational", value="conversational"),
        ],
    ).ask()
    if prefs["cover_letter_tone"] is None:
        raise KeyboardInterrupt

    console.print()

    # Show company research summary before asking "why company"
    company_summary = research.get("summary")
    if company_summary:
        console.print(Panel(
            company_summary,
            title="[bold]Company Research[/bold]",
            border_style="blue",
        ))
        console.print()

    console.print("[bold]Cover letter context[/bold] (press Enter to skip any):")

    company = analysis.get("company", "this company")
    domain = analysis.get("domain", "this field")

    # Look up saved motivation for this domain
    saved_motivations = _load_motivations()
    saved = saved_motivations.get(domain.lower().strip())

    if saved:
        console.print(
            f"  [dim]Saved motivation for {domain}:[/dim] [italic]{saved}[/italic]"
        )
        why_company = questionary.text(
            f"Why {company} / {domain}? (Enter to reuse saved)",
        ).ask()
    else:
        why_company = questionary.text(
            f"Why does {company} / {domain} interest you specifically?",
        ).ask()

    if why_company is None:
        raise KeyboardInterrupt

    if why_company.strip():
        prefs["cl_why_company"] = why_company.strip()
        _save_motivation(domain, why_company.strip())
    elif saved:
        prefs["cl_why_company"] = saved
    else:
        prefs["cl_why_company"] = None

    # Thesis/research angle — load choices from config
    thesis_angles = _load_thesis_angles()
    if thesis_angles:
        angle_choices = [
            questionary.Choice(a["label"], value=a["value"])
            for a in thesis_angles
        ]
        rec_angle = _recommend_thesis_angle(analysis)
        angle_choices.sort(key=lambda c: c.value != rec_angle)
        angle_choices[0].title = f"{angle_choices[0].title} (recommended)"

        thesis_angle = questionary.select(
            "How should we frame your thesis/research for this role?",
            choices=angle_choices,
        ).ask()
        if thesis_angle is None:
            raise KeyboardInterrupt
        prefs["cl_thesis_angle"] = thesis_angle
    else:
        prefs["cl_thesis_angle"] = "ml_research"

    # Personal angle from research
    personal_suggestion = research.get("personal_angle")
    if personal_suggestion:
        console.print()
        console.print(
            f"[bold]Suggested angle:[/bold] [italic]{personal_suggestion}[/italic]"
        )
    personal = questionary.text(
        "Personal connection? (Enter to use suggestion, or type your own)",
        default="" if not personal_suggestion else "",
    ).ask()
    if personal is None:
        raise KeyboardInterrupt
    if personal.strip():
        prefs["cl_personal"] = personal.strip()
    elif personal_suggestion:
        prefs["cl_personal"] = personal_suggestion
    else:
        prefs["cl_personal"] = None

    # Highlight blocks — load from config, auto-select based on posting analysis
    highlight_blocks = _load_highlight_blocks()

    if highlight_blocks:
        auto_selected = _auto_select_highlights(analysis, highlight_blocks)

        console.print()
        if auto_selected:
            console.print(
                f"[bold]Highlight blocks[/bold] ([green]{len(auto_selected)} auto-selected[/green] based on posting):"
            )
        else:
            console.print("[bold]Highlight blocks[/bold] (select any that apply):")

        block_choices = [
            questionary.Choice(
                block["label"],
                value=block["id"],
                checked=block["id"] in auto_selected,
            )
            for block in highlight_blocks
        ]

        selected = questionary.checkbox(
            "Pick relevant highlights:",
            choices=block_choices,
        ).ask()
        if selected is None:
            raise KeyboardInterrupt
        prefs["cl_highlight_blocks"] = selected
    else:
        prefs["cl_highlight_blocks"] = []

    return prefs


def get_job_posting() -> str:
    """Prompt user for job posting text."""
    console.print(
        Panel(
            "Paste the job posting below, then press [bold]Ctrl+D[/bold] (or [bold]Ctrl+Z Enter[/bold] on Windows) to finish:",
            border_style="green",
        )
    )

    lines = []
    try:
        while True:
            line = input()
            lines.append(line)
    except EOFError:
        pass

    text = "\n".join(lines).strip()
    if not text:
        console.print("[red]No posting text provided. Exiting.[/red]")
        raise SystemExit(1)

    return text
