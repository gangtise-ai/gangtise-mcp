import os
import re
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import TextIOWrapper
from typing import Dict, List, Optional, Tuple

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (authorized_request, CONCEPT_SECURITIES_URL, CONCEPT_URL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra)

from .search_concept import SEARCH_TOP_DEFAULT, resolve_concept_keyword

POINTS_PER_CALL = 50
QUERY_TYPES = {"info", "securities", "all"}


def _parse_str_list(raw: str) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    return [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]


def _load_concepts_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"题材文件不存在: {path}")
    import pandas as pd

    df = pd.read_csv(path)
    col = None
    for c in ("concept_id", "conceptId", "concept_name", "conceptName"):
        if c in df.columns:
            col = c
            break
    if col is None:
        raise ValueError("题材文件须包含 concept_id / conceptId / concept_name / conceptName 列")
    return [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]


def resolve_concept_id(
    token: str,
    headers: dict,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[Optional[str], Optional[str], bool]:
    """将题材 ID 或名称解析为 conceptId。"""
    raw = (token or "").strip()
    if not raw:
        return None, "题材标识为空", False

    if re.fullmatch(r"[a-zA-Z0-9]+", raw):
        return raw, None, False

    return resolve_concept_keyword(headers, raw, top)


def _resolve_concept_tokens(
    tokens: List[str],
    headers: dict,
    top: int = SEARCH_TOP_DEFAULT,
) -> Tuple[List[str], List[str], Optional[str]]:
    resolved: List[str] = []
    errors: List[str] = []
    for token in tokens:
        cid, err, is_candidates = resolve_concept_id(token, headers, top)
        if is_candidates:
            return [], [], err
        if cid:
            if cid not in resolved:
                resolved.append(cid)
        elif err:
            errors.append(err)
    return resolved, errors, None


def _normalize_query_type(query_type: str) -> Optional[str]:
    key = (query_type or "all").strip().lower()
    if key not in QUERY_TYPES:
        return None
    return key


def _parse_concept_body(body: dict) -> Tuple[dict, List[dict]]:
    if not body or str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return {}, []
    data = body.get("data") or {}
    if not isinstance(data, dict):
        return {}, []

    profile = {
        "conceptId": data.get("conceptId"),
        "conceptName": data.get("conceptName"),
        "definition": data.get("definition"),
        "investmentLogic": data.get("investmentLogic"),
        "industrySpace": data.get("industrySpace"),
        "competitiveLandscape": data.get("competitiveLandscape"),
    }
    events_raw = data.get("keyEvents")
    events: List[dict] = []
    if isinstance(events_raw, list):
        for item in events_raw:
            if not isinstance(item, dict):
                continue
            date_val = item.get("date")
            content_val = item.get("content")
            if date_val is None and content_val is None:
                continue
            events.append({"date": date_val, "content": content_val})
    return profile, events


def _parse_concept_securities_body(body: dict) -> Tuple[dict, List[dict]]:
    if not body or str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return {}, []
    data = body.get("data") or {}
    if not isinstance(data, dict):
        return {}, []

    concept_id = data.get("conceptId")
    concept_name = data.get("conceptName")
    security_count = data.get("securityCount")
    try:
        security_count = int(security_count) if security_count is not None else 0
    except (TypeError, ValueError):
        security_count = 0

    summary = {
        "conceptId": concept_id,
        "conceptName": concept_name,
        "securityCount": security_count,
    }

    rows: List[dict] = []
    detail = data.get("securityDetail")
    if isinstance(detail, list):
        for group in detail:
            if not isinstance(group, dict):
                continue
            group_name = group.get("groupName")
            sec_list = group.get("securityList")
            if not isinstance(sec_list, list):
                continue
            for sec in sec_list:
                if not isinstance(sec, dict):
                    continue
                rows.append(
                    {
                        "groupName": group_name,
                        "securityCode": sec.get("securityCode"),
                        "securityName": sec.get("securityName"),
                        "isKey": sec.get("isKey"),
                        "inclusionReason": sec.get("inclusionReason"),
                    }
                )
    return summary, rows


def _fetch_concept_info(headers: dict, concept_id: str) -> Tuple[dict, List[dict], Optional[str]]:
    payload = {"conceptId": concept_id}
    try:
        r = authorized_request("POST", CONCEPT_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return {}, [], f"HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return {}, [], f"请求异常: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        msg = str(body.get("msg") or body.get("message") or "接口返回失败")
        return {}, [], msg

    profile, events = _parse_concept_body(body)
    if not profile.get("conceptId"):
        return {}, [], "接口未返回有效题材数据"
    return profile, events, None


def _fetch_concept_securities(
    headers: dict, concept_id: str
) -> Tuple[dict, List[dict], Optional[str]]:
    payload = {"conceptId": concept_id}
    try:
        r = authorized_request("POST", CONCEPT_SECURITIES_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return {}, [], f"HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return {}, [], f"请求异常: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        msg = str(body.get("msg") or body.get("message") or "接口返回失败")
        return {}, [], msg

    summary, rows = _parse_concept_securities_body(body)
    if not summary.get("conceptId"):
        return {}, [], "接口未返回有效题材成分股数据"
    return summary, rows, None


def _md_section(title: str, content: Optional[str]) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    return f"## {title}\n{text}\n\n"


def _format_security_line(sec: dict) -> str:
    name = (sec.get("securityName") or "").strip()
    code = (sec.get("securityCode") or "").strip()
    is_key = sec.get("isKey") is True
    reason = (sec.get("inclusionReason") or "").strip()

    if name and code:
        label = f"**{name}**（{code}，重点）" if is_key else f"{name}（{code}）"
    elif name:
        label = f"**{name}**（重点）" if is_key else name
    elif code:
        label = f"**{code}**（重点）" if is_key else code
    else:
        label = "未知证券"

    if reason:
        return f"- {label}：{reason}"
    return f"- {label}"


def _build_securities_md(summary: dict, securities: List[dict]) -> str:
    count = int(summary.get("securityCount") or 0)
    if count <= 0 or not securities:
        return "## 成分股\n暂无成分股。\n\n"

    groups: "OrderedDict[str, List[dict]]" = OrderedDict()
    for row in securities:
        group_name = (row.get("groupName") or "其他").strip() or "其他"
        groups.setdefault(group_name, []).append(row)

    lines = [f"## 成分股（共 {count} 只）\n"]
    for group_name, items in groups.items():
        lines.append(f"### {group_name}\n")
        lines.extend(_format_security_line(sec) for sec in items)
        lines.append("")
    return "\n".join(lines).strip() + "\n\n"


def _build_concept_markdown(
    profile: Optional[dict],
    events: List[dict],
    summary: Optional[dict],
    securities: List[dict],
    qtype: str,
) -> str:
    concept_id = (profile or summary or {}).get("conceptId") or ""
    concept_name = (profile or summary or {}).get("conceptName") or concept_id
    parts = [f"# {concept_name}（{concept_id}）\n"]

    if qtype in ("info", "all") and profile:
        parts.append(_md_section("题材定义", profile.get("definition")))
        parts.append(_md_section("投资逻辑", profile.get("investmentLogic")))
        parts.append(_md_section("行业空间", profile.get("industrySpace")))
        parts.append(_md_section("竞争格局", profile.get("competitiveLandscape")))
        if events:
            event_lines = []
            for ev in events:
                date_val = (ev.get("date") or "").strip()
                content_val = (ev.get("content") or "").strip()
                if date_val and content_val:
                    event_lines.append(f"- {date_val}：{content_val}")
                elif content_val:
                    event_lines.append(f"- {content_val}")
            if event_lines:
                parts.append("## 催化事件\n" + "\n".join(event_lines) + "\n\n")

    if qtype in ("securities", "all") and summary:
        parts.append(_build_securities_md(summary, securities))

    return "".join(parts).strip()


def concept_data(concepts: List[str], query_type: str = "all", top: int = SEARCH_TOP_DEFAULT):
    usage: dict = {}
    qtype = _normalize_query_type(query_type)
    if qtype is None:
        return format_response(
            {
                "state": "error",
                "message": f"query_type 仅支持 {', '.join(sorted(QUERY_TYPES))}",
                "data": [],
                "usage": usage,
            },
            "concept",
        )

    if not get_authorization_token():
        return format_response(
            {
                "state": "error",
                "message": "未配置 gangtise 授权，无法调用 open 接口",
                "data": [],
                "usage": usage,
            },
            "concept",
        )

    tokens = [str(x).strip() for x in concepts if str(x).strip()]
    if not tokens:
        return format_response(
            {"state": "error", "message": "请提供至少一个题材 ID 或名称", "data": [], "usage": usage},
            "concept",
        )

    headers = get_authorization_headers()

    resolved, resolve_errors, candidate_msg = _resolve_concept_tokens(tokens, headers, top)
    if candidate_msg:
        return candidate_msg
    if not resolved:
        return format_response(
            {
                "state": "error",
                "message": "；".join(resolve_errors) if resolve_errors else "未解析到有效题材",
                "data": [],
                "usage": usage,
            },
            "concept",
        )

    concept_blocks: List[dict] = []
    fetch_errors: List[str] = []
    info_count = 0
    securities_billed = 0

    def _fetch_one(cid: str):
        result = {"cid": cid, "profile": None, "events": [], "summary": None, "securities": []}
        if qtype in ("info", "all"):
            profile, evts, err = _fetch_concept_info(headers, cid)
            result["profile"] = profile
            result["events"] = evts
            result["info_err"] = err
        if qtype in ("securities", "all"):
            summary, rows, err = _fetch_concept_securities(headers, cid)
            result["summary"] = summary
            result["securities"] = rows
            result["securities_err"] = err
        return result

    with ThreadPoolExecutor(max_workers=min(8, len(resolved))) as pool:
        futures = {pool.submit(_fetch_one, cid): cid for cid in resolved}
        for fut in as_completed(futures):
            cid = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                fetch_errors.append(f"{cid}: {e}")
                continue

            profile = result.get("profile") or {}
            summary = result.get("summary") or {}
            events = result.get("events") or []
            securities = result.get("securities") or []

            ok_info = qtype in ("info", "all") and not result.get("info_err") and profile.get("conceptId")
            ok_securities = qtype in ("securities", "all") and not result.get("securities_err") and summary.get("conceptId")

            if qtype == "info" and result.get("info_err"):
                fetch_errors.append(f"{cid}(info): {result['info_err']}")
            if qtype == "securities" and result.get("securities_err"):
                fetch_errors.append(f"{cid}(securities): {result['securities_err']}")
            if qtype == "all":
                if result.get("info_err"):
                    fetch_errors.append(f"{cid}(info): {result['info_err']}")
                if result.get("securities_err"):
                    fetch_errors.append(f"{cid}(securities): {result['securities_err']}")

            if not ok_info and not ok_securities:
                continue

            if ok_info:
                info_count += 1
            if ok_securities and int(summary.get("securityCount") or 0) > 0:
                securities_billed += 1

            concept_blocks.append(
                {
                    "profile": profile if ok_info else None,
                    "events": events if ok_info else [],
                    "summary": summary if ok_securities else None,
                    "securities": securities if ok_securities else [],
                }
            )

    if not concept_blocks:
        msg = "；".join(fetch_errors) if fetch_errors else "未获取到题材数据"
        if resolve_errors:
            msg = "；".join(resolve_errors + [msg])
        return format_response(
            {"state": "error", "message": msg, "data": [], "usage": usage},
            "concept",
        )

    if info_count > 0:
        usage["concept_info"] = usage.get("concept_info", 0) + info_count * POINTS_PER_CALL
    if securities_billed > 0:
        usage["concept_securities"] = usage.get("concept_securities", 0) + securities_billed * POINTS_PER_CALL

    markdown_parts = [
        _build_concept_markdown(
            block.get("profile"),
            block.get("events") or [],
            block.get("summary"),
            block.get("securities") or [],
            qtype,
        )
        for block in concept_blocks
    ]
    markdown_content = "\n\n---\n\n".join(part for part in markdown_parts if part.strip())

    names: List[str] = []
    for block in concept_blocks:
        profile = block.get("profile") or {}
        summary = block.get("summary") or {}
        name = str(profile.get("conceptName") or summary.get("conceptName") or profile.get("conceptId") or summary.get("conceptId") or "")
        if name and name not in names:
            names.append(name)
    label = "、".join(names[:3]) + "等" if len(names) > 3 else "、".join(names)

    type_label = {"info": "基本信息", "securities": "成分股", "all": "基本信息与成分股"}[qtype]
    msg = f"已获取题材「{label}」{type_label}"
    if resolve_errors:
        msg += f"；部分输入未解析：{'；'.join(resolve_errors)}"
    if fetch_errors:
        msg += f"；部分请求失败：{'；'.join(fetch_errors)}"

    parts = [
        {
            "data": markdown_content,
            "module": "concept",
            "type": "markdown",
        }
    ]

    return format_response(
        {"state": "success", "message": msg, "data": parts, "usage": usage},
        "concept",
    )


def concept_info(concepts: List[str]):
    return concept_data(concepts, query_type="info")


def concept_securities(concepts: List[str]):
    return concept_data(concepts, query_type="securities")


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
        description="题材指数：基本信息（投资逻辑/催化事件）与成分股（分组/重点个股/纳入理由）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-t",
        "--type",
        choices=sorted(QUERY_TYPES),
        default="info",
        help="info=基本信息；securities=成分股；all=两者都查",
    )
    parser.add_argument(
        "-c",
        "--concepts",
        default="",
        help="题材 ID 或名称，逗号分隔；纯字母数字视为 ID，否则 search，唯一完全匹配时自动查询",
    )
    parser.add_argument(
        "--concepts-file",
        default=None,
        help="从 csv 读取 concept_id / conceptId / concept_name / conceptName 列",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=SEARCH_TOP_DEFAULT,
        help="名称搜索返回条数上限（最大 10，非纯 ID 时生效）",
    )

    args = parser.parse_args()
    concepts = _parse_str_list(args.concepts)
    if not concepts and args.concepts_file:
        try:
            concepts = _load_concepts_from_file(args.concepts_file)
        except Exception as e:
            print(f"读取题材文件失败: {e}")
            sys.exit(1)
    if not concepts:
        parser.error("请提供 -c/--concepts 或 --concepts-file")

    print(concept_data(concepts, query_type=args.type, top=args.top))


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
