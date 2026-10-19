#!/bin/zsh
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
  echo '首次运行，请先执行 scripts/setup.sh'
  read '?按回车关闭'
  exit 1
fi
export PYTHONPATH="$PWD/backend"
.venv/bin/python -m video_tutor start
