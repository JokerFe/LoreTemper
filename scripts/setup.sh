#!/bin/zsh
set -e
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v uv >/dev/null; then echo '请安装 uv 后重试'; exit 1; fi
uv sync --frozen
if ! command -v npm >/dev/null && [ -s "$HOME/.nvm/nvm.sh" ]; then source "$HOME/.nvm/nvm.sh"; fi
(cd frontend && npm ci --cache ../work/npm-cache --no-audit --no-fund && npm run build)
uv run video-tutor init
uv run video-tutor doctor
printf '\n完成。双击 启动映知.command 打开应用。模型另用 uv run video-tutor models prepare 下载。\n'
