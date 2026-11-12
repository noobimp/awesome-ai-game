#!/usr/bin/env python3
"""Serve the catalog and turn a submission into a GitHub pull request.

A merged pull request adds the prompt, the screenshot, and the catalog entry.
The site reads data/games.json, so the game shows up after this checkout
updates (webhook or git pull).
"""

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render_readme  # noqa: E402

MAX_BODY = 8 * 1024 * 1024
MAX_IMAGE = 2 * 1024 * 1024
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}
MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUBMIT_LOCK = threading.Lock()
HITS = {}


def load_dotenv():
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def github(method, path, body=None, ok_404=False):
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise GitHubError(503, "服务器还没配置 GITHUB_TOKEN，暂时不能开 Pull Request")
    url = "https://api.github.com" + path
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "awesome-ai-game")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        if exc.code == 404 and ok_404:
            return None
        message = raw
        try:
            message = json.loads(raw).get("message", raw)
        except json.JSONDecodeError:
            pass
        raise GitHubError(exc.code, f"GitHub 返回 {exc.code}: {message}") from exc


def repo_name():
    return os.environ.get("GITHUB_REPO", "noobimp/awesome-ai-game")


def clean_line(value, limit, label):
    value = "".join(ch for ch in value.strip() if ch >= " " or ch == "\t")
    if len(value) > limit:
        raise ValueError(f"{label}太长了")
    return value


def clean_block(value, limit, label):
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    value = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32)
    if len(value) > limit:
        raise ValueError(f"{label}太长了")
    return value


def clean_url(value, label, required):
    value = value.strip()
    if not value:
        if required:
            raise ValueError(f"请填写{label}")
        return ""
    parsed = parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or " " in value or len(value) > 500:
        raise ValueError(f"{label}需要是 http 或 https 链接")
    return value


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if len(slug) < 3:
        slug = "game-" + hashlib.sha1(name.encode()).hexdigest()[:8]
    return slug[:40]


def sniff_image(data, claimed):
    if len(data) > MAX_IMAGE:
        raise ValueError("截图不能超过 2MB")
    if len(data) < 12:
        raise ValueError("截图文件不完整")
    kind = None
    for magic, mime in MAGIC:
        if data.startswith(magic):
            kind = mime
            break
    if data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = "image/webp"
    if kind is None or kind not in IMAGE_TYPES:
        raise ValueError("截图只支持 PNG、JPEG、WEBP、GIF")
    if claimed and claimed.split(";")[0].strip() and claimed.split(";")[0].strip() != kind:
        # Trust the file bytes. A browser sometimes sends a generic type.
        pass
    return kind


def parse_multipart(content_type, body):
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type or "")
    if not match:
        raise ValueError("提交格式不对")
    boundary = (match.group(1) or match.group(2)).strip().encode()
    fields = {}
    for part in body.split(b"--" + boundary):
        if not part or part in (b"--", b"--\r\n"):
            continue
        part = part.strip(b"\r\n")
        if part.endswith(b"--"):
            part = part[:-2].strip(b"\r\n")
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        header = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]+)"', header)
        if not name:
            continue
        filename = re.search(r'filename="([^"]*)"', header)
        mime = re.search(r"Content-Type:\s*([^\r\n;]+)", header, re.I)
        fields[name.group(1)] = {
            "filename": filename.group(1) if filename else "",
            "type": mime.group(1).strip() if mime else "",
            "data": data,
        }
    return fields


def field_text(fields, name):
    item = fields.get(name)
    if not item:
        return ""
    return item["data"].decode("utf-8", "replace")


def limited(ip):
    now = time.time()
    recent = [stamp for stamp in HITS.get(ip, []) if now - stamp < 3600]
    if len(recent) >= 5:
        return False
    recent.append(now)
    HITS[ip] = recent
    return True


def author_url_for(author, project_url):
    if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", author):
        return f"https://github.com/{author}"
    return project_url


def blob(repo, content):
    result = github("POST", f"/repos/{repo}/git/blobs", {"content": base64.b64encode(content).decode(), "encoding": "base64"})
    return result["sha"]


def load_remote_catalog(repo, ref):
    payload = github("GET", f"/repos/{repo}/contents/data/games.json?ref={parse.quote(ref)}", ok_404=True)
    if not payload:
        raise GitHubError(409, "仓库里还没有 data/games.json。先把当前目录推上去，再接受提交。")
    return json.loads(base64.b64decode(payload["content"]))


def load_remote_updates(repo, ref):
    payload = github("GET", f"/repos/{repo}/contents/data/updates.json?ref={parse.quote(ref)}", ok_404=True)
    if not payload:
        return []
    return json.loads(base64.b64decode(payload["content"]))


def create_submission(game, image_bytes):
    repo = repo_name()
    meta = github("GET", f"/repos/{repo}")
    base = meta["default_branch"]
    games = load_remote_catalog(repo, base)
    updates = load_remote_updates(repo, base)
    if any(item.get("game_url") == game["game_url"] for item in games):
        raise ValueError("这个游戏地址已经在收录里了")
    taken = {item["id"] for item in games}
    gid = game["id"]
    if gid in taken:
        number = 2
        while f"{gid}-{number}" in taken:
            number += 1
        gid = f"{gid}-{number}"
        game["id"] = gid
    ext = IMAGE_TYPES[game.pop("_image_type")]
    game["screenshot"] = f"screenshots/{gid}.{ext}"
    games.append(game)
    today = game["created_at"]
    updates.insert(0, {"date": today, "text": f"{game['name']} by {render_readme.at_name(game['author'])} added"})
    ref = github("GET", f"/repos/{repo}/git/ref/heads/{base}")
    parent = ref["object"]["sha"]
    commit = github("GET", f"/repos/{repo}/git/commits/{parent}")
    files = {
        "data/games.json": json.dumps(games, ensure_ascii=False, indent=2).encode() + b"\n",
        "data/updates.json": json.dumps(updates, ensure_ascii=False, indent=2).encode() + b"\n",
        "README.md": render_readme.render(games, updates).encode(),
        f"prompts/{gid}.md": render_readme.render_prompt(game).encode(),
        game["screenshot"]: image_bytes,
    }
    tree = []
    for path, content in files.items():
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob(repo, content)})
    tree_sha = github("POST", f"/repos/{repo}/git/trees", {"base_tree": commit["tree"]["sha"], "tree": tree})["sha"]
    message = f"Add game: {game['name']}\n\nSubmitted by {game['author']}."
    new_commit = github(
        "POST",
        f"/repos/{repo}/git/commits",
        {"message": message, "tree": tree_sha, "parents": [parent]},
    )["sha"]
    branch = f"submit/{gid}-{hashlib.sha1(new_commit.encode()).hexdigest()[:6]}"
    github("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": new_commit})
    body = "\n".join(
        [
            "## 新游戏",
            "",
            f"- 用户 ID：{game['author']}",
            f"- 游戏名：{game['name']}",
            f"- 项目地址：{game['project_url']}",
            f"- 游戏地址：{game['game_url']}",
            f"- 模型：{game['model'] or '未填'}",
            f"- 预算：{game['budget'] or '未填'}",
            f"- 对话链接：{game['conversation_url'] or '未填'}",
            "",
            "截图和 prompt 在这个 PR 的文件里。合并后会出现在网站、README、`prompts/` 和 `data/games.json`。",
        ]
    )
    pr = github(
        "POST",
        f"/repos/{repo}/pulls",
        {"title": f"Add game: {game['name']}", "head": branch, "base": base, "body": body},
    )
    return pr["html_url"], gid


def build_game(fields):
    author = clean_line(field_text(fields, "author"), 40, "用户 ID")
    name = clean_line(field_text(fields, "name"), 80, "游戏名")
    if not author:
        raise ValueError("请填写用户 ID")
    if not name:
        raise ValueError("请填写游戏名")
    project_url = clean_url(field_text(fields, "project_url"), "项目地址", True)
    game_url = clean_url(field_text(fields, "game_url"), "游戏地址", True)
    model = clean_line(field_text(fields, "model"), 120, "模型")
    prompt = clean_block(field_text(fields, "prompt"), 20000, "Prompt")
    budget = clean_line(field_text(fields, "budget"), 80, "预算")
    conversation = clean_url(field_text(fields, "conversation_url"), "对话链接", False)
    image = fields.get("screenshot")
    if not image or not image.get("filename") or not image.get("data"):
        raise ValueError("请上传游戏截图")
    kind = sniff_image(image["data"], image.get("type", ""))
    return {
        "id": slugify(name),
        "name": name,
        "author": author,
        "author_url": author_url_for(author, project_url),
        "category": "Community",
        "stack": model or "HTML",
        "project_url": project_url,
        "game_url": game_url,
        "screenshot": "",
        "model": model,
        "prompt": prompt,
        "budget": budget,
        "conversation_url": conversation,
        "created_at": datetime.now(timezone.utc).date().isoformat(),
        "prompt_kind": "full" if prompt else "described",
        "highlight": True,
        "_image_type": kind,
        "_image": image["data"],
    }


def safe_path(url_path):
    path = parse.urlparse(url_path).path
    if path in ("", "/"):
        path = "/index.html"
    rel = Path(path.lstrip("/"))
    if any(part.startswith(".") for part in rel.parts):
        return None
    full = (ROOT / rel).resolve()
    if ROOT not in full.parents and full != ROOT:
        return None
    return full


class Handler(BaseHTTPRequestHandler):
    server_version = "awesome-ai-game"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if parse.urlparse(self.path).path == "/api/health":
            self.send_json(200, {"ok": True})
            return
        full = safe_path(self.path)
        if full is None or not full.is_file():
            self.send_error(404)
            return
        data = full.read_bytes()
        kind = "application/octet-stream"
        suffix = full.suffix.lower()
        if suffix == ".html":
            kind = "text/html; charset=utf-8"
        elif suffix == ".json":
            kind = "application/json; charset=utf-8"
        elif suffix == ".png":
            kind = "image/png"
        elif suffix in (".jpg", ".jpeg"):
            kind = "image/jpeg"
        elif suffix == ".webp":
            kind = "image/webp"
        elif suffix == ".gif":
            kind = "image/gif"
        elif suffix == ".md":
            kind = "text/markdown; charset=utf-8"
        elif suffix == ".css":
            kind = "text/css; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length < 0 or length > MAX_BODY:
            self.send_json(413, {"ok": False, "error": "提交内容太大了"})
            return
        body = self.rfile.read(length)
        if path == "/api/webhook":
            self.handle_webhook(body)
            return
        if path != "/api/submit":
            self.send_json(404, {"ok": False, "error": "没有这个接口"})
            return
        if not limited(self.client_address[0]):
            self.send_json(429, {"ok": False, "error": "提交太频繁了，一小时后再试"})
            return
        try:
            fields = parse_multipart(self.headers.get("Content-Type", ""), body)
            game = build_game(fields)
            image = game.pop("_image")
            with SUBMIT_LOCK:
                pr_url, gid = create_submission(game, image)
        except ValueError as exc:
            self.send_json(400, {"ok": False, "error": str(exc)})
            return
        except GitHubError as exc:
            status = 503 if exc.status == 503 else 502
            self.send_json(status, {"ok": False, "error": str(exc)})
            return
        self.send_json(200, {"ok": True, "pr_url": pr_url, "id": gid})

    def handle_webhook(self, body):
        secret = os.environ.get("WEBHOOK_SECRET", "")
        if not secret:
            self.send_json(404, {"ok": False, "error": "webhook 没打开"})
            return
        signature = self.headers.get("X-Hub-Signature-256", "")
        digest = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, signature):
            self.send_json(401, {"ok": False, "error": "签名不对"})
            return
        if self.headers.get("X-GitHub-Event") != "push":
            self.send_json(200, {"ok": True, "skipped": True})
            return
        payload = json.loads(body.decode("utf-8", "replace") or "{}")
        if not payload.get("ref", "").endswith("/" + os.environ.get("WEBHOOK_BRANCH", "main")):
            self.send_json(200, {"ok": True, "skipped": True})
            return
        try:
            subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"], check=True, timeout=60, capture_output=True)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            self.send_json(500, {"ok": False, "error": "git pull 失败"})
            return
        self.send_json(200, {"ok": True})


def main():
    load_dotenv()
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"serving {ROOT} on {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
