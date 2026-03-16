"""Sync master_cv.yaml from CV rewrite markdown files using Claude."""

import json
from pathlib import Path

import anthropic
import yaml

MASTER_CV_PATH = Path(__file__).parent.parent / "master_cv.yaml"
REWRITE_DIR = Path(__file__).parent.parent / "cvs"  # where CV - * - REWRITE.md files live

SYNC_SYSTEM_PROMPT = """\
You are updating a master_cv.yaml file based on changes from CV rewrite markdown files.

The master_cv.yaml is the single source of truth for all CV data. The rewrite files are \
manually curated CVs that may contain updated bullet text, new roles, changed titles, \
updated personal info, or new skills.

Your job:
1. Compare the rewrite files against the current master_cv.yaml
2. Update master_cv.yaml to reflect any changes found in the rewrite files:
   - Updated bullet text (preserve the tag and priority metadata, just update the text)
   - New bullets (assign reasonable tags and priority)
   - Changed role titles, dates, company names
   - Updated personal info (contact, visa, etc.)
   - New or updated skills
   - Updated summaries
   - New experience entries or projects
3. Do NOT remove anything from master_cv.yaml that exists there but not in a rewrite \
(the master is a superset — rewrites are subsets)
4. Preserve the existing YAML structure, tags, and priority fields
5. If a bullet in a rewrite is a rephrased version of an existing master bullet, update \
the master bullet text while keeping its tags and priority

Return the complete updated master_cv.yaml content as valid YAML. Return ONLY the YAML, \
no other text, no code fences."""


def sync_master_cv() -> None:
    """Read rewrite files + current master, use Claude to produce updated master."""
    current_master = MASTER_CV_PATH.read_text()

    rewrite_contents = {}
    for rewrite_path in sorted(REWRITE_DIR.glob("CV - * - REWRITE.md")):
        rewrite_contents[rewrite_path.name] = rewrite_path.read_text()

    if not rewrite_contents:
        return

    user_content = f"## Current master_cv.yaml\n```yaml\n{current_master}\n```\n\n"
    for name, content in rewrite_contents.items():
        user_content += f"## Rewrite file: {name}\n```markdown\n{content}\n```\n\n"
    user_content += "Update master_cv.yaml to incorporate any changes from the rewrite files."

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        system=SYNC_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )

    new_yaml = response.content[0].text.strip()
    # Strip code fences if Claude added them
    if new_yaml.startswith("```"):
        new_yaml = new_yaml.split("\n", 1)[1]
        new_yaml = new_yaml.rsplit("```", 1)[0]

    # Validate it's parseable YAML
    yaml.safe_load(new_yaml)

    # Write backup then update
    backup_path = MASTER_CV_PATH.with_suffix(".yaml.bak")
    backup_path.write_text(current_master)
    MASTER_CV_PATH.write_text(new_yaml)
