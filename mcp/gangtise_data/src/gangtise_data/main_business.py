import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import TextIOWrapper
from typing import List, Optional, Sequence

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .security import batch_security_search, resolved_code_abbr_map

from .utils import (
    normalize_securities_arg, MAIN_BUSINESS_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, parse_str_list)

# open 文档：可选指标，默认全取
_FIELD_LIST_ALL = [
    "opRevenue",
    "opRevenueYoy",
    "opRevenueRatio",
    "opCost",
    "opCostYoy",
    "opCostRatio",
    "grossProfit",
    "grossProfitYoy",
    "grossProfitRatio",
    "grossMargin",
    "grossMarginYoy",
    "grossMarginRatio",
]

# 命令行使用 Q2、Q4；open 主营接口 period 仅支持中报/年报
MAIN_BUSINESS_PERIOD_CLI_TO_API = {
    "Q2": "interim",
    "Q4": "annual",
}

BREAKDOWN_KEYS = ("product", "industry", "region")
BREAKDOWN_LABEL = {
    "product": "分产品",
    "industry": "分行业",
    "region": "分地区",
}

# 响应 fieldList 中字段 -> 中文列名（与 backend 中「分行业/分产品/分地区」等语义对齐）
API_FIELD_TO_CN = {
    "periodName": "报告期",
    "periodEndDate": "日期",
    "categoryName": "主营业务名称",
    "opRevenue": "营业收入",
    "opRevenueYoy": "营业收入同比增速(%)",
    "opRevenueRatio": "营业收入占比(%)",
    "opCost": "营业成本",
    "opCostYoy": "营业成本同比增速(%)",
    "opCostRatio": "营业成本占比(%)",
    "grossProfit": "毛利",
    "grossProfitYoy": "毛利同比增速(%)",
    "grossProfitRatio": "毛利占比(%)",
    "grossMargin": "毛利率(%)",
    "grossMarginYoy": "毛利率同比增速(%)",
    "grossMarginRatio": "毛利率占比(%)",
}

SORT_TYPE_COL = "分行业/分产品/分地区"



def _parse_main_business_body(body: dict, breakdown_key: str) -> pd.DataFrame:
    if not body or str(body.get("code", "")) != "000000" or body.get("status") is False:
        return pd.DataFrame()
    block = body.get("data") or {}
    rows = block.get("list") or []
    field_list = block.get("fieldList") or []
    if not rows or not field_list:
        return pd.DataFrame()
    security_name = (block.get("securityName") or "").strip()
    security_code = (block.get("securityCode") or "").strip()
    records = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        n = min(len(field_list), len(row))
        if n == 0:
            continue
        rec = {field_list[i]: row[i] for i in range(n)}
        records.append(rec)
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df.insert(0, "证券简称", security_name or security_code)
    df.insert(1, "证券代码", security_code)
    df[SORT_TYPE_COL] = BREAKDOWN_LABEL.get(breakdown_key, breakdown_key)
    rename = {k: v for k, v in API_FIELD_TO_CN.items() if k in df.columns}
    df = df.rename(columns=rename)
    return df


def _fetch_main_business(
    headers: dict,
    security_code: str,
    start_date: str,
    end_date: str,
    breakdown_key: str,
    period: Optional[str],
    field_list: Sequence[str],
) -> pd.DataFrame:
    payload: dict = {
        "securityCode": security_code,
        "startDate": start_date,
        "endDate": end_date,
        "fieldList": list(field_list),
        "breakdown": breakdown_key,
    }
    if period:
        payload["period"] = period
    try:
        r = authorized_request("POST", MAIN_BUSINESS_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return pd.DataFrame()
        body = r.json()
        return _parse_main_business_body(body, breakdown_key)
    except Exception:
        return pd.DataFrame()


def _main_business_for_security(
    headers: dict,
    security_code: str,
    start_date: str,
    end_date: str,
    breakdown: Optional[str],
    period: Optional[str],
    field_list: Sequence[str],
) -> pd.DataFrame:
    keys: List[str]
    if breakdown is None:
        keys = list(BREAKDOWN_KEYS)
    else:
        if breakdown not in BREAKDOWN_LABEL:
            return pd.DataFrame()
        keys = [breakdown]

    frames: List[pd.DataFrame] = []
    if len(keys) == 1:
        df = _fetch_main_business(
            headers, security_code, start_date, end_date, keys[0], period, field_list
        )
        if not df.empty:
            frames.append(df)
    else:
        with ThreadPoolExecutor(max_workers=len(keys)) as ex:
            futs = [
                ex.submit(
                    _fetch_main_business,
                    headers,
                    security_code,
                    start_date,
                    end_date,
                    k,
                    period,
                    field_list,
                )
                for k in keys
            ]
            for fut in as_completed(futs):
                part = fut.result()
                if not part.empty:
                    frames.append(part)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    if "日期" in out.columns:
        out["_dt"] = pd.to_datetime(out["日期"], errors="coerce")
        sort_cols = ["证券代码", "_dt", SORT_TYPE_COL]
        if "主营业务名称" in out.columns:
            sort_cols.append("主营业务名称")
        ascending = [True, False, True] + [True] * (len(sort_cols) - 3)
        out = out.sort_values(by=sort_cols, ascending=ascending).reset_index(drop=True)
        out = out.drop(columns=["_dt"], errors="ignore")
    return out


def main_business_data(
    securities: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    period: Optional[str] = None,
    breakdown: Optional[str] = None,
    output_dir: Optional[str] = None,
):
    usage: dict = {}
    if not get_authorization_token():
        return format_response(
            {"state": "error", "message": "未配置 gangtise 授权，无法调用 open 接口", "data": [], "usage": usage},
            "main_business", output_dir=output_dir)

    if period is not None and period not in ("interim", "annual"):
        return format_response(
            {"state": "error", "message": "period 仅支持 interim（中报）或 annual（年报）", "data": [], "usage": usage},
            "main_business", output_dir=output_dir)

    if breakdown is not None and breakdown not in BREAKDOWN_LABEL:
        return format_response(
            {
                "state": "error",
                "message": f"breakdown 须为 product / industry / region 之一，当前: {breakdown}",
                "data": [],
                "usage": usage,
            },
            "main_business", output_dir=output_dir)

    headers = get_authorization_headers()
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (date.today() - timedelta(days=365 * 3)).strftime("%Y-%m-%d")

    tokens = parse_str_list(securities)
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
                "usage": usage,
            },
            "main_business", output_dir=output_dir)
    securities_codes = resolved.get("codes") or []
    securities_abbrs = resolved.get("abbrs") or []
    abbr_map = resolved_code_abbr_map(resolved)
    for uk, uv in (resolved.get("usage") or {}).items():
        usage[uk] = usage.get(uk, 0) + (uv if isinstance(uv, (int, float)) else 0)

    if not securities_codes:
        return format_response(
            {
                "state": "error",
                "message": "未解析到有效证券代码",
                "data": [],
                "usage": usage,
            },
            "main_business", output_dir=output_dir)

    frames: List[pd.DataFrame] = []
    for abbr, code in zip(securities_abbrs, securities_codes):
        df_one = _main_business_for_security(
            headers,
            code,
            start_date,
            end_date,
            breakdown,
            period,
            _FIELD_LIST_ALL,
        )
        if not df_one.empty:
            df_one["证券简称"] = abbr_map.get(str(code).strip().upper(), abbr)
            frames.append(df_one)

    if not frames:
        return format_response(
            {"state": "error", "message": "未找到主营业务数据", "data": [], "usage": usage},
            "main_business", output_dir=output_dir)

    data = pd.concat(frames, ignore_index=True)
    data = data.sort_values(
        by=["证券代码", "日期"] if "日期" in data.columns else ["证券代码"],
        ascending=[True, False] if "日期" in data.columns else [True],
    ).reset_index(drop=True)

    skip_numeric = {"证券简称", "证券代码", "报告期", SORT_TYPE_COL, "主营业务名称", "日期"}
    numeric_cols = [c for c in data.columns if c not in skip_numeric]
    for c in numeric_cols:
        data[c] = pd.to_numeric(data[c], errors="coerce")

    col_map = {
        "证券简称": "security_abbr",
        "证券代码": "security_code",
        "日期": "date",
    }
    for col in list(data.columns):
        if col in col_map:
            data.rename(columns={col: col_map[col]}, inplace=True)
    front_columns = ["security_abbr", "security_code", "date"]
    columns = [c for c in front_columns if c in data.columns] + [
        c for c in data.columns if c not in front_columns
    ]
    data = data[columns]

    success_label = (
        "、".join(securities_abbrs[:3]) + "等"
        if len(securities_abbrs) > 3
        else "、".join(securities_abbrs)
    )

    parts = [
        {
            "data": data.to_dict(orient="records"),
            "module": "main_business",
            "type": "data",
        }
    ]
    return format_response(
        {
            "state": "success",
            "message": f"已找到{success_label}主营业务数据",
            "data": parts,
            "usage": usage,
        },
        "main_business", output_dir=output_dir)


def main():
    import argparse

    try:
        if not check_version():
            update_sh = os.path.join(script_dir, "update.sh")
            print(f"[WARNING] 存在 Gangtise data 版本更新，可以执行 {update_sh} 更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise data 版本失败\n")

    parser = argparse.ArgumentParser(
        description="查询主营构成（open main-business）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-sd", "--start-date", default=None, help="开始日期 yyyy-MM-dd，默认 end 往前三年")
    parser.add_argument("-ed", "--end-date", default=None, help="结束日期 yyyy-MM-dd，默认今天")
    parser.add_argument("--securities", default=None, help="证券：完整代码/名称/拼音等，逗号分隔")
    parser.add_argument(
        "--breakdown",
        default=None,
        choices=["product", "industry", "region"],
        help="拆分维度；不传则并发拉取 product / industry / region 三种",
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="结果保存目录路径，建议使用绝对路径",
    )

    args = parser.parse_args()

    try:
        securities = normalize_securities_arg(args.securities)
    except Exception as e:
        print(f"解析 --securities 失败: {e}")
        sys.exit(1)

    if not securities:
        parser.error("必须提供 --securities")

    api_period = None
    if args.period is not None and str(args.period).strip():
        k = str(args.period).strip().upper()
        if k not in MAIN_BUSINESS_PERIOD_CLI_TO_API:
            parser.error(
                "主营构成 --period 仅支持 Q2（中报）或 Q4（年报）"
            )
        api_period = MAIN_BUSINESS_PERIOD_CLI_TO_API[k]

    out = main_business_data(
        securities=securities,
        start_date=args.start_date,
        end_date=args.end_date,
        period=api_period,
        breakdown=args.breakdown,
        output_dir=args.output_dir or None,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
