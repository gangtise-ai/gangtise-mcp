import os
import sys
from io import TextIOWrapper
from typing import List, Optional, Tuple

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (authorized_request, FILE_DEFAULT_LIMIT, QA_DATA_LIST_URL, QA_QUESTION_CATEGORY_CODE_MAP, QA_QUESTION_CATEGORY_LABEL, QA_SOURCE_CODE_MAP, QA_SOURCE_LABEL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, match_best)
from .security import batch_security_search  # noqa: E402

PAGE_SIZE_MAX = 500
POINTS_PER_ITEM = 0.1


def _parse_str_list(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    items = [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]
    return items or None


def _normalize_api_time(value: Optional[str], end_of_day: bool) -> Optional[str]:
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    if len(text) <= 10:
        day = text[:10]
        return f"{day} 23:59:59" if end_of_day else f"{day} 00:00:00"
    return text


def _resolve_source_list(tokens: Optional[List[str]]) -> Tuple[Optional[List[str]], Optional[str]]:
    if not tokens:
        return None, None
    resolved: List[str] = []
    for token in tokens:
        raw = str(token).strip()
        if not raw:
            continue
        if raw in QA_SOURCE_CODE_MAP:
            code = QA_SOURCE_CODE_MAP[raw]
        else:
            matched = match_best(raw, list(QA_SOURCE_CODE_MAP.keys()))
            if not matched:
                return None, f"无法识别问题来源：{raw}"
            code = QA_SOURCE_CODE_MAP[matched]
        if code not in resolved:
            resolved.append(code)
    return resolved or None, None


def _resolve_question_category_list(
    tokens: Optional[List[str]],
) -> Tuple[Optional[List[str]], Optional[str]]:
    if not tokens:
        return None, None
    resolved: List[str] = []
    for token in tokens:
        raw = str(token).strip()
        if not raw:
            continue
        if raw in QA_QUESTION_CATEGORY_CODE_MAP:
            code = QA_QUESTION_CATEGORY_CODE_MAP[raw]
        else:
            matched = match_best(raw, list(QA_QUESTION_CATEGORY_CODE_MAP.keys()))
            if not matched:
                return None, f"无法识别问题类型：{raw}"
            code = QA_QUESTION_CATEGORY_CODE_MAP[matched]
        if code not in resolved:
            resolved.append(code)
    return resolved or None, None


def _resolve_answer_important(tokens: Optional[List[str]]) -> Tuple[Optional[List[int]], Optional[str]]:
    if not tokens:
        return None, None
    values: List[int] = []
    for token in tokens:
        raw = str(token).strip().lower()
        if not raw:
            continue
        if raw in ("1", "true", "yes", "y", "是", "重要", "important"):
            val = 1
        elif raw in ("0", "false", "no", "n", "否", "不重要", "unimportant"):
            val = 0
        elif raw in ("all", "全部", "0,1", "0/1"):
            return [0, 1], None
        else:
            return None, f"无法识别 answerImportant 取值：{token}"
        if val not in values:
            values.append(val)
    return values or None, None


def _format_category_labels(categories: object) -> str:
    if not isinstance(categories, list):
        return ""
    labels = []
    for item in categories:
        code = str(item or "").strip()
        if not code:
            continue
        labels.append(QA_QUESTION_CATEGORY_LABEL.get(code, code))
    return "；".join(labels)


def _format_qa_items(rows: List[dict]) -> List[dict]:
    results = []
    for row in rows:
        source = str(row.get("source") or "").strip()
        important = row.get("answerImportant")
        question = str(row.get("question") or "").strip()
        answer = str(row.get("answer") or "").strip()
        item = {
            "标题": question,
            "文件时间": str(row.get("publishTime") or "").strip(),
            "来源": QA_SOURCE_LABEL.get(source, source),
            "提问": question,
            "回答": answer,
            "回答方": str(row.get("member") or "").strip(),
            "所属证券": str(row.get("securityCode") or "").strip(),
            "问题类型": _format_category_labels(row.get("questionCategory")),
            "涉及重要信息": "是" if important == 1 else "否",
            "类型": "投资者问答",
            "类型ID": "",
        }
        results.append(item)
    return results


def _fetch_qa_list(
    headers: dict,
    payload_base: dict,
    limit: int,
) -> Tuple[List[dict], Optional[str]]:
    all_rows: List[dict] = []
    offset = 0
    remaining = max(1, int(limit))

    while remaining > 0:
        page_size = min(remaining, PAGE_SIZE_MAX)
        payload = {**payload_base, "from": offset, "size": page_size}
        try:
            response = authorized_request("POST", QA_DATA_LIST_URL, headers=headers, json=payload, timeout=120)
        except Exception as e:
            if all_rows:
                return all_rows, f"分页请求异常: {e}"
            return [], f"接口请求异常: {e}"

        if response.status_code != 200:
            err = response.text.replace("\n", " ").replace("\r", " ").strip()
            if all_rows:
                return all_rows, err
            return [], err

        result = response.json()
        if result.get("code") not in [200, "000000"] and result.get("status") is not True:
            msg = str(result.get("msg") or result.get("message") or "请求失败")
            if all_rows:
                return all_rows, msg
            return [], msg

        data = result.get("data") or {}
        rows = data.get("list") or []
        if not isinstance(rows, list):
            return [], "接口返回 list 格式异常"
        if not rows:
            break

        all_rows.extend(rows)
        if len(rows) < page_size:
            break

        offset += len(rows)
        remaining -= len(rows)

    return all_rows[:limit], None


def _split_limit_across_groups(limit: int, group_count: int) -> List[int]:
    if group_count <= 0:
        return []
    base = max(1, int(limit)) // group_count
    remainder = max(1, int(limit)) % group_count
    return [base + (1 if i < remainder else 0) for i in range(group_count)]


def qa_finder(
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source_list: Optional[List[str]] = None,
    question_category_list: Optional[List[str]] = None,
    answer_important: Optional[List[str]] = ["1"],
    limit: int = FILE_DEFAULT_LIMIT["qa"],
):
    try:
        security_tokens = [str(s).strip() for s in (securities or []) if str(s).strip()]
        if not security_tokens:
            return format_response(
                {"state": "error", "message": "证券 securities 不能为空"},
                "qa",
            )

        headers = get_authorization_headers()

        resolved = batch_security_search(
            security_tokens,
            category=["stock", "dr"],
            headers=headers,
            output_limit=1,
        )
        if resolved.get("state") != "success":
            return format_response(
                {
                    "state": "error",
                    "message": resolved.get("message") or "证券解析失败",
                    "data": [],
                },
                "qa",
            )

        security_codes = [str(code).strip() for code in (resolved.get("codes") or []) if str(code).strip()]
        if not security_codes:
            return format_response(
                {
                    "state": "error",
                    "message": "未解析到有效证券代码，请检查证券名称或代码",
                    "data": [],
                },
                "qa",
            )

        sources, source_err = _resolve_source_list(source_list)
        if source_err:
            return format_response({"state": "error", "message": source_err}, "qa")

        categories, category_err = _resolve_question_category_list(question_category_list)
        if category_err:
            return format_response({"state": "error", "message": category_err}, "qa")

        answer_vals, answer_err = _resolve_answer_important(answer_important)
        if answer_err:
            return format_response({"state": "error", "message": answer_err}, "qa")

        payload_common: dict = {}
        start_time = _normalize_api_time(start_date, end_of_day=False)
        end_time = _normalize_api_time(end_date, end_of_day=True)
        if start_time:
            payload_common["startTime"] = start_time
        if end_time:
            payload_common["endTime"] = end_time
        if sources:
            payload_common["source"] = sources
        if categories:
            payload_common["questionCategory"] = categories
        if answer_vals is not None:
            payload_common["answerImportant"] = answer_vals

        limits = _split_limit_across_groups(limit, len(security_codes))
        all_results: List[dict] = []
        part_errors: List[str] = []

        for security_code, sub_limit in zip(security_codes, limits):
            payload_base = {**payload_common, "securityCode": security_code}
            rows, err = _fetch_qa_list(headers, payload_base, sub_limit)
            if err and not rows:
                part_errors.append(f"{security_code}：{err}")
                continue
            if err and rows:
                part_errors.append(f"{security_code}：未完整获取全部结果，{err}")
            all_results.extend(_format_qa_items(rows))

        if not all_results:
            message = "未找到相关投资者问答，建议修改查询条件"
            if part_errors:
                message += "；" + "；".join(part_errors)
            return format_response(
                {"state": "error", "message": message, "data": []},
                "qa",
            )

        all_results = all_results[:limit]
        usage_points = round(len(all_results) * POINTS_PER_ITEM, 2)
        response_data = {
            "state": "success",
            "message": "已找到相关投资者问答",
            "data": [{"data": all_results, "module": "qa", "type": "content"}],
            "usage": {"qa": usage_points},
        }
        return format_response(
            response_data,
            "qa",
            additional_message="；".join(part_errors) if part_errors else "",
        )
    except Exception as e:
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "qa",
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="投资者问答检索：按证券拉取互动平台/电话会议/调研纪要中的问答结构化数据。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-s",
        "--securities",
        required=True,
        help="证券列表，逗号分隔；可为名称、代码或拼音，如 601012.SH 或 隆基绿能",
    )
    parser.add_argument(
        "-sd",
        "--start-date",
        default="",
        help="开始时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
    )
    parser.add_argument(
        "-ed",
        "--end-date",
        default="",
        help="结束时间，格式 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS",
    )
    parser.add_argument(
        "-l",
        "--limit",
        default=FILE_DEFAULT_LIMIT["qa"],
        type=int,
        help="返回条数上限，单页最大 500（自动分页）",
    )
    parser.add_argument(
        "--source",
        default="",
        help="问题来源，逗号分隔：电话会议/互动平台/调研纪要 或 conference/interactive/survey",
    )
    parser.add_argument(
        "--question-category",
        default="",
        help="问题类型，逗号分隔；支持中文或英文 code，见 references/qa.md",
    )
    parser.add_argument(
        "--answer-important",
        default="1",
        help="是否涉及重要信息：1/是、0/否；传 0,1 或 all 表示不过滤",
    )

    args = parser.parse_args()
    check_version()

    securities = _parse_str_list(args.securities)
    source_list = _parse_str_list(args.source)
    question_category_list = _parse_str_list(args.question_category)
    answer_important = _parse_str_list(args.answer_important)

    out = qa_finder(
        securities=securities,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        source_list=source_list,
        question_category_list=question_category_list,
        answer_important=answer_important,
        limit=max(1, min(int(args.limit), PAGE_SIZE_MAX)),
    )
    if isinstance(out, str):
        print(out)
    elif hasattr(out, "write"):
        wrapper = TextIOWrapper(out, encoding="utf-8")
        print(wrapper.read())
    else:
        print(out)


if __name__ == "__main__":
    main()
