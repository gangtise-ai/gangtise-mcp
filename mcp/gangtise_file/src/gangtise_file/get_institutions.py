#!/usr/bin/env python3
"""通过 open-reference institutions/search 检索机构 ID。"""

import argparse
import os
import sys
from io import TextIOWrapper
from typing import List, Optional

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .search_institution import (  # noqa: E402
    CATEGORY_LABELS,
    SEARCH_TOP_DEFAULT,
    institution_item_to_row,
    search_institutions,
)
from .utils import authorized_request, check_version, get_authorization_headers, get_authorization_token, get_headers_extra


def format_institutions(rows: List[dict]) -> str:
    if not rows:
        return "未找到匹配的机构。"
    lines = []
    for row in rows:
        iid = row.get("id", "")
        name = row.get("name", "")
        category = row.get("category", "")
        label = CATEGORY_LABELS.get(category, category)
        score = row.get("matchScore")
        scopes = row.get("usageScopes") or []
        scope_txt = "；".join(
            f"{s.get('apiName')}/{s.get('paramName')}"
            for s in scopes
            if isinstance(s, dict) and (s.get("apiName") or s.get("paramName"))
        )
        suffix = f" | matchScore={score:.2f}" if score is not None else ""
        line = f"- {name} ({iid}): {label}{suffix}"
        if scope_txt:
            line += f"\n  适用: {scope_txt}"
        lines.append(line)
    return "\n".join(lines)


def _parse_category_list(raw: str) -> Optional[List[str]]:
    text = (raw or "").strip()
    if not text:
        return None
    return [x.strip() for x in text.split(",") if x.strip()]


def get_institutions(
    keyword: str = "",
    category_list: Optional[List[str]] = None,
    top: int = SEARCH_TOP_DEFAULT,
) -> tuple[str, int]:
    """检索机构，返回 (输出文案, 退出码)。"""
    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise skills 版本失败\n")

    if not get_authorization_token():
        return "未配置 gangtise 授权，无法调用 open 接口", 1

    kw = (keyword or "").strip()
    if not kw:
        return "请提供 --keyword 搜索关键词", 1

    headers = get_authorization_headers()
    items, err = search_institutions(headers, kw, category_list, top=top)
    if err:
        return err, 1

    rows = [institution_item_to_row(item) for item in items]
    header = f"共 {len(rows)} 条（检索词: {kw}）"
    return f"{header}\n{format_institutions(rows)}", 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="通过 institutions/search 检索机构 ID。",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        default="",
        help="搜索关键词（机构名称/简称）",
    )
    parser.add_argument(
        "--category-list",
        default="",
        help="机构分类，逗号分隔：domesticBroker, foreignInstitution, foreignOpinionInstitution, leadInstitution, opinionInstitution",
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
    message, exit_code = get_institutions(
        keyword=args.keyword,
        category_list=_parse_category_list(args.category_list),
        top=args.top,
    )
    print(message)
    sys.exit(exit_code)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
