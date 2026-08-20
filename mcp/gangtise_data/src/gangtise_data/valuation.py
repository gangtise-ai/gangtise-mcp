import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import TextIOWrapper
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (authorized_request, VALUATION_URL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, parse_str_list)

from .security import batch_security_search, resolved_code_abbr_map

# 与 open 接口文档一致；并发拉取后按 backend valuation 的列语义组装
VALUATION_INDICATORS: List[str] = ["peTtm", "psTtm", "pbMrq", "peg", "pcfTtm", "em"]

INDICATOR_CN: Dict[str, str] = {
    "peTtm": "市盈率TTM",
    "psTtm": "市销率TTM",
    "pbMrq": "市净率MRQ",
    "peg": "PEG",
    "pcfTtm": "市现率TTM",
    "em": "企业倍数",
}

# 与 backend 中 abs(range_year)=3 时「在N年中所处分位」一致
QUANTILE_YEAR_LABEL = 1

# 未传起止日期时：先查当天；无有效数据则向前回退至多 LOOKBACK_DAYS 天（含当天）
LOOKBACK_DAYS = 7

_FIELD_LIST = ["value", "percentileRank"]


def _load_security_codes_from_file(path: str) -> List[str]:
    """读取 security_code 列；可为完整代码或名称，后续由 resolve_security_inputs 解析。"""
    full_path = path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"证券文件不存在: {path}")
    df = pd.read_csv(full_path)
    if "security_code" not in df.columns:
        raise ValueError("证券文件须包含 security_code 列（完整代码或证券名称）")
    return [str(x) for x in df["security_code"].dropna().tolist()]


def _is_valid_metric_value(value: object) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return True


def _day_has_valid_metrics(cols: Dict[str, object]) -> bool:
    return any(_is_valid_metric_value(v) for v in cols.values())


def _date_map_has_valid(merged_by_date: Dict[str, Dict[str, object]]) -> bool:
    return any(_day_has_valid_metrics(cols) for cols in merged_by_date.values())


def _keep_latest_valid_day(
    merged_by_date: Dict[str, Dict[str, object]],
) -> Dict[str, Dict[str, object]]:
    for d in sorted(merged_by_date.keys(), reverse=True):
        if _day_has_valid_metrics(merged_by_date[d]):
            return {d: merged_by_date[d]}
    return {}


def _parse_valuation_analysis_body(
    body: dict,
    indicator: str,
    quantile_years: int,
) -> Dict[str, Dict[str, object]]:
    """将单次估值分析接口 data 转为 {日期字符串: {列名: 值}}。"""
    if not body or str(body.get("code", "")) != "000000" or body.get("status") is False:
        return {}
    block = body.get("data") or {}
    field_list = block.get("fieldList") or []
    rows = block.get("list") or []
    if not field_list or not rows:
        return {}
    idx = {name: i for i, name in enumerate(field_list)}
    i_td = idx.get("tradeDate")
    i_val = idx.get("value")
    i_pct = idx.get("percentileRank")
    if i_td is None or i_val is None or i_pct is None:
        return {}
    cn = INDICATOR_CN.get(indicator, indicator)
    col_v = cn
    col_q = f"{cn}在时间范围内所处分位"
    out: Dict[str, Dict[str, object]] = {}
    max_i = max(i_td, i_val, i_pct)
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) <= max_i:
            continue
        raw_d = row[i_td]
        if raw_d is None:
            continue
        ds = str(raw_d).strip()
        if len(ds) >= 10:
            ds = ds[:10]
        if not ds:
            continue
        if ds not in out:
            out[ds] = {}
        out[ds][col_v] = row[i_val]
        out[ds][col_q] = row[i_pct]
    return out


def _fetch_one_indicator(
    headers: dict,
    security_code: str,
    indicator: str,
    start_date: str,
    end_date: str,
    limit: int,
) -> Tuple[str, Dict[str, Dict[str, object]]]:
    payload = {
        "securityCode": security_code,
        "indicator": indicator,
        "startDate": start_date,
        "endDate": end_date,
        "limit": limit,
        "fieldList": list(_FIELD_LIST),
    }
    try:
        r = authorized_request("POST", 
            VALUATION_URL,
            headers=headers,
            json=payload,
            timeout=120,
        )
        if r.status_code != 200:
            return indicator, {}
        body = r.json()
        merged = _parse_valuation_analysis_body(body, indicator, QUANTILE_YEAR_LABEL)
        return indicator, merged
    except Exception:
        return indicator, {}


def _fetch_indicators_merged(
    headers: dict,
    security_code: str,
    start_date: str,
    end_date: str,
    limit: int,
) -> Dict[str, Dict[str, object]]:
    date_maps: List[Dict[str, Dict[str, object]]] = []
    with ThreadPoolExecutor(max_workers=len(VALUATION_INDICATORS)) as ex:
        futs = [
            ex.submit(
                _fetch_one_indicator,
                headers,
                security_code,
                ind,
                start_date,
                end_date,
                limit,
            )
            for ind in VALUATION_INDICATORS
        ]
        for fut in as_completed(futs):
            _, part = fut.result()
            if part:
                date_maps.append(part)

    merged_by_date: Dict[str, Dict[str, object]] = {}
    for part in date_maps:
        for d, cols in part.items():
            if d not in merged_by_date:
                merged_by_date[d] = {}
            merged_by_date[d].update(cols)
    return merged_by_date


def _resolve_explicit_dates(
    start_date: Optional[str],
    end_date: Optional[str],
) -> Tuple[str, str]:
    """显式日期：缺省一侧补齐为另一侧或今天；不触发「当天回退 7 日」逻辑。"""
    today_s = date.today().strftime("%Y-%m-%d")
    end_s = (end_date or "").strip() or today_s
    start_s = (start_date or "").strip() or end_s
    return start_s, end_s


def _resolve_auto_latest_merged(
    headers: dict,
    security_code: str,
    limit: int,
) -> Dict[str, Dict[str, object]]:
    """未传日期：先查当天；无有效值则查 [今天-6, 今天]，并保留最近一个有效交易日。"""
    today = date.today()
    today_s = today.strftime("%Y-%m-%d")
    merged = _fetch_indicators_merged(headers, security_code, today_s, today_s, limit)
    if _date_map_has_valid(merged):
        return _keep_latest_valid_day(merged)

    start_s = (today - timedelta(days=LOOKBACK_DAYS - 1)).strftime("%Y-%m-%d")
    merged = _fetch_indicators_merged(headers, security_code, start_s, today_s, limit)
    if not _date_map_has_valid(merged):
        return {}
    return _keep_latest_valid_day(merged)


def _build_valuation_df(
    security_abbr: str,
    security_code: str,
    merged_by_date: Dict[str, Dict[str, object]],
    start_date: Optional[str],
    end_date: Optional[str],
) -> pd.DataFrame:
    if not merged_by_date:
        return pd.DataFrame()

    records = []
    for d in sorted(merged_by_date.keys(), reverse=True):
        row = {
            "证券简称": security_abbr,
            "证券代码": security_code,
            "日期": d,
        }
        row.update(merged_by_date[d])
        records.append(row)

    df = pd.DataFrame(records)
    base_cols = ["证券简称", "证券代码", "日期"]
    ordered_metric_cols: List[str] = []
    for ind in VALUATION_INDICATORS:
        cn = INDICATOR_CN[ind]
        q = f"{cn}在{QUANTILE_YEAR_LABEL}年中所处分位"
        if cn in df.columns:
            ordered_metric_cols.append(cn)
        if q in df.columns:
            ordered_metric_cols.append(q)
    extra = [c for c in df.columns if c not in base_cols + ordered_metric_cols]
    df = df[base_cols + [c for c in ordered_metric_cols if c in df.columns] + extra]

    for c in df.columns:
        if c not in base_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if start_date:
        sd = pd.to_datetime(start_date, errors="coerce")
        df = df[pd.to_datetime(df["日期"], errors="coerce") >= sd]
    if end_date:
        ed = pd.to_datetime(end_date, errors="coerce")
        df = df[pd.to_datetime(df["日期"], errors="coerce") <= ed]
    df = df.reset_index(drop=True)
    if not df.empty:
        df = df.sort_values(by=["日期"], ascending=False).reset_index(drop=True)
    df = df.drop(columns=["证券简称"])
    return df


def _valuation_df_for_security(
    headers: dict,
    security_abbr: str,
    security_code: str,
    start_date: Optional[str],
    end_date: Optional[str],
    limit: int,
) -> pd.DataFrame:
    auto_latest = not (start_date or "").strip() and not (end_date or "").strip()
    if auto_latest:
        merged_by_date = _resolve_auto_latest_merged(headers, security_code, limit)
        return _build_valuation_df(security_abbr, security_code, merged_by_date, None, None)

    start_s, end_s = _resolve_explicit_dates(start_date, end_date)
    merged_by_date = _fetch_indicators_merged(headers, security_code, start_s, end_s, limit)
    return _build_valuation_df(security_abbr, security_code, merged_by_date, start_s, end_s)


def valuation_data(
    securities: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 2000,
):
    usage: dict = {}
    if not get_authorization_token():
        return format_response(
            {"state": "error", "message": "未配置 gangtise 授权，无法调用 open 接口", "data": [], "usage": usage},
            "valuation",
        )

    headers = get_authorization_headers()

    # 未传日期保持 None：由 _valuation_df_for_security 按「当天 → 7 日内回退」解析
    start_date = (start_date or "").strip() or None
    end_date = (end_date or "").strip() or None

    resolved = batch_security_search(
        parse_str_list(securities),
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
            "valuation",
        )
    securities_codes = resolved.get("codes") or []
    securities_abbrs = resolved.get("abbrs") or []
    abbr_map = resolved_code_abbr_map(resolved)
    for uk, uv in (resolved.get("usage") or {}).items():
        usage[uk] = usage.get(uk, 0) + (uv if isinstance(uv, (int, float)) else 0)

    frames: List[pd.DataFrame] = []
    for abbr, code in zip(securities_abbrs, securities_codes):
        df_one = _valuation_df_for_security(
            headers,
            abbr_map.get(str(code).strip().upper(), abbr),
            code,
            start_date,
            end_date,
            limit,
        )
        if not df_one.empty:
            frames.append(df_one)

    if not frames:
        return format_response(
            {"state": "error", "message": "未找到估值数据", "data": [], "usage": usage},
            "valuation",
        )

    valuation_data_df = pd.concat(frames, ignore_index=True)
    valuation_data_df = valuation_data_df.sort_values(
        by=["证券代码", "日期"], ascending=[True, False]
    ).reset_index(drop=True)

    col_map = {
        "证券简称": "security_abbr",
        "证券代码": "security_code",
        "日期": "date",
    }
    for col in list(valuation_data_df.columns):
        if col in col_map:
            valuation_data_df.rename(columns={col: col_map[col]}, inplace=True)
    front_columns = ["security_abbr", "security_code", "date"]
    columns = [c for c in front_columns if c in valuation_data_df.columns] + [
        c for c in valuation_data_df.columns if c not in front_columns
    ]
    valuation_data_df = valuation_data_df[columns]

    success_securities_abbr = (
        "、".join(securities_abbrs[:3]) + "等"
        if len(securities_abbrs) > 3
        else "、".join(securities_abbrs)
    )

    parts = [
        {
            "data": valuation_data_df.to_dict(orient="records"),
            "module": "valuation",
            "type": "data",
        }
    ]
    return format_response(
        {
            "state": "success",
            "message": f"已找到{success_securities_abbr}估值数据",
            "data": parts,
            "usage": usage,
        },
        "valuation",
    )


def main():
    import argparse

    try:
        if not check_version():
            update_sh = os.path.join(script_dir, "update.sh")
            print(f"[WARNING] 存在 Gangtise data 版本更新，可以执行 {update_sh} 更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise data 版本失败\n")

    parser = argparse.ArgumentParser(
        description="查询估值数据（open 估值分析接口）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    today_str = date.today().strftime("%Y-%m-%d")
    parser.add_argument(
        "-sd",
        "--start-date",
        default=None,
        help="开始日期，如 2023-01-01；与 -ed 均不传时先查当天，无数据则向前回退至多 7 日",
    )
    parser.add_argument(
        "-ed",
        "--end-date",
        default=None,
        help=f"结束日期，如 2026-12-31；仅传一侧时另一侧按显式区间补齐（今天={today_str}）",
    )
    parser.add_argument("--securities", default=None, help="证券名称或完整代码，逗号分隔")
    parser.add_argument(
        "--securities-file",
        default=None,
        help="从 csv 读取列 security_code（完整代码或名称等关键词）",
    )
    parser.add_argument("-l", "--limit", type=int, default=2000, help="单次指标请求最大行数")
    args = parser.parse_args()

    securities: Optional[List[str]] = None
    if args.securities:
        securities = [x.strip() for x in args.securities.replace("，", ",").split(",") if x.strip()]
    if not securities and args.securities_file:
        try:
            securities = _load_security_codes_from_file(args.securities_file)
        except Exception as e:
            print(f"根据证券文件解析证券失败: {e}")
            sys.exit(1)

    if not securities:
        parser.error("必须至少提供 --securities 或 --securities-file")

    out = valuation_data(
        securities=securities,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
