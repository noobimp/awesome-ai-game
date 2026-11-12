#!/usr/bin/env python3
"""Render README.md in the same shape as awesome-opus5-5-videos."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://awesome-ai-game.site/?utm_source=github&utm_medium=readme&utm_campaign=awesome-ai-game"
CATEGORY_ORDER = ["Arcade", "Platformers", "Racing", "Action", "Collections", "Community"]


def esc(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def anchor(title):
    slug = title.lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in slug)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def at_name(author):
    return "@" + str(author).lstrip("@")


def fence_for(text):
    run = 0
    longest = 0
    for ch in text:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return "`" * max(3, longest + 1)


def render_prompt(game):
    ticks = fence_for(game["prompt"])
    lines = [
        f"# {game['name']} by {at_name(game['author'])}",
        "",
        f"[▶ Play]({game['game_url']}) · [Original project]({game['project_url']})",
        "",
        f'<img src="../{game["screenshot"]}" alt="{esc(game["name"])}" width="100%">',
        "",
        f"- **Category:** {game['category']}",
        f"- **Built with:** {game['stack']}",
        f"- **Model:** {game['model'] or 'not recorded'}",
        f"- **Author:** [{at_name(game['author'])}]({game['author_url']})",
    ]
    if game.get("budget"):
        lines.append(f"- **Budget:** {game['budget']}")
    if game.get("conversation_url"):
        lines.append(f"- **Conversation:** {game['conversation_url']}")
    lines.extend(["", "## Prompt", ""])
    if game.get("prompt_kind") == "described":
        lines.append("The creator did not publish a single prompt for this entry. They described the project instead.")
        lines.append("")
    lines.append(f"{ticks}text")
    lines.append(game["prompt"].rstrip("\n"))
    lines.append(ticks)
    lines.append("")
    return "\n".join(lines)


def _rows(cells):
    html = ["<table>"]
    for i in range(0, len(cells), 3):
        html.append("<tr>" + "".join(cells[i : i + 3]) + "</tr>")
    html.append("</table>")
    return "\n".join(html)


def _highlight_cell(game):
    author = at_name(game["author"])
    return (
        '<td width="33%" valign="top">'
        f'<a href="{esc(game["game_url"])}"><img src="{esc(game["screenshot"])}" alt="{esc(game["name"])} by {esc(author)}" width="100%"></a><br>'
        f'<a href="{esc(game["author_url"])}"><b>{esc(author)}</b></a> · '
        f'<a href="prompts/{esc(game["id"])}.md">Prompt</a><br>'
        f'<sub>{esc(game["stack"])}</sub></td>'
    )


def _latest_cell(game):
    author = at_name(game["author"])
    return (
        '<td width="33%" valign="top">'
        f'<a href="prompts/{esc(game["id"])}.md"><img src="{esc(game["screenshot"])}" alt="{esc(game["name"])} by {esc(author)}" width="100%"></a><br>'
        f'<a href="prompts/{esc(game["id"])}.md"><b>{esc(game["name"])}</b></a><br>'
        f'<sub><a href="{esc(game["author_url"])}">{esc(author)}</a></sub></td>'
    )


def render(games, updates):
    highlighted = [game for game in games if game.get("highlight", True)]
    by_category = {name: [] for name in CATEGORY_ORDER}
    for game in highlighted:
        by_category.setdefault(game["category"], []).append(game)
    for name in by_category:
        by_category[name].sort(key=lambda game: game["created_at"], reverse=True)
    categories = [name for name in CATEGORY_ORDER if by_category.get(name)]
    for name in by_category:
        if name not in categories and by_category[name]:
            categories.append(name)

    latest = sorted(games, key=lambda game: (game["created_at"], game["id"]), reverse=True)[:30]
    playable = sum(1 for game in games if game.get("game_url"))
    parts = [
        "# Awesome AI Game",
        "",
        f'<a href="{SITE}"><img src="assets/readme/mosaic.png" alt="A wall of open-source HTML games made with AI: arcade, racing, action and sandboxes" width="100%"></a>',
        "",
        "Open-source HTML games people made with AI, each with the prompt the creator shared (or, where no prompt was published, their original project).",
        "",
        "Every game here was made by asking a model to write the game as code (HTML, Canvas, JavaScript, Three.js and friends). Pick one, copy its prompt, and try it with your own agent.",
        "",
        f"**[▶ Play the collection on awesome-ai-game.site]({SITE})**",
        "",
        f'<a href="{SITE}"><img src="assets/readme/site.png" alt="The Awesome AI Game website, a catalog of playable AI HTML games" width="100%"></a>',
        "",
        f"- {len(games)} prompts in [`prompts/`](prompts/) and [`data/games.json`](data/games.json), {playable} of them playable in the browser",
        f"- {len(highlighted)} games highlighted below, grouped by how they play",
        "- Every entry links to the creator's original project",
    ]
    if updates:
        first = updates[0]
        parts.append(f"- Latest update ({first['date']}): {first['text']}")
        if len(updates) > 1:
            second = updates[1]
            parts.append(f"- {second['date']}: {second['text']}")
    parts.extend(["", "## Contents", ""])
    for name in categories:
        parts.append(f"- [{name}](#{anchor(name)}) ({len(by_category[name])})")
    parts.append(f"- [Latest prompts](#latest-prompts) ({len(latest)})")
    parts.append("- [How to use a prompt](#how-to-use-a-prompt)")
    parts.append("- [Submit a game](#submit-a-game)")
    parts.append("- [Credits](#credits)")
    parts.append("")

    for name in categories:
        parts.append(f"## {name}")
        parts.append("")
        parts.append(_rows([_highlight_cell(game) for game in by_category[name]]))
        parts.append("")

    parts.extend(
        [
            "## Latest prompts",
            "",
            "The newest prompts in the catalog, each linking to the creator and the prompt file. All of them are in [`prompts/`](prompts/).",
            "",
            _rows([_latest_cell(game) for game in latest]),
            "",
            "## How to use a prompt",
            "",
            "1. Open a prompt file and copy the block under **Prompt**.",
            "2. Paste it into the model named on the entry (or any coding agent).",
            "3. Ask it to render the result as a single HTML file, then open that file in a browser.",
            "",
            "Some creators shared only part of their prompt, or only described the result in their project; those files say so.",
            "",
            "## Submit a game",
            "",
            "The website at [awesome-ai-game.site](https://awesome-ai-game.site) turns a submission into a pull request against this repo. After it is merged, the game shows up on the site, in this README, in [`prompts/`](prompts/), and in [`data/games.json`](data/games.json).",
            "",
            "Required:",
            "",
            "- User ID",
            "- Game name",
            "- Project URL",
            "- Playable game URL",
            "- Screenshot",
            "",
            "Optional, and listed after the screenshot on the form:",
            "",
            "- Model",
            "- Prompt",
            "- Budget",
            "- Link to the working conversation",
            "",
            "You can also open the pull request yourself. Add `prompts/<id>.md`, a screenshot under `screenshots/`, and an entry in `data/games.json`. The README is generated from that file with `python3 scripts/render_readme.py`.",
            "",
            "## Credits",
            "",
            "Every game and prompt belongs to its creator, linked from each entry. Title cards in the opening collection are stand-ins where the creator did not publish a screenshot; submitted games use the author's screenshot. If you are a creator and want an entry changed or removed, open an issue.",
            "",
            "The catalog and this website's code are MIT. Each game stays under its creator's own license.",
            "",
            f"Play the collection at **[awesome-ai-game.site]({SITE})**.",
            "",
        ]
    )
    return "\n".join(parts)


def main():
    games = json.loads((ROOT / "data" / "games.json").read_text())
    updates = json.loads((ROOT / "data" / "updates.json").read_text())
    (ROOT / "README.md").write_text(render(games, updates))
    prompt_dir = ROOT / "prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    for game in games:
        (prompt_dir / f"{game['id']}.md").write_text(render_prompt(game))
    print(f"wrote README.md and {len(games)} prompt files")


if __name__ == "__main__":
    main()
