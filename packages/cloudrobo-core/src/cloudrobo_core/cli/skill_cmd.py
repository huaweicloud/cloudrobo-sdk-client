import os
import shutil
from pathlib import Path

import click

from .skill_loader import _parse_frontmatter


TARGET_MAP = {
    "claude-code": "~/.claude/skills",
    "claude-code-project": ".claude/skills",
    "jiuwenswarm": "~/.jiuwenswarm/agent/workspace/skills",
}


def _resolve_target(target: str) -> Path:
    if target.startswith("path:"):
        return Path(target[5:]).expanduser().resolve()

    if target not in TARGET_MAP:
        click.echo(f"Unknown target: {target}", err=True)
        click.echo(f"Supported targets: {', '.join(TARGET_MAP.keys())}, path:/custom/dir", err=True)
        return None

    if target == "jiuwenswarm":
        data_dir = os.environ.get("JIUWENSWARM_DATA_DIR")
        if data_dir:
            return Path(data_dir) / "agent" / "workspace" / "skills"

    return Path(TARGET_MAP[target]).expanduser().resolve()


def _list_skills_from(skills_root: Path) -> list:
    skills = []
    for skill_dir in sorted(skills_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        meta = _parse_frontmatter(skill_md)
        skills.append({
            "name": meta.get("name", skill_dir.name),
            "description": meta.get("description", ""),
            "dir": str(skill_dir),
        })
    return skills


@click.group(name="skill")
def skill():
    """Skill management commands"""
    pass


@skill.command("install")
@click.option("--target", required=True,
              help="Target platform (claude-code, claude-code-project, jiuwenswarm) or custom path (path:/dir)")
@click.option("--source", required=True,
              help="Skill source directory")
@click.option("--skill-name", default=None,
              help="Specific skills to install (comma-separated). Default: all skills")
def install(target, source, skill_name):
    """Install skills to agent platform directories"""
    target_dir = _resolve_target(target)
    if target_dir is None:
        return

    target_dir = Path(target_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    skills_root = Path(source).expanduser().resolve()
    if not skills_root.exists():
        click.echo(f"Source directory not found: {skills_root}", err=True)
        return
    skills = _list_skills_from(skills_root)

    if skill_name:
        skill_names = [n.strip() for n in skill_name.split(",") if n.strip()]
        skills = [s for s in skills if s["name"] in skill_names]
        missing = set(skill_names) - {s["name"] for s in skills}
        if missing:
            click.echo(f"Skills not found: {', '.join(missing)}", err=True)

    if not skills:
        click.echo("No matching skills to install.")
        return

    installed = []
    for s in skills:
        src = Path(s["dir"])
        dst = target_dir / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        installed.append(s["name"])

    click.echo(f"Installed {len(installed)} skill(s) to {target_dir}:")
    for name in installed:
        click.echo(f"  {name}")


@skill.command("uninstall")
@click.option("--target", required=True,
              help="Target platform (claude-code, claude-code-project, jiuwenswarm) or custom path (path:/dir)")
@click.option("--skill-name", default=None,
              help="Specific skills to uninstall (comma-separated). Default: all skills")
def uninstall(target, skill_name):
    """Uninstall skills from agent platform directories"""
    target_dir = _resolve_target(target)
    if target_dir is None:
        return

    target_dir = Path(target_dir).expanduser().resolve()
    if not target_dir.exists():
        click.echo(f"Target directory does not exist: {target_dir}")
        return

    if skill_name:
        skills_to_remove = [n.strip() for n in skill_name.split(",") if n.strip()]
    else:
        skills_to_remove = [d.name for d in target_dir.iterdir() if d.is_dir()]

    removed = []
    for name in skills_to_remove:
        skill_path = target_dir / name
        if skill_path.exists():
            shutil.rmtree(skill_path)
            removed.append(name)

    if removed:
        click.echo(f"Uninstalled {len(removed)} skill(s) from {target_dir}:")
        for name in removed:
            click.echo(f"  {name}")
    else:
        click.echo("No skills to uninstall.")
