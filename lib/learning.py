"""Global learning plan: one topic per week, accumulated across postings.

Each parsed job posting feeds new skill gaps into a single global plan stored
under ``learning/``. Skills are deduped by canonical name. New skills are
slotted into the next free ISO week (one topic per week), preserving the
ordering already locked in for earlier weeks.
"""

import threading
from datetime import date, timedelta
from pathlib import Path

import yaml

from lib.analyzer import analyze_skill_growth

LEARNING_DIR = Path(__file__).parent.parent / "learning"
STATE_PATH = LEARNING_DIR / "state.yaml"
PLAN_PATH = LEARNING_DIR / "plan.md"

_lock = threading.Lock()


def _load_state() -> dict:
    if STATE_PATH.exists():
        return yaml.safe_load(STATE_PATH.read_text()) or {"skills": []}
    return {"skills": []}


def _save_state(state: dict) -> None:
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        yaml.dump(state, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )


def _iso_week_str(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _next_week(week_str: str) -> str:
    y, w = week_str.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1) + timedelta(days=7)
    return _iso_week_str(monday)


def _highest_assigned_week(skills: list[dict]) -> str | None:
    weeks = []
    for s in skills:
        end = s.get("week_end") or s.get("week_start")
        if end:
            weeks.append(end)
    return max(weeks) if weeks else None


def _next_free_week(skills: list[dict]) -> str:
    last = _highest_assigned_week(skills)
    if last is None:
        return _iso_week_str(date.today())
    return _next_week(last)


def _canonical(name: str) -> str:
    return name.strip().lower()


def _merge_skill(state: dict, new_skill: dict, source: dict) -> bool:
    """Merge one skill into state. Returns True if newly added."""
    key = _canonical(new_skill["name"])
    for existing in state["skills"]:
        if _canonical(existing["name"]) == key:
            # Already tracked, just record the new source
            sources = existing.setdefault("sources", [])
            if source not in sources:
                sources.append(source)
            return False

    weeks = max(1, int(new_skill.get("weeks", 1)))
    week_start = _next_free_week(state["skills"])
    week_end = week_start
    for _ in range(weeks - 1):
        week_end = _next_week(week_end)

    state["skills"].append({
        "name": new_skill["name"],
        "why": new_skill.get("why", ""),
        "weeks": weeks,
        "week_start": week_start,
        "week_end": week_end,
        "status": "planned",
        "sources": [source],
        "tasks": new_skill.get("tasks", []),
    })
    return True


def _render_plan(state: dict) -> str:
    skills = sorted(state["skills"], key=lambda s: s.get("week_start", "9999"))
    lines = ["# Learning Plan", ""]
    lines.append("One topic per week, ~1h/day for 5 days. Generated from parsed job postings.")
    lines.append("")
    lines.append("## Schedule")
    lines.append("")
    lines.append("| Week(s) | Skill | Status | Why |")
    lines.append("|---|---|---|---|")
    for s in skills:
        wk = s["week_start"]
        if s.get("week_end") and s["week_end"] != wk:
            wk = f"{wk} .. {s['week_end']}"
        lines.append(f"| {wk} | {s['name']} | {s.get('status', 'planned')} | {s.get('why', '')} |")
    lines.append("")

    for s in skills:
        lines.append(f"## {s['name']}")
        lines.append("")
        lines.append(f"*Why:* {s.get('why', '')}")
        lines.append("")
        sources = s.get("sources", [])
        if sources:
            src_lines = ", ".join(
                f"{src.get('company', '?')} ({src.get('role', '?')})" for src in sources
            )
            lines.append(f"*Surfaced by:* {src_lines}")
            lines.append("")
        for week_block in s.get("tasks", []):
            wnum = week_block.get("week", 1)
            lines.append(f"### Week {wnum}")
            lines.append("")
            for item in week_block.get("items", []):
                lines.append(f"- [ ] {item}")
            lines.append("")
    return "\n".join(lines)


def update_with_posting(analysis: dict, master_cv: dict) -> tuple[int, Path]:
    """Run skill-gap analysis for one posting and merge into the global plan.

    Returns ``(num_new_skills, plan_path)``. Thread-safe via a module lock so
    concurrent background runs can't corrupt state.
    """
    skills = analyze_skill_growth(analysis, master_cv)
    source = {
        "company": analysis.get("company", "?"),
        "role": analysis.get("role_title", "?"),
        "date": date.today().isoformat(),
    }
    with _lock:
        state = _load_state()
        added = 0
        for skill in skills:
            if _merge_skill(state, skill, source):
                added += 1
        _save_state(state)
        LEARNING_DIR.mkdir(parents=True, exist_ok=True)
        PLAN_PATH.write_text(_render_plan(state))
    return added, PLAN_PATH