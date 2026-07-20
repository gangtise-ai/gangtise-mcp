"""整合版服务表：slug ↔ 包 ↔ 端口。stdio 在 mcp/；HTTP/SSE 在 api/。"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ServiceSpec:
    slug: str
    package_dir: str
    port: int
    mcp_command: str
    api_command: str


ALL_SERVICES: Tuple[ServiceSpec, ...] = (
    ServiceSpec("agent", "gangtise_agent", 18001, "gangtise-agent-mcp", "gangtise-agent-api"),
    ServiceSpec("data", "gangtise_data", 18002, "gangtise-data-mcp", "gangtise-data-api"),
    ServiceSpec("file", "gangtise_file", 18003, "gangtise-file-mcp", "gangtise-file-api"),
    ServiceSpec("kb", "gangtise_kb", 18004, "gangtise-kb-mcp", "gangtise-kb-api"),
    ServiceSpec("private", "gangtise_private", 18005, "gangtise-private-mcp", "gangtise-private-api"),
    ServiceSpec("hub", "gangtise_hub", 18006, "gangtise-hub-mcp", "gangtise-hub-api"),
    ServiceSpec("mcp", "gangtise_mcp", 18007, "gangtise-mcp", "gangtise-mcp-api"),
)

DOMAIN_SERVICES: Tuple[ServiceSpec, ...] = ALL_SERVICES[:5]

_BY_KEY: Dict[str, ServiceSpec] = {}
for _s in ALL_SERVICES:
    _BY_KEY[_s.slug] = _s
    _BY_KEY[_s.package_dir] = _s
    _BY_KEY[_s.mcp_command] = _s
    _BY_KEY[_s.api_command] = _s
    _BY_KEY[f"gangtise-{_s.slug}"] = _s
    _BY_KEY[f"gangtise_{_s.slug}"] = _s

_MCP_SERVER_FILES = {
    "gangtise_agent": "agent_server.py",
    "gangtise_data": "data_server.py",
    "gangtise_file": "file_server.py",
    "gangtise_kb": "kb_server.py",
    "gangtise_private": "private_server.py",
    "gangtise_hub": "hub_server.py",
    "gangtise_mcp": "full_server.py",
}


def mcps_root() -> Path:
    raw = (os.getenv("GTS_MCP_ROOT") or os.getenv("GANGTISE_MCP_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    here = Path(__file__).resolve().parent
    if here.name == "src":
        # api/gangtise_mcp/src or mcp/gangtise_mcp/src -> mcps/
        return here.parents[2].resolve()
    return here.parents[1].resolve()


def mcp_root() -> Path:
    """兼容旧名：返回 mcps/ 根。"""
    return mcps_root()


def package_mcp_src(package_dir: str) -> Path:
    root = mcps_root()
    sf = _MCP_SERVER_FILES.get(package_dir, "server.py")
    candidates = [
        root / "mcp" / package_dir / "src",
        root / "mcp" / package_dir,
        Path("/opt/mcp") / "mcp" / package_dir / "src",
        Path("/opt/mcp") / package_dir / "src",
    ]
    for c in candidates:
        if (c / sf).is_file() or (c / "http_server.py").is_file():
            if (c / sf).is_file():
                return c
    for c in candidates:
        if c.is_dir():
            return c
    return root / "mcp" / package_dir / "src"


def package_api_src(package_dir: str) -> Path:
    root = mcps_root()
    candidates = [
        root / "api" / package_dir / "src",
        root / "api" / package_dir,
        Path("/opt/mcp") / "api" / package_dir / "src",
    ]
    for c in candidates:
        if (c / "http_server.py").is_file():
            return c
    return root / "api" / package_dir / "src"


def package_src(package_dir: str) -> Path:
    """默认业务/stdio 源。"""
    return package_mcp_src(package_dir)


def resolve_package(key: str) -> ServiceSpec:
    if key in ("all", "*", ""):
        raise KeyError("all")
    spec = _BY_KEY.get(key)
    if spec is None:
        raise KeyError(key)
    return spec


def select_services(package_key: str) -> List[ServiceSpec]:
    key = (package_key or "domains").strip()
    if key in ("all", "*"):
        return list(ALL_SERVICES)
    if key in ("domains", "five", "core"):
        return list(DOMAIN_SERVICES)
    return [resolve_package(key)]


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_port(port: int, *, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_open(port):
            return
        time.sleep(0.25)
    raise TimeoutError(f"等待端口 {port} 超时")


def backends_csv(services: Sequence[ServiceSpec]) -> str:
    return ",".join(f"{s.slug}={s.port}" for s in services)


def _pythonpath_for_api(package_dir: str) -> str:
    root = mcps_root()
    parts = [str(package_api_src(package_dir)), str(package_mcp_src(package_dir))]
    for dom in (
        "gangtise_agent",
        "gangtise_data",
        "gangtise_file",
        "gangtise_kb",
        "gangtise_private",
    ):
        parts.append(str(package_mcp_src(dom)))
        parts.append(str(package_api_src(dom)))
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    seen = set()
    ordered = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    return os.pathsep.join(ordered)


def _pythonpath_for_mcp(package_dir: str) -> str:
    root = mcps_root()
    parts = [str(package_mcp_src(package_dir))]
    for dom in (
        "gangtise_agent",
        "gangtise_data",
        "gangtise_file",
        "gangtise_kb",
        "gangtise_private",
    ):
        parts.append(str(package_mcp_src(dom)))
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    seen = set()
    ordered = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    return os.pathsep.join(ordered)


def start_backend(
    spec: ServiceSpec,
    *,
    transport: str,
) -> subprocess.Popen:
    if transport == "stdio":
        raise ValueError("gateway 子进程不可使用 stdio transport")
    argv = [
        "--transport",
        transport,
        "--host",
        "127.0.0.1",
        "--port",
        str(spec.port),
        "--path",
        f"/open-mcp/{spec.slug}",
        "--sse-path",
        f"/sse/{spec.slug}",
        "--message-path",
        f"/messages/{spec.slug}/",
    ]
    if spec.package_dir == "gangtise_mcp":
        argv.extend(["--layout", "unified", "--package", "all"])
    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath_for_api(spec.package_dir)
    env.setdefault("GTS_MCP_ROOT", str(mcps_root()))
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys; "
            f"sys.path.insert(0, {str(package_api_src(spec.package_dir))!r}); "
            "from http_server import main; main(sys.argv[1:])"
        ),
        *argv,
    ]
    return subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=None,
        cwd=str(package_api_src(spec.package_dir)),
    )


def run_package_stdio(package_dir: str, extra_argv: Optional[Sequence[str]] = None) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = _pythonpath_for_mcp(package_dir)
    env.setdefault("GTS_MCP_ROOT", str(mcps_root()))
    sf = _MCP_SERVER_FILES[package_dir]
    mod = sf[:-3]
    cmd = [
        sys.executable,
        "-c",
        f"from {mod} import main; import sys; main(sys.argv[1:])",
        *(extra_argv or ()),
    ]
    os.execve(cmd[0], cmd, env)


def invoke_package_main(package_dir: str, argv: Sequence[str]) -> None:
    """委托到对应层：含 --transport 且非 stdio → api；否则 mcp stdio。"""
    transport = "stdio"
    for i, a in enumerate(argv):
        if a == "--transport" and i + 1 < len(argv):
            transport = argv[i + 1]
            break
    if transport == "stdio":
        mcp_src = package_mcp_src(package_dir)
        for p in _pythonpath_for_mcp(package_dir).split(os.pathsep):
            if p and p not in sys.path:
                sys.path.insert(0, p)
        os.environ.setdefault("GTS_MCP_ROOT", str(mcps_root()))
        import importlib.util

        mod_name = f"_gts_mcp_delegate_{package_dir}"
        path = mcp_src / _MCP_SERVER_FILES[package_dir]
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载 {path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        # 过滤掉 --transport stdio
        cleaned = []
        skip = False
        for a in argv:
            if skip:
                skip = False
                continue
            if a == "--transport":
                skip = True
                continue
            cleaned.append(a)
        mod.main(cleaned)
        return

    api_src = package_api_src(package_dir)
    for p in _pythonpath_for_api(package_dir).split(os.pathsep):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault("GTS_MCP_ROOT", str(mcps_root()))
    import importlib.util

    mod_name = f"_gts_api_delegate_{package_dir}"
    path = api_src / "http_server.py"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    mod.main(list(argv))
