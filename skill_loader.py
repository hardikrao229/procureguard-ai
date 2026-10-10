"""
skill_loader.py - reads Agent Skill files (SKILL.md).

A SKILL.md has two parts (Lab 10):
  1. YAML metadata between the two '---' lines (name, version, allowed_tools, ...)
  2. The procedural Markdown body (role, guardrails, phases, output schema)

Only the skill the user picks is loaded into the AI - this is "progressive disclosure":
the model never sees the other agents' instructions, which keeps prompts small and focused.
"""
from pathlib import Path

import yaml

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def load_skill(path) -> dict:
    """Return {'meta': {...yaml...}, 'body': '...markdown...', 'path': Path}."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{path} has no YAML metadata block at the top")
    _, yaml_block, body = text.split("---", 2)
    meta = yaml.safe_load(yaml_block)
    for field in ("name", "description", "version", "allowed_tools"):
        if field not in meta:
            raise ValueError(f"{path} is missing the '{field}' field in its YAML metadata")
    return {"meta": meta, "body": body.strip(), "path": path}


def list_skills() -> list:
    """All active skills: skills/<skill-name>/SKILL.md (the archive folder is ignored)."""
    return [load_skill(p) for p in sorted(SKILLS_DIR.glob("*/SKILL.md"))]


if __name__ == "__main__":
    # Quick check: python skill_loader.py
    for s in list_skills():
        m = s["meta"]
        print(f"{m['name']} v{m['version']} - {len(m['allowed_tools'])} tools allowed")
