#!/usr/bin/env python3
"""CV Tailor — Interactive CLI for generating tailored CVs and cover letters."""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import questionary
import yaml
from rich.console import Console
from rich.panel import Panel

from lib.analyzer import analyze_posting, research_company
from lib.generator import generate_cover_letter, generate_tailored_cv, regenerate_paragraph
from lib.interactive import check_skill_gaps, console, display_analysis, get_cl_preferences, get_cv_preferences, get_job_posting
from lib.renderer import apply_reductions, get_available_reductions, render_cover_letter, render_cv, render_cv_doc
from lib.sync import sync_master_cv

MASTER_CV_PATH = Path(__file__).parent / "master_cv.yaml"
BLOCKS_PATH = Path(__file__).parent / "Blocks.md"
REWRITE_DIR = Path(__file__).parent / "cvs"  # where CV - * - REWRITE.md files live


def _load_master_cv() -> dict:
    return yaml.safe_load(MASTER_CV_PATH.read_text())


def _build_cv_render_data(master: dict, tailored: dict, company_name: str) -> dict:
    """Assemble the data dict expected by cv.html.j2."""
    return {
        "personal": master["personal"],
        "title": tailored["title"],
        "summary": tailored["summary"],
        "skills": tailored["skills"],
        "experience": tailored["experience"],
        "projects": tailored.get("projects", []),
        "education": tailored["education"],
        "company_name": company_name,
    }


def _build_cl_render_data(master: dict, cl: dict, company_name: str) -> dict:
    """Assemble the data dict expected by cover_letter.html.j2."""
    return {
        "personal": master["personal"],
        "date": date.today().strftime("%B %d, %Y"),
        "recipient": cl.get("recipient", ""),
        "subject": cl.get("subject", ""),
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


def _review_cover_letter(cl_content: dict, analysis: dict, tailored_cv: dict, prefs: dict) -> dict:
    """Paragraph-by-paragraph review. Returns the approved cover letter dict."""
    paragraphs = list(cl_content.get("paragraphs", []))

    while True:
        console.print()
        console.print("[bold]Cover letter review[/bold]")
        console.print()

        # Display each paragraph numbered
        for i, para in enumerate(paragraphs):
            console.print(Panel(
                para,
                title=f"[bold]Paragraph {i + 1}[/bold]",
                border_style="cyan",
            ))

        action = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice("Approve all — render PDF", value="approve"),
                questionary.Choice("Edit a paragraph (manual rewrite)", value="edit"),
                questionary.Choice("Regenerate a paragraph (Claude rewrites it)", value="regenerate"),
                questionary.Choice("Regenerate entire cover letter", value="regenerate_all"),
            ],
        ).ask()

        if action is None:
            raise KeyboardInterrupt

        if action == "approve":
            cl_content["paragraphs"] = paragraphs
            return cl_content

        if action == "edit":
            idx = _pick_paragraph(paragraphs)
            if idx is None:
                continue
            console.print(f"\n[dim]Current text:[/dim]\n{paragraphs[idx]}\n")
            new_text = questionary.text(
                "New text (paste replacement):",
            ).ask()
            if new_text and new_text.strip():
                paragraphs[idx] = new_text.strip()
                console.print("[green]Updated.[/green]")

        elif action == "regenerate":
            idx = _pick_paragraph(paragraphs)
            if idx is None:
                continue
            feedback = questionary.text(
                "What's wrong with it? (guidance for rewrite, or Enter to just redo):",
            ).ask()
            if feedback is None:
                raise KeyboardInterrupt
            with console.status("[bold green]Regenerating paragraph..."):
                new_para = regenerate_paragraph(
                    paragraphs, idx, feedback.strip() or None,
                    analysis, tailored_cv, prefs,
                )
            console.print(Panel(
                new_para,
                title=f"[green]New paragraph {idx + 1}[/green]",
                border_style="green",
            ))
            use_it = questionary.confirm("Use this version?", default=True).ask()
            if use_it:
                paragraphs[idx] = new_para

        elif action == "regenerate_all":
            with console.status("[bold green]Regenerating cover letter..."):
                try:
                    cl_content = generate_cover_letter(analysis, tailored_cv, prefs)
                except Exception as e:
                    console.print(f"[red]Failed: {e}[/red]")
                    continue
            paragraphs = list(cl_content.get("paragraphs", []))
            console.print("[green]Regenerated.[/green]")


def _pick_paragraph(paragraphs: list[str]) -> int | None:
    """Let the user pick which paragraph to act on."""
    choices = [
        questionary.Choice(
            f"Paragraph {i + 1}: {p[:60]}{'...' if len(p) > 60 else ''}",
            value=i,
        )
        for i, p in enumerate(paragraphs)
    ]
    idx = questionary.select("Which paragraph?", choices=choices).ask()
    return idx


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
                    cat["items"].append(item["skill"])
                    break
        # Persist updated master CV
        MASTER_CV_PATH.write_text(yaml.dump(master, default_flow_style=False, allow_unicode=True, sort_keys=False))
        console.print(f"[green]Added {len(additions)} skill(s) to master_cv.yaml[/green]")

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
        else:
            prefs = cv_prefs

        # Wait for CV generation to finish
        with console.status("[bold green]Waiting for CV generation (Claude)..."):
            try:
                tailored_cv = cv_future.result()
            except Exception as e:
                console.print(f"[red]Failed to generate CV: {e}[/red]")
                return

    console.print("  [dim]CV content generated[/dim]")

    # Step 5: Render CV PDF (with interactive page fitting)
    cv_data = _build_cv_render_data(master, tailored_cv, company_name)

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

        # Step 6b: Paragraph-by-paragraph review
        cl_content = _review_cover_letter(cl_content, analysis, tailored_cv, prefs)

        # Step 7: Render approved cover letter
        with console.status("[bold green]Rendering cover letter PDF..."):
            try:
                cl_data = _build_cl_render_data(master, cl_content, company_name)
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
