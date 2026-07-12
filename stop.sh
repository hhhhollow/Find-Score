#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    echo "❌ 虚拟环境不存在，请先在 $DIR 执行: uv sync" >&2
    exit 1
fi

exec "$PYTHON" -m grade_monitor.service stop
