# Gangtise MCP 整合镜像（aliyun / 百炼内部部署）
#
#   cd mcps && docker build -t gangtise-mcp -f Dockerfile \
#     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#     --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
#     .
#
# 运行：
#   docker run -d -p 8000:8000 gangtise-mcp
#   Endpoint: https://<host>:8000/mcp
#   鉴权：请求头 Authorization: Bearer <token>（原样透传下游）

ARG BASE_IMAGE=python:3.11.9
FROM ${BASE_IMAGE}

WORKDIR /app

ARG OBS_ACCESS_KEY=
ARG OBS_SECRET_KEY=
ARG OBS_ENDPOINT=
ARG OBS_BUCKET=
ARG OBS_PATH=
ARG MCP_ATTACH_MAX_BYTES=33554432
ARG INSTALL_OBS=
# pip 源：留空走默认 PyPI；可覆盖为清华等
ARG PIP_INDEX_URL=
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_TRUSTED_HOST=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    MCP_PACKAGE=domains \
    MCP_LAYOUT=unified \
    MCP_PATH=/mcp \
    MCP_STATELESS=true \
    MCP_JSON_RESPONSE=true \
    MCP_REQUIRE_AUTH=true \
    GTS_SAVE_FILE=False \
    GTS_MCP_ROOT=/opt/mcp \
    TOOL_URL_DEPS_PATH=/opt/mcp/tool_url_deps.json \
    MCP_ATTACH_MAX_BYTES=${MCP_ATTACH_MAX_BYTES} \
    OBS_ACCESS_KEY=${OBS_ACCESS_KEY} \
    OBS_SECRET_KEY=${OBS_SECRET_KEY} \
    OBS_ENDPOINT=${OBS_ENDPOINT} \
    OBS_BUCKET=${OBS_BUCKET} \
    OBS_PATH=${OBS_PATH}

COPY mcp/gangtise_mcp/entrypoint.sh /entrypoint.sh
COPY api /app/api
COPY mcp /app/mcp
COPY scripts/scan_tool_url_deps.py /app/scripts/scan_tool_url_deps.py

RUN set -eux; \
    PIP_OPTS=""; \
    if [ -n "${PIP_INDEX_URL}" ]; then PIP_OPTS="${PIP_OPTS} -i ${PIP_INDEX_URL}"; fi; \
    if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then PIP_OPTS="${PIP_OPTS} --extra-index-url ${PIP_EXTRA_INDEX_URL}"; fi; \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then PIP_OPTS="${PIP_OPTS} --trusted-host ${PIP_TRUSTED_HOST}"; fi; \
    chmod +x /entrypoint.sh; \
    mkdir -p /opt/mcp; \
    python /app/scripts/scan_tool_url_deps.py --mcp-root /app/mcp --out /opt/mcp/tool_url_deps.json; \
    pip install --upgrade pip ${PIP_OPTS}; \
    pip install ${PIP_OPTS} \
        "mcp>=1.0.0" \
        "uvicorn>=0.30.0" \
        "httpx>=0.27.0" \
        "requests>=2.32.5" \
        "pandas>=2.2.3" \
        "pyyaml>=6.0" \
        "starlette>=0.37.0"; \
    for pkg in gangtise_agent gangtise_data gangtise_file gangtise_kb gangtise_private gangtise_hub gangtise_mcp; do \
         mkdir -p "/opt/mcp/api/${pkg}" "/opt/mcp/mcp/${pkg}"; \
         pip install --no-deps --target "/opt/mcp/api/${pkg}" ${PIP_OPTS} "/app/api/${pkg}"; \
         pip install --no-deps --target "/opt/mcp/mcp/${pkg}" ${PIP_OPTS} "/app/mcp/${pkg}"; \
       done; \
    if [ -n "${INSTALL_OBS}" ] || { [ -n "${OBS_ACCESS_KEY}" ] && [ -n "${OBS_SECRET_KEY}" ] && [ -n "${OBS_ENDPOINT}" ] && [ -n "${OBS_BUCKET}" ]; }; then \
         pip install ${PIP_OPTS} "esdk-obs-python>=3.24.12"; \
       fi; \
    rm -rf /root/.cache/pip

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
