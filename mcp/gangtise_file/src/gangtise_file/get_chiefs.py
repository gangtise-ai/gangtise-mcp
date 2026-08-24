#!/usr/bin/env python3
"""通过 open-reference chiefs/search 检索首席 ID，供 opinion.py 的 --chiefs 等参数使用。"""

import argparse
import os
import sys
from io import TextIOWrapper
from typing import List, Optional

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .search_chief import SEARCH_TOP_DEFAULT, chief_item_to_row, search_chiefs  # noqa: E402
from .utils import authorized_request, check_version, get_authorization_headers, get_authorization_token, get_headers_extra


def _strip_securities_suffix(text: str) -> str:
    t = text.strip()
    if t.endswith("证券"):
        return t[: -len("证券")]
    return t


def institution_matches(query: str, institution: str) -> bool:
    """券商名模糊匹配：支持省略「证券」，如「广发」可匹配「广发证券」。"""
    q = query.strip()
    inst = institution.strip()
    if not q or not inst:
        return False
    if q in inst or inst in q:
        return True
    q_base = _strip_securities_suffix(q)
    inst_base = _strip_securities_suffix(inst)
    if not q_base:
        return False
    return (
        inst_base.startswith(q_base)
        or q_base.startswith(inst_base)
        or q_base in inst_base
        or inst_base in q_base
    )


def name_matches(query: str, name: str) -> bool:
    q = query.strip()
    if not q:
        return False
    return q in name.strip()


def group_matches(query: str, group: str) -> bool:
    q = query.strip()
    g = group.strip()
    if not q or not g:
        return False
    return q in g or g in q


def filter_chief_rows(
    rows: List[dict],
    name: Optional[str] = None,
    institution: Optional[str] = None,
    group: Optional[str] = None,
) -> List[dict]:
    out = list(rows)
    if name and name.strip():
        out = [r for r in out if name_matches(name, r.get("name", ""))]
    if institution and institution.strip():
        out = [
            r
            for r in out
            if institution_matches(institution, r.get("institution", ""))
        ]
    if group and group.strip():
        out = [r for r in out if group_matches(group, r.get("group", ""))]
    return out


def format_chiefs(rows: List[dict]) -> str:
    if not rows:
        return "未找到匹配的首席分析师。"
    lines = []
    for row in rows:
        cid = row.get("id", "")
        name = row.get("name", "")
        inst = row.get("institution", "")
        grp = row.get("group", "")
        score = row.get("matchScore")
        suffix = f" | matchScore={score:.2f}" if score is not None else ""
        lines.append(f"- {name} ({cid}): {inst} / {grp}{suffix}")
    return "\n".join(lines)


def _build_search_keyword(
    keyword: Optional[str],
    name: Optional[str],
    institution: Optional[str],
    group: Optional[str],
) -> Optional[str]:
    if keyword and keyword.strip():
        return keyword.strip()
    parts = [x.strip() for x in (name, institution, group) if x and str(x).strip()]
    if not parts:
        return None
    return " ".join(parts)


def get_chiefs(
    keyword: str = "",
    name: str = "",
    institution: str = "",
    group: str = "",
    top: int = SEARCH_TOP_DEFAULT,
) -> tuple[str, int]:
    """检索首席分析师，返回 (输出文案, 退出码)。"""
    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise skills 版本失败\n")

    if not get_authorization_token():
        return "未配置 gangtise 授权，无法调用 open 接口", 1

    search_keyword = _build_search_keyword(
        keyword.strip() or None,
        name.strip() or None,
        institution.strip() or None,
        group.strip() or None,
    )
    if not search_keyword:
        return "请至少提供 --keyword 或 --name / --institution / --group 之一", 1

    headers = get_authorization_headers()
    items, err = search_chiefs(headers, search_keyword, top=top)
    if err:
        return err, 1

    rows = [chief_item_to_row(item) for item in items]
    rows = filter_chief_rows(
        rows,
        name=name.strip() or None,
        institution=institution.strip() or None,
        group=group.strip() or None,
    )

    header = f"共 {len(rows)} 条（检索词: {search_keyword}）"
    return f"{header}\n{format_chiefs(rows)}", 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="通过 chiefs/search 检索首席分析师。至少提供 --keyword 或 --name / --institution / --group 之一。",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        default="",
        help="搜索关键词（姓名、机构、团队等多维匹配）",
    )
    parser.add_argument(
        "--name",
        default="",
        help="首席姓名（检索后在本地再筛选）",
    )
    parser.add_argument(
        "--institution",
        default="",
        help="券商名称（检索后在本地再筛选；可省略「证券」）",
    )
    parser.add_argument(
        "--group",
        default="",
        help="团队/研究方向（检索后在本地再筛选）",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=SEARCH_TOP_DEFAULT,
        help="返回条数上限，最大 10",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    message, exit_code = get_chiefs(
        keyword=args.keyword,
        name=args.name,
        institution=args.institution,
        group=args.group,
        top=args.top,
    )
    print(message)
    sys.exit(exit_code)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
