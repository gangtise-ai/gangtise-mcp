import os
import sys
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple
import requests
from io import TextIOWrapper

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (authorized_request, MANAGEMENT_DISCUSS_DIMENSION_LABEL, MANAGEMENT_DISCUSS_DIMENSION_MAP, MANAGEMENT_DISCUSS_FROM_ANNOUNCEMENT_URL, MANAGEMENT_DISCUSS_FROM_EARNINGS_CALL_URL, MANAGEMENT_DISCUSS_TYPE_MAP, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, match_best)
from .security import batch_security_search

EARNINGS_CALL_ALL_DIMENSIONS = [
    "businessOperation",
    "financialPerformance",
    "developmentAndRisk",
]


def _parse_str_list(raw: str) -> Optional[List[str]]:
    if not raw:
        return None
    items = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    return items or None


def _normalize_discuss_type(raw: str) -> Optional[str]:
    if not raw:
        return None
    token = str(raw).strip()
    if token in ("announcement", "earningsCall"):
        return token
    if token in MANAGEMENT_DISCUSS_TYPE_MAP:
        mapped = MANAGEMENT_DISCUSS_TYPE_MAP[token]
        if mapped in ("announcement", "earningsCall"):
            return mapped
    matched = match_best(token, list(MANAGEMENT_DISCUSS_TYPE_MAP.keys()))
    if not matched:
        return None
    mapped = MANAGEMENT_DISCUSS_TYPE_MAP[matched]
    if mapped in ("announcement", "earningsCall"):
        return mapped
    return None


def _normalize_discussion_dimension(raw: str) -> Tuple[Optional[str], Optional[str]]:
    if not raw:
        return None, "discussionDimension 不能为空"
    token = str(raw).strip()
    if token in MANAGEMENT_DISCUSS_DIMENSION_MAP:
        dim = MANAGEMENT_DISCUSS_DIMENSION_MAP[token]
    else:
        matched = match_best(token, list(MANAGEMENT_DISCUSS_DIMENSION_MAP.keys()))
        if not matched:
            return None, f"无法识别讨论维度：{token}"
        dim = MANAGEMENT_DISCUSS_DIMENSION_MAP[matched]
    return dim, None


def _validate_report_date(report_date: str, discuss_type: str) -> Optional[str]:
    if not report_date:
        return "reportDate 不能为空"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
        return f"reportDate 格式须为 yyyy-MM-dd，当前为：{report_date}"
    month_day = report_date[5:]
    if discuss_type == "announcement":
        if month_day not in ("06-30", "12-31"):
            return "半年报/年报报告期须为 xxxx-06-30 或 xxxx-12-31"
    elif discuss_type == "earningsCall":
        if month_day not in ("03-31", "06-30", "09-30", "12-31"):
            return "业绩会报告期须为 xxxx-03-31、xxxx-06-30、xxxx-09-30 或 xxxx-12-31"
    return None


def _format_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, list):
        parts = [str(x).strip() for x in content if str(x).strip()]
        return "\n\n".join(parts)
    return str(content).strip()


def _fetch_management_discuss(
    headers: dict,
    url: str,
    security_code: str,
    report_date: str,
    discussion_dimension: str,
) -> Tuple[Optional[dict], Optional[str]]:
    payload = {
        "securityCode": security_code,
        "reportDate": report_date,
        "discussionDimension": discussion_dimension,
    }
    try:
        response = authorized_request("POST", url, headers=headers, json=payload, timeout=120)
        if response.status_code != 200:
            return None, f"接口请求失败: HTTP {response.status_code}"
        body = response.json()
    except Exception as e:
        return None, f"接口请求异常: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        msg = body.get("msg") or body.get("message") or response.text[:500]
        return None, f"接口返回错误: {msg}"

    data = body.get("data") or {}
    if not data:
        return None, "接口未返回数据"
    return data, None


def _merge_dimension_contents(parts: List[Tuple[str, str]]) -> str:
    sections = []
    for dim, content in parts:
        text = _format_content(content)
        if not text:
            continue
        label = MANAGEMENT_DISCUSS_DIMENSION_LABEL.get(dim, dim)
        sections.append(f"## {label}\n\n{text}")
    return "\n\n".join(sections)


def _fetch_earnings_call_all_dimensions(
    headers: dict,
    url: str,
    security_code: str,
    report_date: str,
) -> Tuple[Optional[str], int, List[str]]:
    """并发拉取业绩会三个维度，合并正文。返回 (merged_content, success_count, errors)。"""
    parts: List[Tuple[str, str]] = []
    errors: List[str] = []
    success_count = 0

    with ThreadPoolExecutor(max_workers=len(EARNINGS_CALL_ALL_DIMENSIONS)) as executor:
        futures = {
            executor.submit(
                _fetch_management_discuss,
                headers,
                url,
                security_code,
                report_date,
                dim,
            ): dim
            for dim in EARNINGS_CALL_ALL_DIMENSIONS
        }
        for future in as_completed(futures):
            dim = futures[future]
            dim_label = MANAGEMENT_DISCUSS_DIMENSION_LABEL.get(dim, dim)
            try:
                data, err = future.result()
            except Exception as e:
                errors.append(f"{dim_label}：{e}")
                continue
            if err:
                errors.append(f"{dim_label}：{err}")
                continue
            content = data.get("content") if data else None
            if not _format_content(content):
                errors.append(f"{dim_label}：未找到相关管理层讨论内容")
                continue
            parts.append((dim, content))
            success_count += 1

    parts_by_dim = {d: content for d, content in parts}
    ordered_parts = [
        (dim, parts_by_dim[dim])
        for dim in EARNINGS_CALL_ALL_DIMENSIONS
        if dim in parts_by_dim
    ]
    merged = _merge_dimension_contents(ordered_parts)
    if not merged:
        return None, 0, errors
    return merged, success_count, errors


def _format_result_item(
    security_code: str,
    security_abbr: str,
    report_date: str,
    discussion_dimension: str,
    discuss_type: str,
    content,
) -> dict:
    dim_label = MANAGEMENT_DISCUSS_DIMENSION_LABEL.get(discussion_dimension, discussion_dimension)
    type_label = MANAGEMENT_DISCUSS_TYPE_MAP.get(discuss_type, discuss_type)
    sec_display = f"{security_abbr}({security_code})" if security_abbr else security_code
    return {
        "标题": f"{sec_display} {report_date} {dim_label}",
        "文件时间": report_date,
        "所属证券": sec_display,
        "讨论维度": dim_label,
        "来源": type_label,
        "正文": _format_content(content),
        "类型": "管理层讨论与分析",
        "类型ID": "",
    }


def management_discuss_finder(
    discuss_type: str,
    report_date: str,
    securities: List[str],
    discussion_dimension: str,
):
    try:
        headers = get_authorization_headers()

        normalized_type = _normalize_discuss_type(discuss_type)
        if not normalized_type:
            return format_response(
                {"state": "error", "message": f"无法识别 --type：{discuss_type}", "data": []},
                "management_discuss",
            )

        date_err = _validate_report_date(report_date, normalized_type)
        if date_err:
            return format_response(
                {"state": "error", "message": date_err, "data": []},
                "management_discuss",
            )

        dim, dim_err = _normalize_discussion_dimension(discussion_dimension)
        if dim_err:
            return format_response(
                {"state": "error", "message": dim_err, "data": []},
                "management_discuss",
            )

        url = (
            MANAGEMENT_DISCUSS_FROM_ANNOUNCEMENT_URL
            if normalized_type == "announcement"
            else MANAGEMENT_DISCUSS_FROM_EARNINGS_CALL_URL
        )

        tokens = [str(s).strip() for s in securities if str(s).strip()]
        resolved = batch_security_search(
            tokens,
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
                "management_discuss",
            )

        codes = resolved.get("codes") or []
        abbrs = resolved.get("abbrs") or []
        if not codes:
            return format_response(
                {"state": "error", "message": "未解析到有效证券代码", "data": []},
                "management_discuss",
            )

        all_results = []
        errors = []
        usage_count = 0
        for i, security_code in enumerate(codes):
            security_abbr = abbrs[i] if i < len(abbrs) else ""
            label = f"{security_abbr}({security_code})" if security_abbr else security_code

            if normalized_type == "earningsCall" and dim == "all":
                merged_content, success_count, dim_errors = _fetch_earnings_call_all_dimensions(
                    headers, url, security_code, report_date
                )
                if not merged_content:
                    err_msg = "；".join(dim_errors) if dim_errors else "未找到相关管理层讨论内容"
                    errors.append(f"{label}：{err_msg}")
                    continue
                usage_count += success_count
                if dim_errors:
                    errors.append(f"{label}（部分维度未取全：{'；'.join(dim_errors)}）")
                all_results.append(
                    _format_result_item(
                        security_code=security_code,
                        security_abbr=security_abbr,
                        report_date=report_date,
                        discussion_dimension="all",
                        discuss_type=normalized_type,
                        content=merged_content,
                    )
                )
                continue

            data, err = _fetch_management_discuss(
                headers, url, security_code, report_date, dim
            )
            if err:
                errors.append(f"{label}：{err}")
                continue
            content = data.get("content")
            if not _format_content(content):
                errors.append(f"{label}：未找到相关管理层讨论内容")
                continue
            usage_count += 1
            all_results.append(
                _format_result_item(
                    security_code=security_code,
                    security_abbr=security_abbr,
                    report_date=data.get("reportDate") or report_date,
                    discussion_dimension=data.get("discussionDimension") or dim,
                    discuss_type=normalized_type,
                    content=content,
                )
            )

        if not all_results:
            msg = "；".join(errors) if errors else "未找到相关管理层讨论内容，建议修改查询条件"
            return format_response(
                {"state": "error", "message": msg, "data": []},
                "management_discuss",
            )

        type_label = MANAGEMENT_DISCUSS_TYPE_MAP.get(normalized_type, normalized_type)
        msg = f"已获取{type_label}管理层讨论与分析内容"
        response_data = {
            "state": "success",
            "message": msg,
            "data": [{"data": all_results, "module": "management_discuss", "type": "content"}],
            "usage": {"management_discuss": 10 * usage_count},
        }
        if errors:
            response_data["message"] = f"{msg}（部分证券未取全：{'；'.join(errors)}）"
        return format_response(response_data, "management_discuss")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return format_response(
            {"state": "error", "message": str(e), "data": [], "usage": {}},
            "management_discuss",
        )


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="管理层讨论与分析：通过 --type 区分半年报/年报（announcement）与业绩会（earningsCall）两个接口。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-t",
        "--type",
        required=True,
        help="数据来源类型：announcement（半年报/年报）或 earningsCall（业绩会）；也支持中文「半年报年报」「业绩会」",
    )
    parser.add_argument(
        "-rd",
        "--report-date",
        required=True,
        help="报告期，格式 yyyy-MM-dd；announcement 为 xxxx-06-30/xxxx-12-31，earningsCall 为季末日期",
    )
    parser.add_argument(
        "--securities",
        required=True,
        help="证券列表，逗号分隔，可为证券名称、代码或拼音首字母，如 000001.SZ 或 平安银行",
    )
    parser.add_argument(
        "-dd",
        "--discussion-dimension",
        required=True,
        help="讨论维度：businessOperation / financialPerformance / developmentAndRisk / all",
    )
    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise skills 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise skills 版本失败\n")

    args = parser.parse_args()
    securities = _parse_str_list(args.securities)
    if not securities:
        print(format_response(
            {"state": "error", "message": "securities 不能为空", "data": []},
            "management_discuss",
        ))
        return

    out = management_discuss_finder(
        discuss_type=args.type,
        report_date=args.report_date,
        securities=securities,
        discussion_dimension=args.discussion_dimension,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
