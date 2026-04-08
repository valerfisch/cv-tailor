"""Render tailored CV and cover letter data to PDF via Jinja2 + WeasyPrint."""

import copy
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
OUTPUT_DIR = Path(__file__).parent.parent / "output"


def _get_jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
    )


def _output_path(data: dict, doc_type: str) -> Path:
    """Build path as output/<Company>/<Name> CV.pdf or CL.pdf."""
    name = data.get("personal", {}).get("name", "CV")
    company = data.get("company_name", data.get("company_slug", "output"))
    company_dir = OUTPUT_DIR / company
    company_dir.mkdir(parents=True, exist_ok=True)
    return company_dir / f"{name} {doc_type}.pdf"


def _reduce_skill(data: dict, category_name: str) -> dict:
    """Remove a specific skill category by name."""
    data = copy.deepcopy(data)
    data["skills"] = [s for s in data.get("skills", []) if s["category"] != category_name]
    return data


def _remove_bachelor(data: dict) -> dict:
    """Remove bachelor's degree entries from education."""
    data = copy.deepcopy(data)
    data["education"] = [
        edu for edu in data.get("education", [])
        if "B.Sc." not in edu.get("degree", "") and "Bachelor" not in edu.get("degree", "")
    ]
    return data


def _trim_position(data: dict, index: int) -> dict:
    """Trim an experience entry at *index* to 2 bullets."""
    data = copy.deepcopy(data)
    experiences = data.get("experience", [])
    if 0 <= index < len(experiences):
        exp = experiences[index]
        if len(exp.get("bullets", [])) > 2:
            exp["bullets"] = exp["bullets"][:2]
    return data


def _reduce_all_by_one(data: dict) -> dict:
    """Remove the last bullet from every experience entry that has more than one."""
    data = copy.deepcopy(data)
    for exp in data.get("experience", []):
        bullets = exp.get("bullets", [])
        if len(bullets) > 1:
            exp["bullets"] = bullets[:-1]
    return data


def _drop_additional(data: dict) -> dict:
    """Flag to hide languages & work authorization."""
    data = copy.deepcopy(data)
    data["hide_additional"] = True
    return data


def _remove_experience(data: dict, index: int) -> dict:
    """Remove a specific experience entry by index."""
    data = copy.deepcopy(data)
    experiences = data.get("experience", [])
    if 0 <= index < len(experiences):
        experiences.pop(index)
    return data


def _remove_project(data: dict, index: int) -> dict:
    """Remove a specific project entry by index."""
    data = copy.deepcopy(data)
    projects = data.get("projects", [])
    if 0 <= index < len(projects):
        projects.pop(index)
    return data


def get_available_reductions(data: dict) -> list[dict]:
    """Return available content reductions based on current CV data.

    Each entry: {"id": str, "label": str, "apply": callable(data) -> data}
    """
    reductions = []

    # One entry per removable skill category (all except the first)
    skills = data.get("skills", [])
    if len(skills) > 1:
        for cat in skills[1:]:
            name = cat["category"]
            reductions.append({
                "id": f"skill:{name}",
                "label": f"Remove skill category: {name}",
                "apply": lambda d, n=name: _reduce_skill(d, n),
            })

    # Bachelor's degree
    has_bachelor = any(
        "B.Sc." in edu.get("degree", "") or "Bachelor" in edu.get("degree", "")
        for edu in data.get("education", [])
    )
    if has_bachelor:
        reductions.append({
            "id": "remove_bachelor",
            "label": "Remove bachelor's degree",
            "apply": _remove_bachelor,
        })

    # Trim individual experience entries to 2 bullets
    experiences = data.get("experience", [])
    for i, exp in enumerate(experiences):
        n = len(exp.get("bullets", []))
        if n > 2:
            company = exp.get("company", "experience")
            reductions.append({
                "id": f"trim_pos:{i}",
                "label": f"Trim {company} bullets ({n} -> 2)",
                "apply": lambda d, idx=i: _trim_position(d, idx),
            })

    # Reduce all experience entries by one bullet
    has_trimmable = any(len(e.get("bullets", [])) > 1 for e in experiences)
    if has_trimmable:
        reductions.append({
            "id": "reduce_all_by_one",
            "label": "Reduce all experience entries by 1 bullet",
            "apply": _reduce_all_by_one,
        })

    # Remove individual experience entries (short ones with <= 3 bullets)
    if len(experiences) > 1:
        for i, exp in enumerate(experiences):
            bullet_count = len(exp.get("bullets", []))
            if bullet_count <= 3:
                company = exp.get("company", "experience")
                title = exp.get("title", "")
                label = f"Remove {company}"
                if title:
                    label += f" ({title})"
                label += f" [{bullet_count} bullets]"
                reductions.append({
                    "id": f"remove_exp:{i}",
                    "label": label,
                    "apply": lambda d, idx=i: _remove_experience(d, idx),
                })

    # Remove individual projects
    projects = data.get("projects", [])
    if projects:
        for i, proj in enumerate(projects):
            title = proj.get("title", f"Project {i + 1}")
            reductions.append({
                "id": f"remove_proj:{i}",
                "label": f"Remove project: {title}",
                "apply": lambda d, idx=i: _remove_project(d, idx),
            })

    # Languages & visa
    if not data.get("hide_additional"):
        reductions.append({
            "id": "drop_additional",
            "label": "Drop languages & visa section",
            "apply": _drop_additional,
        })

    return reductions


def apply_reductions(data: dict, selected_ids: list[str], available: list[dict]) -> dict:
    """Apply selected reductions to CV data. Returns modified copy."""
    result = copy.deepcopy(data)
    lookup = {r["id"]: r["apply"] for r in available}
    for rid in selected_ids:
        if rid in lookup:
            result = lookup[rid](result)
    return result


def render_cv_doc(data: dict):
    """Render CV data to a WeasyPrint document. Returns (doc, output_path)."""
    env = _get_jinja_env()
    template = env.get_template("cv.html.j2")
    css_path = TEMPLATES_DIR / "style.css"

    output_path = _output_path(data, "CV")
    html_str = template.render(**data)
    doc = HTML(string=html_str, base_url=str(TEMPLATES_DIR)).render(
        stylesheets=[str(css_path)],
    )
    return doc, output_path


def render_cv(data: dict, output_path: Path | None = None) -> Path:
    """Render CV data dict to PDF. No auto-fitting — caller handles reductions."""
    env = _get_jinja_env()
    template = env.get_template("cv.html.j2")

    if output_path is None:
        output_path = _output_path(data, "CV")

    css_path = TEMPLATES_DIR / "style.css"
    html_str = template.render(**data)
    doc = HTML(string=html_str, base_url=str(TEMPLATES_DIR)).render(
        stylesheets=[str(css_path)],
    )
    doc.write_pdf(str(output_path))
    return output_path


def render_cover_letter(data: dict, output_path: Path | None = None) -> Path:
    """Render cover letter data dict to PDF. Returns the output path."""
    env = _get_jinja_env()
    template = env.get_template("cover_letter.html.j2")
    html_str = template.render(**data)

    if output_path is None:
        output_path = _output_path(data, "CL")

    css_path = TEMPLATES_DIR / "style.css"
    HTML(string=html_str, base_url=str(TEMPLATES_DIR)).write_pdf(
        str(output_path),
        stylesheets=[str(css_path)],
    )
    return output_path
