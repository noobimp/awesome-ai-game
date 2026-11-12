# 参与收录

感兴趣，或想贡献，请到 GitHub 提交 Pull Request：https://github.com/noobimp/awesome-ai-game/pulls

合并之后，游戏会出现在：

- 网站（读 `data/games.json`）
- README 的分类表和 Latest prompts
- `prompts/<id>.md`
- `screenshots/`

## 本地跑起来

```bash
python3 server/app.py
```

然后打开 `http://127.0.0.1:8080`。

要让提交真正开出 Pull Request，在仓库根目录放一个 `.env`（可以照 `.env.example` 复制）：

```bash
GITHUB_TOKEN=github_pat_xxx
GITHUB_REPO=noobimp/awesome-ai-game
PORT=8080
```

Token 需要这个仓库的 Contents 写权限和 Pull requests 写权限。不要把 `.env` 提交进 Git。

## 合并之后网站怎么更新

服务器展示的是当前这份检出。GitHub 往 `main` 推送时，如果配置了 `WEBHOOK_SECRET`，`POST /api/webhook` 会执行 `git pull --ff-only`。也可以自己定时拉取。

域名 `awesome-ai-game.site` 指到跑着 `server/app.py` 的机器后，网站和提交接口是同一个源。

## 手动改收录

改 `data/games.json`，然后运行：

```bash
python3 scripts/render_readme.py
python3 scripts/validate_games.py
```

`render_readme.py` 会重写 `README.md` 和 `prompts/`。校验不过的 Pull Request 不会通过 GitHub Action。
