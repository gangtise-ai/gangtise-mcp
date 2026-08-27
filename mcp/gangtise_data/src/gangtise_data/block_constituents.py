import os
import sys
from io import TextIOWrapper
from typing import List, Optional, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (SECTOR_CONSTITUENTS_URL, SECTOR_SEARCH_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra)

MATCH_SCORE_THRESHOLD = 0.6
SEARCH_TOP_DEFAULT = 10
SEARCH_TOP_MAX = 10
_EXCLUDED_HIERARCHY_MARKERS = ("指数成份类", "指数成分类")


def _is_excluded_sector(item: dict) -> bool:
    hierarchy = str(item.get("hierarchy") or "")
    return any(marker in hierarchy for marker in _EXCLUDED_HIERARCHY_MARKERS)


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _search_sectors(
    headers: dict,
    keyword: str,
    top: int,
) -> Tuple[List[dict], Optional[str]]:
    req_top = max(1, min(int(top), SEARCH_TOP_MAX))
    payload = {"keyword": keyword.strip(), "top": req_top}
    try:
        r = authorized_request("POST", SECTOR_SEARCH_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return [], f"板块检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"板块检索请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"板块检索接口错误: {_api_error_message(body)}"

    data = body.get("data") or {}
    items = data.get("list") or []
    if not isinstance(items, list):
        return [], "板块检索返回 list 格式异常"
    return items, None


def _fetch_constituents(
    headers: dict,
    sector_id: str,
) -> Tuple[List[dict], Optional[str]]:
    payload = {"sectorId": str(sector_id).strip()}
    try:
        r = authorized_request("POST", SECTOR_CONSTITUENTS_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return [], f"成分股 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"成分股请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"成分股接口错误: {_api_error_message(body)}"

    data = body.get("data") or {}
    items = data.get("list") or []
    if not isinstance(items, list):
        return [], "成分股返回 list 格式异常"
    return items, None


def _match_score(item: dict) -> float:
    try:
        return float(item.get("matchScore") or 0)
    except (TypeError, ValueError):
        return 0.0


def _filter_strong_candidates(items: List[dict]) -> List[dict]:
    out = []
    for item in items:
        if _is_excluded_sector(item):
            continue
        if _match_score(item) > MATCH_SCORE_THRESHOLD:
            out.append(item)
    out.sort(key=_match_score, reverse=True)
    return out


def _pick_auto_candidate(candidates: List[dict]) -> Optional[dict]:
    """唯一候选，或候选中仅一条 matchScore=1 时可直接查询。"""
    if len(candidates) == 1:
        return candidates[0]
    perfect = [c for c in candidates if _match_score(c) == 1.0]
    if len(perfect) == 1:
        return perfect[0]
    return None


def _print_sector_candidates(candidates: List[dict], keyword: str) -> str:
    lines = [
        f"关键词「{keyword}」匹配到 {len(candidates)} 个板块，"
        "请确认后使用 -s/--sector-id 指定 sectorId：\n"
    ]
    for c in candidates:
        lines.append(
            f"- sectorId={c.get('sectorId')} | {c.get('sectorName')} | {c.get('hierarchy')}"
        )
    return "\n".join(lines)


def _constituents_to_dataframe(
    rows: List[dict],
    sector_id: str,
    sector_name: str = "",
    hierarchy: str = "",
) -> pd.DataFrame:
    records = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        records.append(
            {
                "sector_id": sector_id,
                "sector_name": sector_name,
                "hierarchy": hierarchy,
                "security_code": row.get("gtsCode"),
                "security_name": row.get("gtsName"),
            }
        )
    if not records:
        return pd.DataFrame(
            columns=["sector_id", "sector_name", "hierarchy", "security_code", "security_name"]
        )
    df = pd.DataFrame(records)
    return df.sort_values(by=["security_code"], ascending=True).reset_index(drop=True)


def block_constituents_data(
    keyword: Optional[str] = None,
    sector_id: Optional[str] = None,
    top: int = SEARCH_TOP_DEFAULT,
    output_dir: Optional[str] = None,
):
    usage: dict = {}
    if not get_authorization_token():
        return format_response(
            {
                "state": "error",
                "message": "未配置 gangtise 授权，无法调用 open 接口",
                "data": [],
                "usage": usage,
            },
            "block_constituents", output_dir=output_dir)

    headers = get_authorization_headers()

    resolved_id = (sector_id or "").strip()
    resolved_name = ""
    resolved_hierarchy = ""

    if not resolved_id:
        kw = (keyword or "").strip()
        if not kw:
            return format_response(
                {
                    "state": "error",
                    "message": "必须提供 keyword 或 sectorId",
                    "data": [],
                    "usage": usage,
                },
                "block_constituents", output_dir=output_dir)

        items, err = _search_sectors(headers, kw, top)
        if err:
            return format_response(
                {"state": "error", "message": err, "data": [], "usage": usage},
                "block_constituents", output_dir=output_dir)

        candidates = _filter_strong_candidates(items)
        if not candidates:
            return format_response(
                {
                    "state": "error",
                    "message": f"未找到与「{kw}」匹配的相关板块",
                    "data": [],
                    "usage": usage,
                },
                "block_constituents", output_dir=output_dir)

        picked = _pick_auto_candidate(candidates)
        if picked is None:
            return _print_sector_candidates(candidates, kw)

        resolved_id = str(picked.get("sectorId") or "").strip()
        resolved_name = str(picked.get("sectorName") or "").strip()
        resolved_hierarchy = str(picked.get("hierarchy") or "").strip()
        if not resolved_id:
            return format_response(
                {
                    "state": "error",
                    "message": "板块检索结果缺少 sectorId",
                    "data": [],
                    "usage": usage,
                },
                "block_constituents", output_dir=output_dir)

    rows, err = _fetch_constituents(headers, resolved_id)
    if err:
        return format_response(
            {"state": "error", "message": err, "data": [], "usage": usage},
            "block_constituents", output_dir=output_dir)

    df = _constituents_to_dataframe(rows, resolved_id, resolved_name, resolved_hierarchy)
    if df.empty:
        label = resolved_name or resolved_id
        return format_response(
            {
                "state": "error",
                "message": f"板块「{label}」（{resolved_id}）暂无成分股数据",
                "data": [],
                "usage": usage,
            },
            "block_constituents", output_dir=output_dir)

    label = resolved_name or resolved_id
    parts = [
        {
            "data": df.to_dict(orient="records"),
            "module": "block_constituents",
            "type": "data",
        }
    ]
    return format_response(
        {
            "state": "success",
            "message": f"已获取板块「{label}」（{resolved_id}）共 {len(df)} 只成分股",
            "data": parts,
            "usage": usage,
        },
        "block_constituents", output_dir=output_dir)


def main():
    import argparse

    try:
        if not check_version():
            update_sh = os.path.join(script_dir, "update.sh")
            print(
                f"[WARNING] 存在 Gangtise data 版本更新，可以执行 {update_sh} 更新，请与用户确认是否更新\n"
            )
    except Exception:
        print("[WARNING] 检查 Gangtise data 版本失败\n")

    parser = argparse.ArgumentParser(
        description="查询板块成分股（open sectors/search + sectors/constituents）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-k", "--keyword", default=None, help="板块搜索关键词（名称/拼音/首字母）")
    group.add_argument("-s", "--sector-id", default=None, help="板块 ID，可直接查询成分股")
    parser.add_argument(
        "-t",
        "--top",
        type=int,
        default=SEARCH_TOP_DEFAULT,
        help=f"关键词搜索返回条数上限（最大 {SEARCH_TOP_MAX}，仅 -k 时生效）",
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="结果保存目录路径，建议使用绝对路径",
    )

    args = parser.parse_args()

    out = block_constituents_data(
        keyword=args.keyword,
        sector_id=args.sector_id,
        top=args.top,
        output_dir=args.output_dir or None,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
