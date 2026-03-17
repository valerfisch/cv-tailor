#!/usr/bin/env python3
"""CV Tailor — Interactive CLI for generating tailored CVs and cover letters."""

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import questionary
import yaml
from rich.console import Console
from rich.panel import Panel

from lib.analyzer import analyze_posting, research_company
from lib.generator import generate_cover_letter, generate_tailored_cv, polish_cover_letter, regenerate_bullet, regenerate_paragraph, regenerate_summary, validate_claims
from lib.interactive import check_skill_gaps, console, display_analysis, get_cl_preferences, get_cv_preferences, get_job_posting, get_required_info
from lib.renderer import apply_reductions, get_available_reductions, render_cover_letter, render_cv, render_cv_doc
from lib.sync import sync_master_cv

MASTER_CV_PATH = Path(__file__).parent / "master_cv.yaml"
BLOCKS_PATH = Path(__file__).parent / "Blocks.md"
REWRITE_DIR = Path(__file__).parent / "cvs"  # where CV - * - REWRITE.md files live
OUTPUT_DIR = Path(__file__).parent / "output"


def _load_master_cv() -> dict:
    return yaml.safe_load(MASTER_CV_PATH.read_text())


def _strip_metadata_tags(skills: list[dict]) -> list[dict]:
    """Remove internal metadata tags (e.g. 'source: user_claimed') from skill items."""
    cleaned = []
    for cat in skills:
        items = [re.sub(r"\s*\(source:\s*\w+\)", "", item) for item in cat.get("items", [])]
        cleaned.append({**cat, "items": items})
    return cleaned


def _build_cv_render_data(
    master: dict,
    tailored: dict,
    company_name: str,
    required_info: list[dict] | None = None,
) -> dict:
    """Assemble the data dict expected by cv.html.j2."""
    header_notes = []
    for item in (required_info or []):
        header_notes.append(f"{item['label']}: {item['value']}")
    return {
        "personal": master["personal"],
        "title": tailored["title"],
        "summary": tailored["summary"],
        "skills": _strip_metadata_tags(tailored["skills"]),
        "experience": tailored["experience"],
        "projects": tailored.get("projects", []),
        "education": tailored["education"],
        "company_name": company_name,
        "header_notes": header_notes,
    }


def _build_cl_render_data(
    master: dict,
    cl: dict,
    company_name: str,
    required_info: list[dict] | None = None,
) -> dict:
    """Assemble the data dict expected by cover_letter.html.j2."""
    subject = cl.get("subject", "")

    # Append reference number to subject if present and not already included
    for item in (required_info or []):
        if item["type"] == "reference_number" and item["value"] not in subject:
            subject = f"{subject} (Ref: {item['value']})"
            break

    return {
        "personal": master["personal"],
        "date": date.today().strftime("%B %d, %Y"),
        "recipient": cl.get("recipient", ""),
        "subject": subject,
        "paragraphs": cl.get("paragraphs", []),
        "closing": cl.get("closing", "Kind regards"),
        "company_name": company_name,
    }


def _get_candidate_name() -> str:
    """Read candidate name from master_cv.yaml."""
    master = yaml.safe_load(MASTER_CV_PATH.read_text())
    return master.get("personal", {}).get("name", "")


def _format_cover_letter_text(cl: dict, role_title: str) -> str:
    """Format cover letter content as readable text."""
    name = _get_candidate_name()
    lines = [cl.get("recipient", "Dear Hiring Manager") + ",", ""]
    for para in cl.get("paragraphs", []):
        lines.append(para)
        lines.append("")
    lines.append(cl.get("closing", "Kind regards") + ",")
    lines.append(name)
    return "\n".join(lines)


def _append_to_blocks(cl: dict, role_title: str) -> None:
    """Append cover letter to Blocks.md for future reference."""
    name = _get_candidate_name()
    block = f"\n\n{role_title}\n{'=' * len(role_title)}\n\n"
    block += cl.get("recipient", "Dear Hiring Manager") + ",\n\n"
    for para in cl.get("paragraphs", []):
        block += para + "\n\n"
    block += cl.get("closing", "Kind regards") + ",\n"
    block += f"{name}\n"

    with open(BLOCKS_PATH, "a") as f:
        f.write(block)


def _check_rewrite_freshness() -> None:
    """Warn if any CV rewrite files are newer than master_cv.yaml. Offer to sync."""
    if not MASTER_CV_PATH.exists():
        return

    master_mtime = MASTER_CV_PATH.stat().st_mtime
    stale_files = []

    for rewrite in REWRITE_DIR.glob("CV - * - REWRITE.md"):
        if rewrite.stat().st_mtime > master_mtime:
            stale_files.append(rewrite.name)

    if stale_files:
        console.print(
            Panel(
                "[yellow]The following CV rewrite files are newer than master_cv.yaml:[/yellow]\n"
                + "\n".join(f"  - {f}" for f in stale_files),
                title="Stale master CV",
                border_style="yellow",
            )
        )
        action = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice("Sync now (update master_cv.yaml from rewrite files)", value="sync"),
                questionary.Choice("Continue without syncing", value="continue"),
                questionary.Choice("Exit", value="exit"),
            ],
        ).ask()

        if action == "sync":
            _run_sync()
        elif action == "exit" or action is None:
            sys.exit(0)


def _run_sync() -> None:
    """Run the sync process to update master_cv.yaml from rewrite files."""
    with console.status("[bold green]Syncing master_cv.yaml from rewrite files..."):
        try:
            sync_master_cv()
        except Exception as e:
            console.print(f"[red]Sync failed: {e}[/red]")
            sys.exit(1)
    console.print("[green]master_cv.yaml synced successfully.[/green]")
    console.print()


def _write_review_file(
    paragraphs: list[str],
    review_path: Path,
    issues: list[dict] | None = None,
) -> None:
    """Write cover letter paragraphs to a markdown file for editing."""
    lines = [
        "# Cover Letter Review",
        "",
        "Edit paragraphs directly. Check a checkbox to request an AI rewrite.",
        "Add feedback as a `>` blockquote below the checkbox to guide the rewrite.",
        "Save the file and press Enter in the terminal when done.",
        "",
    ]

    if issues:
        lines.append("### Flagged Issues")
        lines.append("")
        for issue in issues:
            lines.append(f"- **{issue['text']}**: {issue['issue']}")
        lines.append("")

    for i, para in enumerate(paragraphs):
        lines.append(f"### Paragraph {i + 1}")
        lines.append("- [ ] Rewrite with AI")
        lines.append("")
        lines.append(para)
        lines.append("")

    lines.append("---")
    lines.append("- [ ] Regenerate entire cover letter")
    lines.append("")

    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("\n".join(lines))


def _parse_review_file(review_path: Path) -> dict:
    """Parse the review markdown file.

    Returns::

        {
            "paragraphs": list[str],
            "rewrite_requests": list[{"index": int, "feedback": str | None}],
            "regenerate_all": bool,
        }
    """
    content = review_path.read_text()

    regenerate_all = bool(re.search(r"- \[[xX]\] Regenerate entire", content))

    # Split content into paragraph sections using headers as boundaries
    paragraph_pattern = r"### Paragraph (\d+)\s*\n(.*?)(?=### Paragraph \d+|\n---\n|\Z)"
    matches = list(re.finditer(paragraph_pattern, content, re.DOTALL))

    paragraphs = []
    rewrite_requests = []

    for match in matches:
        idx = int(match.group(1)) - 1
        section = match.group(2)
        lines = section.split("\n")

        rewrite_checked = False
        feedback_lines = []
        text_lines = []
        collecting_feedback = False

        for line in lines:
            # Checkbox line
            if re.match(r"^- \[[ xX]\]", line):
                rewrite_checked = bool(re.match(r"^- \[[xX]\]", line))
                collecting_feedback = True
                continue

            # Feedback blockquote (right after checkbox)
            if collecting_feedback and line.startswith(">"):
                fb = line.lstrip(">").strip()
                if fb:
                    feedback_lines.append(fb)
                continue

            # Empty line between checkbox and text
            if collecting_feedback and line.strip() == "":
                collecting_feedback = False
                continue

            collecting_feedback = False
            text_lines.append(line)

        text = "\n".join(text_lines).strip()
        paragraphs.append(text)

        if rewrite_checked:
            feedback = " ".join(feedback_lines) if feedback_lines else None
            rewrite_requests.append({"index": idx, "feedback": feedback})

    return {
        "paragraphs": paragraphs,
        "rewrite_requests": rewrite_requests,
        "regenerate_all": regenerate_all,
    }


def _review_cover_letter(
    cl_content: dict,
    analysis: dict,
    tailored_cv: dict,
    prefs: dict,
    company_name: str,
    issues: list[dict] | None = None,
) -> dict:
    """File-based cover letter review. Returns the approved cover letter dict."""
    paragraphs = list(cl_content.get("paragraphs", []))
    review_path = OUTPUT_DIR / company_name / "cover_letter_review.md"

    while True:
        _write_review_file(paragraphs, review_path, issues=issues)
        issues = None  # Only show flagged issues on first write

        console.print()
        console.print(f"  [bold]Review file:[/bold] {review_path}")
        console.print(
            "  Edit paragraphs, check boxes for AI rewrites, then save the file."
        )

        try:
            input("  Press Enter when done editing... ")
        except EOFError:
            raise KeyboardInterrupt

        parsed = _parse_review_file(review_path)

        # Full regeneration
        if parsed["regenerate_all"]:
            with console.status("[bold green]Regenerating cover letter..."):
                try:
                    cl_content = generate_cover_letter(analysis, tailored_cv, prefs)
                except Exception as e:
                    console.print(f"[red]Failed: {e}[/red]")
                    continue
            paragraphs = list(cl_content.get("paragraphs", []))
            console.print("  [green]Cover letter regenerated[/green]")
            continue

        # Apply manual edits
        paragraphs = parsed["paragraphs"]

        # Process AI rewrite requests
        if parsed["rewrite_requests"]:
            for req in parsed["rewrite_requests"]:
                idx = req["index"]
                with console.status(f"[bold green]Rewriting paragraph {idx + 1}..."):
                    new_para = regenerate_paragraph(
                        paragraphs, idx, req["feedback"],
                        analysis, tailored_cv, prefs,
                    )
                paragraphs[idx] = new_para
                console.print(f"  [green]Paragraph {idx + 1} rewritten[/green]")
            continue  # Write updated file for further review

        # No AI requests — ask what to do
        action = questionary.select(
            "What next?",
            choices=[
                questionary.Choice("Approve and render PDF", value="approve"),
                questionary.Choice("Continue editing", value="edit"),
            ],
        ).ask()
        if action is None:
            raise KeyboardInterrupt

        if action == "edit":
            continue

        # Polish step
        with console.status("[bold green]Polishing for cohesion and flow..."):
            polished = polish_cover_letter(paragraphs, analysis, prefs)

        if polished != paragraphs:
            _write_review_file(polished, review_path)
            console.print()
            console.print("  [bold]Polished version written to review file.[/bold]")

            use_polished = questionary.select(
                "Use polished version?",
                choices=[
                    questionary.Choice("Yes, use polished version", value="accept"),
                    questionary.Choice("No, keep my version", value="keep"),
                    questionary.Choice("Continue editing polished version", value="edit"),
                ],
            ).ask()
            if use_polished is None:
                raise KeyboardInterrupt
            if use_polished == "accept":
                paragraphs = polished
            elif use_polished == "edit":
                paragraphs = polished
                continue

        cl_content["paragraphs"] = paragraphs
        review_path.unlink(missing_ok=True)
        return cl_content


def _find_claim_sources(issues: list[dict], tailored_cv: dict) -> list[dict]:
    """Enrich validation issues with their source location in the CV structure."""
    enriched = []
    for issue in issues:
        flagged_text = issue["text"].lower()
        source = None

        # Check summary
        summary = tailored_cv.get("summary", "")
        if flagged_text in summary.lower():
            source = {"type": "summary", "text": summary, "label": "Summary"}

        # Check experience bullets
        if not source:
            for i, exp in enumerate(tailored_cv.get("experience", [])):
                for j, bullet in enumerate(exp.get("bullets", [])):
                    if flagged_text in bullet.lower():
                        source = {
                            "type": "experience",
                            "exp_idx": i,
                            "bullet_idx": j,
                            "text": bullet,
                            "label": exp.get("company", "Experience"),
                        }
                        break
                if source:
                    break

        # Check project bullets
        if not source:
            for i, proj in enumerate(tailored_cv.get("projects", [])):
                for j, bullet in enumerate(proj.get("bullets", [])):
                    if flagged_text in bullet.lower():
                        source = {
                            "type": "project",
                            "proj_idx": i,
                            "bullet_idx": j,
                            "text": bullet,
                            "label": proj.get("title", "Project"),
                        }
                        break
                if source:
                    break

        enriched.append({**issue, "source": source})
    return enriched


def _write_cv_review_file(
    enriched_issues: list[dict],
    review_path: Path,
    summary: str = "",
) -> None:
    """Write CV claim review markdown file for interactive editing."""
    lines = [
        "# CV Claim Review",
        "",
        "Review each flagged claim. Edit the text directly to fix the issue,",
        "or check the box to remove the bullet.",
        "Check 'Rewrite with AI' and add a `>` blockquote to guide the rewrite.",
        "Save the file and press Enter in the terminal when done.",
        "",
    ]
    for i, item in enumerate(enriched_issues):
        source = item.get("source")
        label = f" ({source['label']})" if source else ""
        lines.append(f"### Issue {i + 1}{label}")
        lines.append(f'**Flagged:** "{item["text"]}"')
        lines.append(f"**Reason:** {item['issue']}")
        if source and source["type"] != "summary":
            lines.append("- [ ] Remove this bullet")
            lines.append("- [ ] Rewrite with AI")
            lines.append("> (add feedback here)")
        lines.append("")

        if source:
            lines.append(source["text"])
        else:
            lines.append("_Could not locate in generated CV._")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("### Professional Summary")
    lines.append("- [ ] Rewrite with AI")
    lines.append("")
    lines.append(summary)
    lines.append("")
    lines.append("---")
    lines.append("")

    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("\n".join(lines))


def _parse_cv_review_file(review_path: Path) -> dict:
    """Parse CV review markdown.

    Returns::

        {
            "issues": list[{"remove": bool, "rewrite": bool,
                            "feedback": str | None, "text": str}],
            "redo_summary": bool,
            "summary_text": str,
            "summary_feedback": str | None,
        }
    """
    content = review_path.read_text()

    # Parse issue sections
    pattern = r"### Issue \d+.*?\n(.*?)(?=### Issue \d+|\n---|\Z)"
    matches = list(re.finditer(pattern, content, re.DOTALL))

    issues = []
    for match in matches:
        section = match.group(1)
        lines = section.split("\n")

        remove = False
        rewrite = False
        feedback_lines: list[str] = []
        collecting_feedback = False
        text_lines = []
        in_text = False

        for line in lines:
            if line.startswith("**Flagged:**") or line.startswith("**Reason:**"):
                continue
            if re.match(r"^- \[[xX]\] Remove", line):
                remove = True
                continue
            if re.match(r"^- \[ \] Remove", line):
                continue
            if re.match(r"^- \[[xX]\] Rewrite", line):
                rewrite = True
                collecting_feedback = True
                continue
            if re.match(r"^- \[ \] Rewrite", line):
                collecting_feedback = True
                continue
            if collecting_feedback and line.startswith(">"):
                fb = line.lstrip(">").strip()
                if fb and fb != "(add feedback here)":
                    feedback_lines.append(fb)
                continue
            if collecting_feedback and line.strip() == "":
                collecting_feedback = False
                in_text = True
                continue
            collecting_feedback = False
            if not in_text and line.strip() == "":
                in_text = True
                continue
            in_text = True
            text_lines.append(line)

        text = "\n".join(text_lines).strip()
        feedback = " ".join(feedback_lines) if feedback_lines else None
        issues.append({
            "remove": remove,
            "rewrite": rewrite,
            "feedback": feedback,
            "text": text,
        })

    # Parse professional summary section
    summary_match = re.search(
        r"### Professional Summary\s*\n(.*?)(?=\n---|\Z)",
        content, re.DOTALL,
    )

    redo_summary = False
    summary_text = ""
    summary_feedback = None

    if summary_match:
        section = summary_match.group(1)
        lines = section.split("\n")

        text_lines = []
        feedback_lines = []
        collecting_feedback = False
        in_text = False

        for line in lines:
            if re.match(r"^- \[[xX]\] Rewrite", line):
                redo_summary = True
                collecting_feedback = True
                continue
            if re.match(r"^- \[ \] Rewrite", line):
                collecting_feedback = True
                continue
            if collecting_feedback and line.startswith(">"):
                fb = line.lstrip(">").strip()
                if fb:
                    feedback_lines.append(fb)
                continue
            if collecting_feedback and line.strip() == "":
                collecting_feedback = False
                in_text = True
                continue
            collecting_feedback = False
            in_text = True
            text_lines.append(line)

        summary_text = "\n".join(text_lines).strip()
        summary_feedback = " ".join(feedback_lines) if feedback_lines else None

    return {
        "issues": issues,
        "redo_summary": redo_summary,
        "summary_text": summary_text,
        "summary_feedback": summary_feedback,
    }


def _apply_cv_review(
    parsed: list[dict],
    enriched: list[dict],
    tailored_cv: dict,
) -> int:
    """Apply review decisions to tailored_cv in-place. Returns number of changes."""
    changes = 0
    exp_removals: set[tuple[int, int]] = set()
    proj_removals: set[tuple[int, int]] = set()

    for item, original in zip(parsed, enriched):
        source = original.get("source")
        if not source:
            continue

        if item["remove"]:
            if source["type"] == "experience":
                exp_removals.add((source["exp_idx"], source["bullet_idx"]))
                changes += 1
            elif source["type"] == "project":
                proj_removals.add((source["proj_idx"], source["bullet_idx"]))
                changes += 1
        elif item["text"] != source["text"]:
            if source["type"] == "summary":
                tailored_cv["summary"] = item["text"]
            elif source["type"] == "experience":
                tailored_cv["experience"][source["exp_idx"]]["bullets"][source["bullet_idx"]] = item["text"]
            elif source["type"] == "project":
                tailored_cv["projects"][source["proj_idx"]]["bullets"][source["bullet_idx"]] = item["text"]
            changes += 1

    # Apply removals in reverse index order to preserve positions
    for exp_idx, bullet_idx in sorted(exp_removals, reverse=True):
        tailored_cv["experience"][exp_idx]["bullets"].pop(bullet_idx)
    for proj_idx, bullet_idx in sorted(proj_removals, reverse=True):
        tailored_cv["projects"][proj_idx]["bullets"].pop(bullet_idx)

    return changes


def _review_cv_claims(
    cv_issues: list[dict],
    tailored_cv: dict,
    company_name: str,
    analysis: dict,
) -> dict:
    """File-based CV claim review loop. Returns the (possibly modified) tailored_cv."""
    review_path = OUTPUT_DIR / company_name / "cv_claims_review.md"
    enriched = _find_claim_sources(cv_issues, tailored_cv)

    console.print()
    console.print(
        f"  [bold yellow]{len(cv_issues)} potential issue(s)[/bold yellow] flagged in generated CV."
    )

    while True:
        _write_cv_review_file(enriched, review_path, summary=tailored_cv.get("summary", ""))

        console.print(f"  [bold]Review file:[/bold] {review_path}")
        console.print(
            "  Edit text, check boxes to remove or rewrite with AI, then save the file."
        )

        try:
            input("  Press Enter when done reviewing... ")
        except EOFError:
            raise KeyboardInterrupt

        parsed = _parse_cv_review_file(review_path)
        changes = _apply_cv_review(parsed["issues"], enriched, tailored_cv)

        # Handle AI bullet rewrites
        rewrites = []
        for item, original in zip(parsed["issues"], enriched):
            source = original.get("source")
            if item.get("rewrite") and source and source["type"] in ("experience", "project"):
                rewrites.append((item, source))

        if rewrites:
            for item, source in rewrites:
                label = source.get("label", "bullet")
                with console.status(f"[bold green]Rewriting bullet ({label})..."):
                    new_text = regenerate_bullet(
                        tailored_cv, analysis,
                        source["text"], source["type"],
                        feedback=item.get("feedback"),
                    )
                if source["type"] == "experience":
                    tailored_cv["experience"][source["exp_idx"]]["bullets"][source["bullet_idx"]] = new_text
                else:
                    tailored_cv["projects"][source["proj_idx"]]["bullets"][source["bullet_idx"]] = new_text
                console.print(f"  [green]Bullet rewritten ({label})[/green]")
                changes += 1

        # Handle professional summary
        if parsed["redo_summary"]:
            with console.status("[bold green]Rewriting professional summary..."):
                new_summary = regenerate_summary(tailored_cv, analysis, parsed["summary_feedback"])
            tailored_cv["summary"] = new_summary
            console.print("  [green]Summary rewritten[/green]")
            changes += 1
        elif parsed["summary_text"] != tailored_cv.get("summary", ""):
            tailored_cv["summary"] = parsed["summary_text"]
            changes += 1

        if changes:
            console.print(f"  [green]{changes} change(s) applied[/green]")

        # If AI rewrites happened, loop back for further review
        if rewrites:
            enriched = _find_claim_sources(cv_issues, tailored_cv)
            enriched = [e for e in enriched if e.get("source") is not None]
            if not enriched:
                console.print("  [green]All flagged items resolved[/green]")
                break
            continue

        action = questionary.select(
            "What next?",
            choices=[
                questionary.Choice("Continue to rendering", value="continue"),
                questionary.Choice("Keep editing", value="edit"),
            ],
        ).ask()
        if action is None:
            raise KeyboardInterrupt

        if action == "continue":
            break

        # Refresh sources for next iteration
        enriched = _find_claim_sources(cv_issues, tailored_cv)
        enriched = [e for e in enriched if e.get("source") is not None]
        if not enriched:
            console.print("  [green]All flagged items resolved[/green]")
            break

    review_path.unlink(missing_ok=True)
    return tailored_cv


def _process_posting(master: dict) -> None:
    """Process a single job posting: analyze, tailor, render."""
    # Step 1: Get job posting
    posting = get_job_posting()
    console.print()

    # Step 2: Analyze posting
    with console.status("[bold green]Analyzing posting (Claude Haiku)..."):
        try:
            analysis = analyze_posting(posting)
        except Exception as e:
            console.print(f"[red]Failed to analyze posting: {e}[/red]")
            return

    console.print("  [dim]Analysis complete[/dim]")
    display_analysis(analysis)

    # Skip recommendation gate
    rec = analysis.get("fit_recommendation", "apply")
    if rec == "skip":
        proceed = questionary.confirm(
            "Low fit score — skip this posting?", default=True
        ).ask()
        if proceed is None:
            raise KeyboardInterrupt
        if proceed:
            return
    elif rec == "consider":
        proceed = questionary.confirm(
            "Moderate fit — proceed anyway?", default=True
        ).ask()
        if proceed is None:
            raise KeyboardInterrupt
        if not proceed:
            return

    detected_company = analysis.get("company", "company")
    company_name = questionary.text(
        "Company name for output folder/PDF?",
        default=detected_company,
    ).ask()
    if company_name is None:
        raise KeyboardInterrupt
    company_name = company_name.strip() or detected_company
    analysis["company"] = company_name

    # Skill gap check — ask about missing competencies
    additions = check_skill_gaps(analysis, master)
    if additions:
        for item in additions:
            for cat in master["skills"]:
                if cat["category"] == item["category"]:
                    # Mark as user-claimed so the LLM treats it with less authority
                    cat["items"].append(f"{item['skill']} (source: user_claimed)")
                    break
        # Persist updated master CV
        MASTER_CV_PATH.write_text(yaml.dump(master, default_flow_style=False, allow_unicode=True, sort_keys=False))
        console.print(f"[green]Added {len(additions)} skill(s) to master_cv.yaml (marked as user-claimed)[/green]")

    # Required applicant info (salary, start date, reference number, etc.)
    required_info = get_required_info(analysis)

    # Step 3: CV preferences (profile type, framing, projects, cover letter yes/no)
    cv_prefs = get_cv_preferences(analysis)
    console.print()

    # Step 4: Start CV generation and company research in parallel
    console.print("  [dim]Starting CV generation...[/dim]")
    with ThreadPoolExecutor(max_workers=2) as executor:
        cv_future = executor.submit(generate_tailored_cv, analysis, cv_prefs)

        # Kick off company research early (runs while user answers CL prefs)
        research_future = None
        if cv_prefs.get("include_cover_letter") and not analysis.get("is_recruiter"):
            research_future = executor.submit(research_company, analysis, master)

        # Ask CL preferences while CV generates (research results fed in)
        if cv_prefs["include_cover_letter"]:
            # Wait for research to finish before CL prefs (it's fast, Haiku)
            research_result = {}
            if research_future is not None:
                with console.status("[bold green]Researching company..."):
                    try:
                        research_result = research_future.result()
                    except Exception:
                        pass

            cl_prefs = get_cl_preferences(analysis, research=research_result)
            prefs = {**cv_prefs, **cl_prefs}
            # Pass company research through for grounding in cover letter
            if research_result:
                prefs["company_research"] = {
                    "summary": research_result.get("summary", ""),
                    "verified": research_result.get("verified", False),
                }
        else:
            prefs = cv_prefs

        if required_info:
            prefs["required_info"] = required_info

        # Wait for CV generation to finish
        with console.status("[bold green]Waiting for CV generation (Claude)..."):
            try:
                tailored_cv = cv_future.result()
            except Exception as e:
                console.print(f"[red]Failed to generate CV: {e}[/red]")
                return

    console.print("  [dim]CV content generated[/dim]")

    # Step 4b: Validate CV claims against master CV
    with console.status("[bold green]Validating CV claims..."):
        cv_issues = validate_claims(tailored_cv, master, doc_type="cv")
    if cv_issues:
        tailored_cv = _review_cv_claims(cv_issues, tailored_cv, company_name, analysis)

    # Step 5: Render CV PDF (with interactive page fitting)
    cv_data = _build_cv_render_data(master, tailored_cv, company_name, required_info)

    with console.status("[bold green]Rendering CV (page check)..."):
        try:
            doc, cv_output_path = render_cv_doc(cv_data)
        except Exception as e:
            console.print(f"[red]Failed to render CV: {e}[/red]")
            return

    current_data = cv_data
    while len(doc.pages) > 1:
        # Save preview PDF so user can inspect before deciding
        preview_path = cv_output_path.with_name(cv_output_path.stem + " - PREVIEW.pdf")
        doc.write_pdf(str(preview_path))
        console.print(
            f"[yellow]CV overflows to {len(doc.pages)} pages — needs to fit on 1.[/yellow]"
        )
        console.print(f"  [dim]Preview saved:[/dim] {preview_path}")

        available = get_available_reductions(current_data)
        if not available:
            console.print("[red]No more reductions available. Rendering as-is.[/red]")
            break

        choices = [
            questionary.Choice(r["label"], value=r["id"], checked=True)
            for r in available
        ]
        selected = questionary.checkbox(
            "Which content to remove? (all recommended, uncheck to keep)",
            choices=choices,
        ).ask()
        if selected is None:
            raise KeyboardInterrupt

        if selected:
            current_data = apply_reductions(current_data, selected, available)

        with console.status("[bold green]Re-rendering CV (page check)..."):
            doc, cv_output_path = render_cv_doc(current_data)

    # Clean up preview if it exists
    preview_path = cv_output_path.with_name(cv_output_path.stem + " - PREVIEW.pdf")
    if preview_path.exists():
        preview_path.unlink()

    with console.status("[bold green]Writing CV PDF..."):
        doc.write_pdf(str(cv_output_path))
    cv_path = cv_output_path

    console.print(f"  [green]CV:[/green]           {cv_path}")

    # Step 6: Generate and review cover letter (if requested)
    if prefs.get("include_cover_letter"):
        with console.status("[bold green]Generating cover letter (Claude)..."):
            try:
                cl_content = generate_cover_letter(analysis, tailored_cv, prefs)
            except Exception as e:
                console.print(f"[red]Failed to generate cover letter: {e}[/red]")
                return

        console.print("  [dim]Cover letter generated[/dim]")

        # Step 6a: Validate cover letter claims
        with console.status("[bold green]Validating cover letter claims..."):
            cl_issues = validate_claims(cl_content, master, doc_type="cover_letter")

        # Step 6b: File-based review (issues shown in review file)
        cl_content = _review_cover_letter(
            cl_content, analysis, tailored_cv, prefs, company_name,
            issues=cl_issues or None,
        )

        # Step 7: Render approved cover letter
        with console.status("[bold green]Rendering cover letter PDF..."):
            try:
                cl_data = _build_cl_render_data(master, cl_content, company_name, required_info)
                cl_path = render_cover_letter(cl_data)
            except Exception as e:
                console.print(f"[red]Failed to render cover letter PDF: {e}[/red]")
                return

        console.print(f"  [green]Cover letter:[/green]  {cl_path}")

        # Step 8: Offer to save cover letter to Blocks.md
        role_title = analysis.get("role_title", "Unknown Role")
        save = questionary.confirm(
            "Save this cover letter to Blocks.md for future reference?",
            default=True,
        ).ask()

        if save:
            _append_to_blocks(cl_content, role_title)
            console.print(f"[green]  Saved to:[/green]     {BLOCKS_PATH}")

    console.print()
    console.print("[bold green]Done![/bold green]")


def main():
    # Handle --sync flag: sync and exit
    if "--sync" in sys.argv:
        console.print(Panel("[bold green]CV Tailor — Sync[/bold green]", border_style="green"))
        console.print()
        _run_sync()
        return

    console.print(Panel("[bold green]CV Tailor[/bold green]", border_style="green"))
    console.print()

    # Step 0: Check if rewrite files are newer than master
    _check_rewrite_freshness()

    master = _load_master_cv()

    while True:
        try:
            _process_posting(master)
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled.[/yellow]")
            break

        console.print()
        another = questionary.confirm("Process another posting?", default=True).ask()
        if not another:
            break
        console.print()


if __name__ == "__main__":
    main()
