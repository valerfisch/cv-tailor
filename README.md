# CV Tailor

Interactive CLI tool that generates tailored CVs and cover letters as PDFs. Paste a job posting, answer a few questions, get application-ready documents.

## How it works

1. You paste a job posting into the terminal
2. Claude (haiku) analyzes the posting: role, required skills, fit score, red flags
3. Skill gaps are identified and you can add missing skills to your master CV on the fly
4. You answer interactive questions (profile type, framing, projects, cover letter tone)
5. Company research runs via DuckDuckGo search, synthesized by Claude (haiku) into a summary with a verified/unverified flag
6. Claude (sonnet) selects and reframes relevant content from your master CV
7. Generated CV claims are validated against your master CV to catch hallucinated skills or inflated metrics
8. You review flagged issues in a markdown file: edit text directly, remove bullets, or request AI rewrites
9. CV is rendered to PDF with interactive page fitting (suggests content reductions if it overflows one page)
10. Claude (sonnet) writes a cover letter grounded in verified company research
11. Cover letter claims are validated the same way, then you review and polish via a markdown file
12. Both documents are rendered to PDF matching your CV design

All CV data lives in `master_cv.yaml`, a tagged superset of all your experience, projects, skills, and education. The AI selects from this based on the posting, so you never lose content between versions.

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd cv-tailor

python3 -m venv .venv
source .venv/bin/activate        # bash/zsh
# source .venv/bin/activate.fish  # fish

pip install -r requirements.txt
```

### 2. API key

You need an Anthropic API key. Get one at https://console.anthropic.com/.

Set it as an environment variable, **never commit it to a file**:

```bash
# bash/zsh — add to ~/.bashrc or ~/.zshrc
export ANTHROPIC_API_KEY="sk-ant-..."

# fish — add to ~/.config/fish/config.fish
set -gx ANTHROPIC_API_KEY "sk-ant-..."
```

### 3. Create your personal data files

The tool reads all personal data from local YAML files that are **gitignored** (never committed). Copy the sample files and fill in your own information:

```bash
cp master_cv.sample.yaml master_cv.yaml
cp motivations.sample.yaml motivations.yaml
cp personal_notes.sample.yaml personal_notes.yaml   # optional
```

- **`master_cv.yaml`** -- Your complete CV data: contact info, experience, skills, education, projects. See the sample file for the expected structure. This is the core data source.
- **`motivations.yaml`** -- Domain-specific motivations for the "why this company?" prompt. Gets auto-updated as you use the tool.
- **`personal_notes.yaml`** *(optional)* -- Personal anecdotes and background context used for cover letter company research.

### 4. (Optional) CV rewrite baselines

You can place manually curated CV markdown files in the `cvs/` folder following this naming pattern:

```
cvs/CV - Machine Learning Engineer - REWRITE.md
cvs/CV - Senior Full-Stack Developer - REWRITE.md
```

These serve as baseline references for Claude when generating tailored CVs. The `cvs/` folder is gitignored.

## Usage

```bash
.venv/bin/python tailor.py
```

Then:
- Paste the job posting text
- Press **Ctrl+D** to submit (Ctrl+Z Enter on Windows)
- Answer the interactive prompts
- Review and edit generated content in markdown review files
- PDFs appear in `output/`

### Sync mode

If you edit your CV rewrite markdown files directly, sync changes back to `master_cv.yaml`:

```bash
.venv/bin/python tailor.py --sync
```

## Output

Generated files land in `output/<company-name>/`:
```
output/
└── Acme Corp/
    ├── Jane Doe CV.pdf
    └── Jane Doe CL.pdf
```

## Anti-hallucination safeguards

The tool includes several layers to prevent fabricated content:

- **Web-grounded company research**: DuckDuckGo search provides real company data. Results include a `verified` flag so the cover letter generator knows what is confirmed vs. inferred from the posting alone.
- **Claim validation**: After generation, a separate Haiku call cross-checks every bullet and paragraph against your master CV data. It flags skills not in your CV, inflated metrics, and fabricated achievements.
- **Interactive review**: Flagged issues are presented in a markdown file where you can edit text, remove bullets, or request targeted AI rewrites with feedback.
- **Grounded rewrites**: When you request an AI rewrite of a bullet or paragraph, the rewriter receives your master CV as ground truth and is instructed not to fabricate.

## Cost

~$0.05-0.10 per run (haiku calls for analysis, research, and validation + sonnet calls for CV and cover letter generation).

## Project structure

```
cv-tailor/
├── tailor.py                    # CLI entry point + review/render orchestration
├── master_cv.yaml               # Your CV data (gitignored)
├── master_cv.sample.yaml        # Sample CV data (committed)
├── motivations.yaml             # Domain motivations (gitignored)
├── motivations.sample.yaml      # Sample motivations (committed)
├── personal_notes.yaml          # Personal anecdotes (gitignored, optional)
├── personal_notes.sample.yaml   # Sample notes (committed)
├── requirements.txt
├── lib/
│   ├── analyzer.py              # Job posting analysis + company research (Claude haiku)
│   ├── generator.py             # CV + cover letter generation + claim validation (Claude sonnet/haiku)
│   ├── renderer.py              # Jinja2 + WeasyPrint PDF rendering + page fitting
│   ├── interactive.py           # Terminal Q&A flow + skill gap detection
│   └── sync.py                  # Sync rewrite files → master_cv.yaml
├── templates/
│   ├── cv.html.j2               # CV layout template
│   ├── cover_letter.html.j2     # Cover letter layout template
│   └── style.css                # Print-optimized CSS (A4)
├── prompts/                     # System prompts for each API call
├── cvs/                         # CV rewrite baselines (gitignored)
├── research/                    # Research papers/PDFs (gitignored)
└── output/                      # Generated PDFs (gitignored)
```

## Customization

- **Add experience/projects**: Edit `master_cv.yaml`. Use tags and priority fields so the AI knows what to select.
- **Change CV design**: Edit `templates/style.css` and the `.html.j2` templates.
- **Adjust AI behavior**: Edit the system prompts in `prompts/`.
- **Cover letter style**: Edit `prompts/cover_letter.txt` to match your writing voice.
- **Highlight blocks and thesis angles**: Configure in `master_cv.yaml` under `cover_letter:` to control cover letter content selection.

## Keeping master_cv.yaml up to date

The master CV is only as good as its data. Update it when:

- **New project/repo** -- add or update the relevant entry in `projects:` with concrete metrics
- **New job/role** -- add a new entry under `experience:` with tagged bullets
- **New skills/tools** -- add to the relevant `skills:` category
- **CV rewrite changes** -- if you refine bullet wording in the markdown CV drafts, run `--sync` to pull changes back
