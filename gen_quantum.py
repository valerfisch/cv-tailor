#!/usr/bin/env python3
"""One-shot CV generation for Quantum IT Innovation (no interactive prompts)."""

import json
import re
import sys
from pathlib import Path

import yaml

from lib.analyzer import research_company
from lib.generator import generate_tailored_cv, validate_claims
from lib.renderer import render_cv, render_cv_doc, apply_reductions, get_available_reductions

MASTER_CV_PATH = Path(__file__).parent / "master_cv.yaml"

def _load_master_cv() -> dict:
    return yaml.safe_load(MASTER_CV_PATH.read_text())

def _strip_metadata_tags(skills: list[dict]) -> list[dict]:
    cleaned = []
    for cat in skills:
        items = [re.sub(r"\s*\(source:\s*\w+\)", "", item) for item in cat.get("items", [])]
        cleaned.append({**cat, "items": items})
    return cleaned


def main():
    master = _load_master_cv()

    # Synthetic analysis based on deep research of quantumitinnovation.com
    analysis = {
        "role_title": "Senior AI & Full-Stack Developer",
        "company": "Quantum IT Innovation",
        "company_slug": "quantum-it-innovation",
        "must_have_skills": [
            "Python", "PyTorch", "TensorFlow",
            "Generative AI", "LLM development", "AI Agent development",
            "NLP", "Deep Learning",
            "Node.js", "PHP", "React",
            "REST APIs", "Microservices",
            "Docker", "CI/CD",
            "PostgreSQL", "SQL",
            "UI/UX Design"
        ],
        "nice_to_have_skills": [
            "Prompt Engineering", "RAG Pipelines", "Multi-agent workflows",
            "Computer Vision", "Chatbot development",
            "Ruby on Rails", ".NET",
            "IoT", "Robotic Process Automation",
            "Kubernetes", "Cloud deployment",
            "Data Engineering", "Data Mining",
            "Mobile app development"
        ],
        "experience_years": "5+",
        "key_responsibilities": [
            "Design, develop, and deploy AI/ML solutions including generative AI, LLM applications, and AI agents for enterprise clients",
            "Build and maintain full-stack web applications using modern frameworks (React, Node.js, PHP) with robust REST APIs",
            "Architect scalable microservices and data pipelines for AI-driven products",
            "Lead UI/UX design and development for client-facing digital platforms",
            "Collaborate with cross-functional teams across global offices to deliver end-to-end digital transformation solutions"
        ],
        "domain": "enterprise software / AI consulting / digital transformation",
        "role_type": "hybrid",
        "is_recruiter": False,
        "seniority": "senior",
        "culture_signals": [
            "Innovation-driven",
            "Global collaboration across USA, UK, Canada, Australia, India",
            "Client-facing delivery",
            "Digital transformation focus",
            "Rapid prototyping and agile delivery"
        ],
        "requires_chinese": False,
        "red_flags": [],
        "tone": "formal",
        "fit_score": 9,
        "fit_recommendation": "apply",
        "fit_reasoning": "Strong match across AI/ML, full-stack development, enterprise delivery, and UI/UX design with proven production experience.",
        "required_applicant_info": []
    }

    # User preferences: hybrid profile to showcase both AI and full-stack
    user_prefs = {
        "profile_type": "hybrid",
        "include_projects": True,
        "include_awards": True,
        "include_cover_letter": False,
        "framing": "Position as a senior AI and full-stack engineer who bridges ML research and production web development, with proven enterprise delivery experience and award-winning UI/UX skills.",
    }

    print("Generating tailored CV for Quantum IT Innovation...")
    tailored_cv = generate_tailored_cv(analysis, user_prefs)
    print("  CV content generated.")

    # Validate claims
    print("Validating claims...")
    issues = validate_claims(tailored_cv, master, doc_type="cv")
    if issues:
        print(f"  {len(issues)} issue(s) flagged:")
        for issue in issues:
            print(f"    - {issue['text']}: {issue['issue']}")
    else:
        print("  No issues found.")

    # Build render data
    cv_data = {
        "personal": master["personal"],
        "title": tailored_cv["title"],
        "summary": tailored_cv["summary"],
        "skills": _strip_metadata_tags(tailored_cv["skills"]),
        "experience": tailored_cv["experience"],
        "projects": tailored_cv.get("projects", []),
        "education": tailored_cv["education"],
        "company_name": "Quantum IT Innovation",
        "header_notes": [],
    }

    # Add awards if included
    if tailored_cv.get("include_awards") and tailored_cv.get("awards"):
        cv_data["awards"] = tailored_cv["awards"]

    # --- Post-processing: fix claims, curate content, enforce order ---

    # Fix known claim: straightlabs backend bullet uses Java not Node.js
    for exp in cv_data["experience"]:
        if exp.get("company") == "straightlabs":
            exp["bullets"] = [
                b.replace("(Node.js, Kotlin, Golang)", "(Java, Kotlin, Golang)")
                 .replace("(Docker, Kubernetes)", "")
                 .replace("(Docker)", "")
                for b in exp["bullets"]
            ]

    # Curate freelance bullets: LLM bullet is most relevant for this company
    llm_bullet = (
        "Built multi-agent LLM pipeline using Claude API with structured prompt "
        "engineering, JSON schema validation, and iterative refinement loops for "
        "automated document generation"
    )
    hydro_bullet = (
        "Building IoT-enabled hydroponics system: React frontend, PHP backend, "
        "PostgreSQL, custom PCB with ESP32 microcontrollers (embedded C), ZeroMQ "
        "for device-server communication"
    )
    for exp in cv_data["experience"]:
        if exp.get("company") == "Self-employed":
            # Ensure LLM bullet comes first, keep hydroponics for full-stack proof
            exp["bullets"] = [llm_bullet, hydro_bullet]

    # Enforce chronological order (most recent first)
    order = {"Self-employed": 0, "BMW Group": 1, "straightlabs": 2, "Frisch & Veg": 3}
    cv_data["experience"].sort(key=lambda e: order.get(e.get("company", ""), 99))

    # Ensure straightlabs has enough bullets to show enterprise depth
    sl_backend = (
        "Architected data-intensive backend systems (Java, Kotlin, Golang) "
        "with microservices, REST APIs, and SQL/NoSQL databases for enterprise clients"
    )
    sl_cicd = (
        "Implemented CI/CD pipelines and containerized deployments; "
        "owned full product lifecycle from MVP through production"
    )
    for exp in cv_data["experience"]:
        if exp.get("company") == "straightlabs":
            existing = [b.lower() for b in exp["bullets"]]
            if not any("backend" in b or "architected" in b for b in existing):
                exp["bullets"].append(sl_backend)
            if not any("ci/cd" in b or "lifecycle" in b for b in existing):
                exp["bullets"].append(sl_cicd)
            # Cap at 4 to fit page
            exp["bullets"] = exp["bullets"][:4]

    # Cap BMW bullets at 3 most relevant
    for exp in cv_data["experience"]:
        if exp.get("company") == "BMW Group":
            exp["bullets"] = exp["bullets"][:3]

    # Remove projects section entirely (not needed, experience is stronger)
    cv_data["projects"] = []

    # Cap skills to 4 most relevant categories to save vertical space
    keep_categories = {"ML & AI", "Backend", "Frontend & UI/UX", "Frontend",
                       "Data & Infrastructure"}
    cv_data["skills"] = [
        s for s in cv_data["skills"] if s["category"] in keep_categories
    ][:4]

    # Render and check page count
    print("Rendering CV (page check)...")
    doc, cv_output_path = render_cv_doc(cv_data)

    current_data = cv_data
    attempt = 0
    while len(doc.pages) > 1 and attempt < 10:
        attempt += 1
        print(f"  CV is {len(doc.pages)} pages, applying one reduction (attempt {attempt})...")
        available = get_available_reductions(current_data)
        if not available:
            print("  No more reductions available.")
            break
        # Prioritize reductions: trim bullets and remove projects before
        # removing entire skill categories or education
        priority_order = [
            "remove_proj:",      # remove projects first
            "trim_pos:",         # trim specific positions
            "remove_bachelor",   # drop bachelor
            "drop_additional",   # drop languages/visa
            "reduce_all_by_one", # trim all by one bullet
            "remove_exp:",       # remove experience entries
            "skill:",            # remove skill categories last
        ]
        pick = available[0]  # fallback
        for prefix in priority_order:
            matches = [r for r in available if r["id"].startswith(prefix)]
            if matches:
                pick = matches[0]
                break
        print(f"    Applying: {pick['label']}")
        current_data = apply_reductions(current_data, [pick["id"]], available)
        doc, cv_output_path = render_cv_doc(current_data)

    print("Writing final PDF...")
    doc.write_pdf(str(cv_output_path))
    print(f"  Done! CV saved to: {cv_output_path}")


if __name__ == "__main__":
    main()
