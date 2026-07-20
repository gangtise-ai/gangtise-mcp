#!/usr/bin/env bash
# Docker 入口：stdio → mcp（gangtise-mcp）；HTTP/SSE/gateway → api（gangtise-mcp-api）。
# 默认（HTTP 部署）：MCP_LAYOUT=unified、MCP_TRANSPORT=http、MCP_REQUIRE_AUTH=true。
# 鉴权：透传 Authorization: Bearer <token>；回传 X-DashScope-Request-ID。
set -euo pipefail

MCP_HOST="${MCP_HOST:-0.0.0.0}"
MCP_PORT="${MCP_PORT:-8000}"
MCP_TRANSPORT="${MCP_TRANSPORT:-http}"
MCP_PACKAGE="${MCP_PACKAGE:-domains}"
MCP_LAYOUT="${MCP_LAYOUT:-unified}"
GTS_SAVE_FILE="${GTS_SAVE_FILE:-False}"
GTS_MCP_ROOT="${GTS_MCP_ROOT:-/opt/mcp}"
MCP_REQUIRE_AUTH="${MCP_REQUIRE_AUTH:-true}"
export GTS_SAVE_FILE
export GTS_MCP_ROOT
export MCP_LAYOUT
export MCP_REQUIRE_AUTH
export MCP_PATH="${MCP_PATH:-/open-mcp}"
export MCP_STATELESS="${MCP_STATELESS:-true}"
export MCP_JSON_RESPONSE="${MCP_JSON_RESPONSE:-true}"

if [[ "${MCP_LAYOUT}" == "hub" ]]; then
  MCP_LAYOUT=unified
  export MCP_LAYOUT
fi

# 收集包路径：优先 gangtise_mcp（避免各包同名 http_server.py 抢导入）
_add_path() {
  local d="$1"
  if [[ -d "${d}" ]]; then
    case ":${PYTHONPATH:-}:" in
      *":${d}:"*) ;;
      *) export PYTHONPATH="${d}${PYTHONPATH:+:${PYTHONPATH}}" ;;
    esac
  fi
}

_add_pkg() {
  local layer="$1" pkg="$2"
  _add_path "${GTS_MCP_ROOT}/${layer}/${pkg}/src"
  _add_path "${GTS_MCP_ROOT}/${layer}/${pkg}"
}

# 先加整合包，再加其它（_add_path 往前插，故后加的更靠前 → 先调用 mcp）
for pkg in gangtise_private gangtise_kb gangtise_file gangtise_data gangtise_agent gangtise_hub; do
  _add_pkg mcp "${pkg}"
  _add_pkg api "${pkg}"
done
_add_pkg mcp gangtise_mcp
_add_pkg api gangtise_mcp

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -d "${SCRIPT_DIR}/src" ]]; then
  _add_path "${SCRIPT_DIR}/src"
  export GTS_MCP_ROOT="${GTS_MCP_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
fi

_resolve_http_server_dir() {
  for d in \
    "${GTS_MCP_ROOT}/api/gangtise_mcp/src" \
    "${GTS_MCP_ROOT}/api/gangtise_mcp" \
    "${SCRIPT_DIR}/../../api/gangtise_mcp/src" \
    "${SCRIPT_DIR}/../../api/gangtise_mcp"
  do
    if [[ -f "${d}/http_server.py" ]]; then
      echo "${d}"
      return 0
    fi
  done
  return 1
}

_resolve_full_server_dir() {
  for d in \
    "${GTS_MCP_ROOT}/mcp/gangtise_mcp/src" \
    "${GTS_MCP_ROOT}/mcp/gangtise_mcp" \
    "${SCRIPT_DIR}/src" \
    "${SCRIPT_DIR}"
  do
    if [[ -f "${d}/full_server.py" ]]; then
      echo "${d}"
      return 0
    fi
  done
  return 1
}

run_stdio() {
  if command -v gangtise-mcp >/dev/null 2>&1; then
    exec gangtise-mcp "$@"
  fi
  local src
  src="$(_resolve_full_server_dir)" || {
    echo "未找到 gangtise_mcp/full_server.py（GTS_MCP_ROOT=${GTS_MCP_ROOT})" >&2
    exit 1
  }
  export PYTHONPATH="${src}${PYTHONPATH:+:${PYTHONPATH}}"
  exec python -c "from full_server import main; import sys; main(sys.argv[1:])" "$@"
}

run_api() {
  if command -v gangtise-mcp-api >/dev/null 2>&1; then
    exec gangtise-mcp-api "$@"
  fi
  local src
  src="$(_resolve_http_server_dir)" || {
    echo "未找到 gangtise_mcp/http_server.py（GTS_MCP_ROOT=${GTS_MCP_ROOT})" >&2
    exit 1
  }
  # 强制本进程优先使用整合包 http_server（勿被 private/data 等同名模块覆盖）
  export PYTHONPATH="${src}${PYTHONPATH:+:${PYTHONPATH}}"
  exec python -c "from http_server import main; import sys; main(sys.argv[1:])" "$@"
}

if [[ "${MCP_TRANSPORT}" == "stdio" ]]; then
  run_stdio "$@"
fi

EXTRA=(
  --transport "${MCP_TRANSPORT}"
  --layout "${MCP_LAYOUT}"
  --package "${MCP_PACKAGE}"
  --host "${MCP_HOST}"
  --port "${MCP_PORT}"
)

run_api "${EXTRA[@]}" "$@"
