import os
import sys
from datetime import date, timedelta
from io import TextIOWrapper
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .security import batch_security_search, resolved_code_abbr_map

from .utils import (FUND_FLOW_CN, FUND_FLOW_DAILY_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, parse_str_list)

# 默认返回全部净流入相关字段（securityCode、tradeDate 由接口自动前置）
DEFAULT_NET_INFLOW_FIELDS = [
    "smallNetInflow",
    "mediumNetInflow",
    "largeNetInflow",
    "xlargeNetInflow",
    "totalNetInflow",
    "mainNetInflow",
]

_CN_TO_FIELD_EN = {v: k for k, v in FUND_FLOW_CN.items()}


def _build_fund_flow_cn_lookup() -> Dict[str, str]:
    """中文列名查找表：完整名及去掉（单位：元）/（%）/金额 等后缀的简写。"""
    lookup: Dict[str, str] = {}
    for en, cn in FUND_FLOW_CN.items():
        if en in {"securityCode", "tradeDate"}:
            continue
        keys: List[str] = [cn]
        if cn.endswith("（单位：元）"):
            base = cn[: -len("（单位：元）")]
            keys.append(base)
            if base.endswith("金额"):
                keys.append(base[: -len("金额")])
        elif cn.endswith("（%）"):
            keys.append(cn[: -len("（%）")])
        for key in keys:
            k = key.strip()
            if k and k not in lookup:
                lookup[k] = en
    return lookup


_FUND_FLOW_CN_LOOKUP = _build_fund_flow_cn_lookup()

ALL_MARKET_DATE_LOOKBACK_DAYS = 15


def _load_security_codes_from_file(path: str) -> List[str]:
    full_path = path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"证券文件不存在: {path}")
    df = pd.read_csv(full_path)
    if "security_code" not in df.columns:
        raise ValueError("证券文件须包含 security_code 列（完整代码或证券名称等关键词）")
    return [str(x) for x in df["security_code"].dropna().tolist()]


def _resolve_field_token(token: str) -> Optional[str]:
    t = str(token).strip()
    if not t:
        return None
    if t in FUND_FLOW_CN:
        return t
    if t in _CN_TO_FIELD_EN:
        return _CN_TO_FIELD_EN[t]
    if t in _FUND_FLOW_CN_LOOKUP:
        return _FUND_FLOW_CN_LOOKUP[t]
    return t


def _prepare_field_list(field_list: Optional[List[str]]) -> List[str]:
    if not field_list:
        return list(DEFAULT_NET_INFLOW_FIELDS)
    out: List[str] = []
    seen: Set[str] = set()
    for raw in field_list:
        en = _resolve_field_token(raw)
        if not en or en in {"securityCode", "tradeDate"}:
            continue
        if en not in seen:
            out.append(en)
            seen.add(en)
    return out or list(DEFAULT_NET_INFLOW_FIELDS)


def _parse_fund_flow_body(body: dict) -> pd.DataFrame:
    if not body or str(body.get("code", "")) != "000000" or body.get("status") is False:
        return pd.DataFrame()
    block = body.get("data") or {}
    field_list = block.get("fieldList") or []
    rows = block.get("list") or []
    if not field_list or not rows:
        return pd.DataFrame()
    records = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        n = min(len(field_list), len(row))
        if n == 0:
            continue
        records.append({field_list[i]: row[i] for i in range(n)})
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _fetch_fund_flow(
    headers: dict,
    security_list: List[str],
    start_date: Optional[str],
    end_date: Optional[str],
    limit: int,
    field_list: List[str],
) -> Tuple[pd.DataFrame, Optional[str]]:
    payload: dict = {
        "securityList": security_list,
        "limit": limit,
        "fieldList": field_list,
    }
    if start_date:
        payload["startDate"] = start_date
    if end_date:
        payload["endDate"] = end_date
    try:
        r = authorized_request("POST", FUND_FLOW_DAILY_URL, headers=headers, json=payload, timeout=300)
        if r.status_code != 200:
            return pd.DataFrame(), r.text
        body = r.json()
        if str(body.get("code", "")) != "000000" or body.get("status") is False:
            return pd.DataFrame(), body.get("msg") or "接口返回失败"
        data = _parse_fund_flow_body(body)
        if data.empty:
            return pd.DataFrame(), "未找到资金流向数据"
        return data, None
    except Exception as e:
        return pd.DataFrame(), str(e)


def _fetch_all_market_with_date_fallback(
    headers: dict,
    security_list: List[str],
    limit: int,
    field_list: List[str],
    max_lookback: int = ALL_MARKET_DATE_LOOKBACK_DAYS,
) -> Tuple[pd.DataFrame, Optional[str], Optional[str]]:
    """全市场且未指定日期时：从当天起逐日向前查询，直到取到数据或达到回退上限。"""
    today = date.today()
    last_err: Optional[str] = None
    for offset in range(max_lookback):
        query_date = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        data, err = _fetch_fund_flow(
            headers=headers,
            security_list=security_list,
            start_date=query_date,
            end_date=query_date,
            limit=limit,
            field_list=field_list,
        )
        if err:
            last_err = err
            continue
        if not data.empty:
            return data, None, query_date
        last_err = "未找到资金流向数据"
    return pd.DataFrame(), last_err, None


def _apply_cn_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {k: v for k, v in FUND_FLOW_CN.items() if k in df.columns}
    out = df.rename(columns=rename_map)
    if "tradeDate" in df.columns and "交易日期" not in out.columns:
        out = out.rename(columns={"tradeDate": "交易日期"})
    if "securityCode" in df.columns and "证券代码" not in out.columns:
        out = out.rename(columns={"securityCode": "证券代码"})
    return out


def _format_fund_flow_df(df: pd.DataFrame, abbr_map: Dict[str, str]) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    if "securityCode" in df.columns:
        codes = df["securityCode"].astype(str).str.strip().str.upper()
        df["证券简称"] = codes.map(lambda c: abbr_map.get(c, ""))
        df["securityCode"] = codes
    if "tradeDate" in df.columns:
        df["tradeDate"] = pd.to_datetime(df["tradeDate"], errors="coerce").dt.strftime("%Y-%m-%d")

    for c in df.columns:
        if c in {"证券简称", "securityCode", "tradeDate"}:
            continue
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = _apply_cn_columns(df)
    if "日期" not in df.columns and "交易日期" in df.columns:
        df = df.rename(columns={"交易日期": "日期"})

    sort_cols = [c for c in ["证券代码", "日期"] if c in df.columns]
    if sort_cols:
        asc = [True, False][: len(sort_cols)]
        df = df.sort_values(by=sort_cols, ascending=asc).reset_index(drop=True)

    metric_cols = [c for c in df.columns if c not in {"证券简称", "证券代码", "日期"}]
    for c in metric_cols:
        df[c] = df[c].round(2)

    col_map = {
        "证券简称": "security_abbr",
        "证券代码": "security_code",
        "日期": "date",
    }
    for col in list(df.columns):
        if col in col_map:
            df = df.rename(columns={col: col_map[col]})
    front = ["security_abbr", "security_code", "date"]
    df = df[[c for c in front if c in df.columns] + [c for c in df.columns if c not in front]]
    if "security_abbr" in df.columns:
        df = df.drop(columns=["security_abbr"])
    return df


def _title_for_df(df: pd.DataFrame, all_market: bool) -> str:
    if all_market:
        return "全市场A股资金流向数据"
    if "security_code" not in df.columns:
        return "资金流向数据"
    codes = df["security_code"].dropna().astype(str).str.upper().unique().tolist()
    codes.sort()
    if not codes:
        return "资金流向数据"
    if len(codes) == 1:
        return f"{codes[0]}资金流向数据"
    return f"{codes[0]}等{len(codes)}只标的资金流向数据"


def fund_flow_data(
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 5000,
    all_market: bool = False,
    field_list: Optional[List[str]] = None,
    output_dir: Optional[str] = None,
):
    usage: dict = {}
    if not get_authorization_token():
        return format_response(
            {"state": "error", "message": "未配置 gangtise 授权，无法调用 open 接口", "data": [], "usage": usage},
            "fund_flow", output_dir=output_dir)

    headers = get_authorization_headers()
    capped_limit = max(1, min(int(limit), 10000))
    api_fields = _prepare_field_list(field_list)
    abbr_map: Dict[str, str] = {}
    skip_note: Optional[str] = None
    fallback_note: Optional[str] = None

    if all_market:
        security_list = ["aShares"]
    else:
        tokens = parse_str_list(securities)
        if not tokens:
            return format_response(
                {
                    "state": "error",
                    "message": "请指定 --securities / --securities-file，或使用 --all-market",
                    "data": [],
                    "usage": usage,
                },
                "fund_flow", output_dir=output_dir)
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
                "fund_flow", output_dir=output_dir)
        codes = resolved.get("codes") or []
        types = list(resolved.get("types") or [])
        abbr_map = resolved_code_abbr_map(resolved)
        for uk, uv in (resolved.get("usage") or {}).items():
            usage[uk] = usage.get(uk, 0) + (uv if isinstance(uv, (int, float)) else 0)
        if not codes:
            return format_response(
                {
                    "state": "error",
                    "message": "未解析到有效证券代码",
                    "data": [],
                    "usage": usage,
                },
                "fund_flow", output_dir=output_dir)

        a_codes: List[str] = []
        skipped: List[str] = []
        for code, st in zip(codes, types + [""] * max(0, len(codes) - len(types))):
            cu = str(code).strip().upper()
            if st == "A股" or cu.endswith((".SH", ".SZ", ".BJ")):
                a_codes.append(cu)
            else:
                skipped.append(f"{cu}（{st or '非A股'}）")
        if skipped:
            tail = "、".join(skipped[:6])
            if len(skipped) > 6:
                tail += "…"
            skip_note = f"[WARNING]资金流向仅支持A股，已跳过：{tail}"
        if not a_codes:
            msg = skip_note or "未找到支持的A股证券代码（资金流向仅支持上交所/深交所/北交所）"
            return format_response(
                {"state": "error", "message": msg, "data": [], "usage": usage},
                "fund_flow", output_dir=output_dir)
        security_list = list(dict.fromkeys(a_codes))

    if all_market and not start_date and not end_date:
        data, err, used_date = _fetch_all_market_with_date_fallback(
            headers=headers,
            security_list=security_list,
            limit=capped_limit,
            field_list=api_fields,
        )
        if used_date and used_date != date.today().strftime("%Y-%m-%d"):
            fallback_note = f"当日（{date.today().strftime('%Y-%m-%d')}）无数据，已回退至 {used_date}"
    else:
        data, err = _fetch_fund_flow(
            headers=headers,
            security_list=security_list,
            start_date=start_date,
            end_date=end_date,
            limit=capped_limit,
            field_list=api_fields,
        )
    if err or data.empty:
        msg = err or "未找到资金流向数据"
        if skip_note:
            msg = f"{skip_note}；{msg}"
        return format_response(
            {"state": "error", "message": msg, "data": [], "usage": usage},
            "fund_flow", output_dir=output_dir)

    data = _format_fund_flow_df(data, abbr_map)
    title = _title_for_df(data, all_market)
    msg = f"已找到{title}"
    if fallback_note:
        msg += f"\n{fallback_note}"
    if skip_note:
        msg += f"\n{skip_note}"

    parts = [{"data": data.to_dict(orient="records"), "module": "fund_flow_daily", "type": "data"}]
    return format_response(
        {
            "state": "success",
            "message": msg,
            "data": parts,
            "usage": usage,
        },
        "fund_flow", output_dir=output_dir)


def main():
    import argparse

    try:
        if not check_version():
            update_sh = os.path.join(script_dir, "update.sh")
            print(f"[WARNING] 存在 Gangtise data 版本更新，可以执行 {update_sh} 更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise data 版本失败\n")

    parser = argparse.ArgumentParser(
        description="查询 A 股日资金流向（open fund-flow/daily）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-sd", "--start-date", default=None, help="开始日期 yyyy-MM-dd；全市场未指定时默认当天并向前回退取数")
    parser.add_argument("-ed", "--end-date", default=None, help="结束日期 yyyy-MM-dd；全市场未指定时默认当天并向前回退取数")
    parser.add_argument(
        "--securities",
        default=None,
        help="证券逗号分隔：完整代码或名称/拼音等；与 --all-market 二选一",
    )
    parser.add_argument(
        "--securities-file",
        default=None,
        help="csv 含 security_code 列（代码或名称）",
    )
    parser.add_argument(
        "--all-market",
        action="store_true",
        help="查询全市场 A 股（securityList=[aShares]）；未指定 -sd/-ed 时默认查当天，无数据则最多向前回退 15 天",
    )
    parser.add_argument("-l", "--limit", type=int, default=5000, help="单次请求最大行数（上限 10000）")
    parser.add_argument(
        "--field-list",
        default=None,
        help="指定字段英文名或中文名，逗号分隔；不传则默认返回全部净流入字段",
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="结果保存目录路径，建议使用绝对路径",
    )

    args = parser.parse_args()

    if args.all_market and (args.securities or args.securities_file):
        parser.error("--all-market 时不要使用 --securities / --securities-file")

    securities: Optional[List[str]] = None
    if args.securities:
        securities = [x.strip() for x in args.securities.replace("，", ",").split(",") if x.strip()]
    if not securities and args.securities_file:
        try:
            securities = _load_security_codes_from_file(args.securities_file)
        except Exception as e:
            print(f"根据证券文件解析证券失败: {e}")
            sys.exit(1)
    if not args.all_market and not securities:
        parser.error("请指定 --securities / --securities-file，或使用 --all-market")

    fl: Optional[List[str]] = None
    if args.field_list:
        fl = [
            x.strip()
            for x in args.field_list.replace("，", ",").replace("、", ",").split(",")
            if x.strip()
        ]

    out = fund_flow_data(
        securities=securities,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        all_market=args.all_market,
        field_list=fl,
        output_dir=args.output_dir or None,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
