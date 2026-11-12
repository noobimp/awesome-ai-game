#!/usr/bin/env python3
"""Check the catalog, prompt files, screenshots, and generated README."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render_readme  # noqa: E402

REQUIRED = [
    "id",
    "name",
    "author",
    "author_url",
    "category",
    "stack",
    "project_url",
    "game_url",
    "screenshot",
    "model",
    "prompt",
    "budget",
    "conversation_url",
    "created_at",
    "prompt_kind",
    "highlight",
]
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_url(value, label, errors, required=True):
    if not value:
        if required:
            errors.append(f"{label} is empty")
        return
    if not value.startswith(("http://", "https://")) or " " in value:
        errors.append(f"{label} is not an http(s) URL: {value}")


def main():
    errors = []
    games = json.loads((ROOT / "data" / "games.json").read_text())
    updates = json.loads((ROOT / "data" / "updates.json").read_text())
    if not isinstance(games, list) or not games:
        errors.append("data/games.json must be a non-empty list")
        games = []
    seen = set()
    urls = set()
    for game in games:
        for key in REQUIRED:
            if key not in game:
                errors.append(f"{game.get('id', '?')}: missing {key}")
        gid = game.get("id", "")
        if not ID_RE.match(gid):
            errors.append(f"bad id: {gid}")
        elif gid in seen:
            errors.append(f"duplicate id: {gid}")
        seen.add(gid)
        if game.get("game_url") in urls:
            errors.append(f"duplicate game_url on {gid}")
        urls.add(game.get("game_url"))
        if game.get("prompt_kind") not in ("full", "described"):
            errors.append(f"{gid}: bad prompt_kind")
        if not DATE_RE.match(game.get("created_at", "")):
            errors.append(f"{gid}: bad created_at")
        check_url(game.get("project_url", ""), f"{gid} project_url", errors)
        check_url(game.get("game_url", ""), f"{gid} game_url", errors)
        check_url(game.get("author_url", ""), f"{gid} author_url", errors)
        check_url(game.get("conversation_url", ""), f"{gid} conversation_url", errors, required=False)
        shot = game.get("screenshot", "")
        if not shot.startswith("screenshots/") or ".." in shot:
            errors.append(f"{gid}: screenshot must live in screenshots/")
        elif not (ROOT / shot).is_file():
            errors.append(f"{gid}: missing {shot}")
        prompt_path = ROOT / "prompts" / f"{gid}.md"
        if not prompt_path.is_file():
            errors.append(f"{gid}: missing prompts/{gid}.md")
        else:
            text = prompt_path.read_text()
            if "## Prompt" not in text or game["name"] not in text:
                errors.append(f"{gid}: prompt file is out of date")
        if game.get("prompt_kind") == "full" and not game.get("prompt", "").strip():
            errors.append(f"{gid}: full prompt is empty")
    for name in ("assets/readme/mosaic.png", "assets/readme/site.png"):
        if not (ROOT / name).is_file():
            errors.append(f"missing {name}")
    if not isinstance(updates, list) or len(updates) < 2:
        errors.append("data/updates.json needs at least two dated lines")
    else:
        for item in updates:
            if not DATE_RE.match(item.get("date", "")) or not item.get("text"):
                errors.append(f"bad update: {item}")
    expected = render_readme.render(games, updates)
    actual = (ROOT / "README.md").read_text() if (ROOT / "README.md").is_file() else ""
    if actual != expected:
        errors.append("README.md does not match scripts/render_readme.py")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"ok: {len(games)} games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
