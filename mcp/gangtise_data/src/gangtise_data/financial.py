import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import TextIOWrapper
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .security import batch_security_search, resolved_code_abbr_map

from .utils import (BALANCE_FIELD_CN, CASH_FLOW_FIELD_CN, FINANCIAL_REPORT_BALANCE_URL, FINANCIAL_REPORT_CASH_FLOW_QUARTERLY_URL, FINANCIAL_REPORT_CASH_FLOW_URL, FINANCIAL_REPORT_INCOME_QUARTERLY_URL, FINANCIAL_REPORT_INCOME_URL, HK_BALANCE_FIELD_CN, HK_CASH_FLOW_FIELD_CN, HK_FINANCIAL_REPORT_BALANCE_URL, HK_FINANCIAL_REPORT_CASH_FLOW_URL, HK_FINANCIAL_REPORT_INCOME_URL, HK_INCOME_FIELD_CN, INCOME_FIELD_CN, US_BALANCE_FIELD_CN, US_CASH_FLOW_FIELD_CN, US_FINANCIAL_REPORT_BALANCE_URL, US_FINANCIAL_REPORT_CASH_FLOW_URL, US_FINANCIAL_REPORT_INCOME_URL, US_INCOME_FIELD_CN, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, parse_str_list)

# 命令行使用 Q1~Q4、Q0；发往 open 接口时映射为官方 period 枚举
FINANCIAL_PERIOD_CLI_TO_API_A = {
    "Q1": "q1",
    "Q2": "interim",
    "Q3": "q3",
    "Q4": "annual",
    "Q0": "latest",
}

# 港股：h1=上半年报、h2=下半年报（CLI 的 Q2 对应 h1）
FINANCIAL_PERIOD_CLI_TO_API_HK = {
    "Q1": "q1",
    "Q2": "h1",
    "Q3": "q3",
    "Q4": "annual",
    "Q0": "latest",
}

# 美股：与港股类似；利润表/现金流量表另支持 h2（CLI 仍用 Q4→annual）
FINANCIAL_PERIOD_CLI_TO_API_US = {
    "Q1": "q1",
    "Q2": "h1",
    "Q3": "q3",
    "Q4": "annual",
    "Q0": "latest",
}

# 财务报表共有元数据列（不参与「数值全空」判定中的「数值列」）
META_FIELDS_EN: Set[str] = {
    "securityCode",
    "companyName",
    "endDate",
    "fiscalYear",
    "period",
    "reportType",
    "companyType",
    "currency",
    "unit",
}

META_FIELDS_HK_EXTRA: Set[str] = {"startDate", "timeCovered"}

ALL_META_FIELDS_EN: Set[str] = META_FIELDS_EN | META_FIELDS_HK_EXTRA | {
    "category",
    "announcementDate",
}

HK_CN_META_ALIASES = {
    "股票代码": "证券代码",
    "股票中文名称": "证券简称",
}

US_CN_META_ALIASES = HK_CN_META_ALIASES

FINANCIAL_GRANULARITY_ARG_ALIASES = {
    "accumulated": "accumulated",
    "累计": "accumulated",
    "quarterly": "quarterly",
    "单季度": "quarterly",
}

# 利润表衍生指标：接口无对应科目，由营业收入/营业成本/净利润计算
COMPUTED_INCOME_MARGIN_EN = frozenset({"grossProfitMargin", "netProfitMargin", "grossProfit"})
COMPUTED_INCOME_MARGIN_CN = frozenset({"毛利率", "净利率", "毛利"})
_CN_TO_COMPUTED_INCOME_EN = {
    "毛利率": "grossProfitMargin",
    "净利率": "netProfitMargin",
    "毛利": "grossProfit",
}

A_SHARE_MARGIN_SOURCE_EN = {
    "revenue": ("operatingRevenue", "opRev", "totalOperatingRevenue", "totalOpRev"),
    "cost": ("operatingCost", "opCost"),
    "net": ("netProfit",),
}
HK_MARGIN_SOURCE_EN = {
    "revenue": ("opRev", "totalOpRev"),
    "cost": ("opCost",),
    "net": ("netProfit",),
}
US_MARGIN_SOURCE_EN = {
    "revenue": ("totalOpRev", "opRev"),
    "cost": ("opCost",),
    "net": ("netProfit",),
}


def _margin_source_en(market: str) -> dict:
    if market == "hk":
        return HK_MARGIN_SOURCE_EN
    if market == "us":
        return US_MARGIN_SOURCE_EN
    return A_SHARE_MARGIN_SOURCE_EN


def _table_field_map(table_type: str, market: str) -> dict:
    if market == "hk":
        maps = TABLE_TYPE_TO_FIELD_MAP_HK
    elif market == "us":
        maps = TABLE_TYPE_TO_FIELD_MAP_US
    else:
        maps = TABLE_TYPE_TO_FIELD_MAP_A
    return maps.get(table_type) or {}


def _resolve_report_field_token(token: str, field_map: dict) -> Optional[str]:
    t = str(token).strip()
    if not t:
        return None
    if t in COMPUTED_INCOME_MARGIN_EN:
        return t
    if t in COMPUTED_INCOME_MARGIN_CN:
        return _CN_TO_COMPUTED_INCOME_EN[t]
    if t in field_map:
        return t
    cn_to_en = {v: k for k, v in field_map.items()}
    if t in cn_to_en:
        return cn_to_en[t]
    return t


def _append_unique(target: List[str], item: str, seen: Set[str]) -> None:
    if item not in seen:
        target.append(item)
        seen.add(item)


def _prepare_field_list(
    field_list: List[str],
    table_type: str,
    market: str,
) -> Tuple[List[str], Set[str], List[str]]:
    """解析 field-list：展开衍生指标依赖，保留用户请求顺序。

    返回 (发往接口的 fieldList, 需计算的衍生指标英文名, 用户请求科目英文名有序列表)。
    """
    if not field_list:
        return [], set(), []

    field_map = _table_field_map(table_type, market)
    margin_sources = _margin_source_en(market)
    api_fields: List[str] = []
    requested_en: List[str] = []
    margins_needed: Set[str] = set()
    seen_api: Set[str] = set()
    seen_requested: Set[str] = set()

    for raw in field_list:
        en = _resolve_report_field_token(raw, field_map)
        if not en:
            continue
        _append_unique(requested_en, en, seen_requested)
        if table_type == "income" and en in COMPUTED_INCOME_MARGIN_EN:
            margins_needed.add(en)
            continue
        _append_unique(api_fields, en, seen_api)

    if table_type == "income" and margins_needed:
        dep_keys: Set[str] = set()
        if margins_needed & {"grossProfit", "grossProfitMargin"}:
            dep_keys |= {"revenue", "cost"}
        if "netProfitMargin" in margins_needed:
            dep_keys |= {"revenue", "net"}
        for key in dep_keys:
            for dep in margin_sources[key]:
                _append_unique(api_fields, dep, seen_api)

    return api_fields, margins_needed, requested_en


def _pick_numeric_series(df: pd.DataFrame, candidates: Tuple[str, ...]) -> Optional[pd.Series]:
    for col in candidates:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().any():
            return s
    return None


def _apply_computed_income_margins(
    df: pd.DataFrame,
    market: str,
    margins_needed: Set[str],
) -> pd.DataFrame:
    if df.empty or not margins_needed:
        return df
    sources = _margin_source_en(market)
    out = df.copy()
    revenue = _pick_numeric_series(out, sources["revenue"])
    cost = _pick_numeric_series(out, sources["cost"])
    net = _pick_numeric_series(out, sources["net"])
    if revenue is None:
        return out
    rev = revenue.astype(float)
    gp = None
    if cost is not None and margins_needed & {"grossProfit", "grossProfitMargin"}:
        gp = rev - cost.astype(float)
    if "grossProfit" in margins_needed and gp is not None:
        out["grossProfit"] = gp
    if "grossProfitMargin" in margins_needed and gp is not None:
        out["grossProfitMargin"] = (gp / rev * 100).where(rev != 0)
    if "netProfitMargin" in margins_needed and net is not None:
        out["netProfitMargin"] = (net.astype(float) / rev * 100).where(rev != 0)
    return out


_OUTPUT_META_COLS = ("security_abbr", "security_code", "date")


def _output_column_for_field(en: str, field_map: dict, df: pd.DataFrame) -> Optional[str]:
    """将 field-list 中的字段名映射为格式化后 DataFrame 中实际存在的列名。"""
    for candidate in (field_map.get(en, en), en):
        if candidate in df.columns:
            return candidate
    return None


def _filter_output_to_requested_fields(
    df: pd.DataFrame,
    table_type: str,
    market: str,
    requested_en: List[str],
) -> pd.DataFrame:
    if not requested_en or df.empty:
        return df
    field_map = _table_field_map(table_type, market)
    ordered: List[str] = [c for c in _OUTPUT_META_COLS if c in df.columns]
    seen_cols: Set[str] = set(ordered)
    for en in requested_en:
        col = _output_column_for_field(en, field_map, df)
        if col and col not in seen_cols:
            ordered.append(col)
            seen_cols.add(col)
    return df[ordered]


def _normalize_financial_granularity(s: str) -> Tuple[Optional[str], Optional[str]]:
    if not s or not str(s).strip():
        return "accumulated", None
    key = str(s).strip().lower()
    if key in FINANCIAL_GRANULARITY_ARG_ALIASES:
        return FINANCIAL_GRANULARITY_ARG_ALIASES[key], None
    raw = str(s).strip()
    if raw in FINANCIAL_GRANULARITY_ARG_ALIASES:
        return FINANCIAL_GRANULARITY_ARG_ALIASES[raw], None
    return None, f"granularity 无效: {s}，可选 accumulated/累计、quarterly/单季度"


def _resolve_financial_report_url(
    table_type: str,
    granularity: str,
    market: str = "a_share",
) -> Tuple[Optional[str], Optional[str]]:
    if market == "hk":
        if granularity != "accumulated":
            return (
                None,
                "港股财务报表不支持单季度口径（quarterly），请使用 accumulated（累计值）",
            )
        if table_type == "balance":
            return HK_FINANCIAL_REPORT_BALANCE_URL, None
        if table_type == "income":
            return HK_FINANCIAL_REPORT_INCOME_URL, None
        if table_type == "cashflow":
            return HK_FINANCIAL_REPORT_CASH_FLOW_URL, None
        return None, f"table-type 无效: {table_type}"

    if market == "us":
        if granularity != "accumulated":
            return (
                None,
                "美股财务报表不支持单季度口径（quarterly），请使用 accumulated（累计值）",
            )
        if table_type == "balance":
            return US_FINANCIAL_REPORT_BALANCE_URL, None
        if table_type == "income":
            return US_FINANCIAL_REPORT_INCOME_URL, None
        if table_type == "cashflow":
            return US_FINANCIAL_REPORT_CASH_FLOW_URL, None
        return None, f"table-type 无效: {table_type}"

    if table_type == "balance":
        if granularity != "accumulated":
            return None, "granularity=quarterly 仅对利润表和现金流量表有效，资产负债表仅支持 accumulated"
        return FINANCIAL_REPORT_BALANCE_URL, None
    if table_type == "income":
        return (
            FINANCIAL_REPORT_INCOME_QUARTERLY_URL
            if granularity == "quarterly"
            else FINANCIAL_REPORT_INCOME_URL
        ), None
    if table_type == "cashflow":
        return (
            FINANCIAL_REPORT_CASH_FLOW_QUARTERLY_URL
            if granularity == "quarterly"
            else FINANCIAL_REPORT_CASH_FLOW_URL
        ), None
    return None, f"table-type 无效: {table_type}"

TABLE_TYPE_LABEL_CN = {
    "income": "利润表",
    "balance": "资产负债表",
    "cashflow": "现金流量表",
}

TABLE_TYPE_ARG_ALIASES = {
    "income": "income",
    "profit": "income",
    "pl": "income",
    "利润表": "income",
    "balance": "balance",
    "bs": "balance",
    "资产负债表": "balance",
    "cashflow": "cashflow",
    "cf": "cashflow",
    "现金流量表": "cashflow",
}


def _normalize_table_type(s: str) -> Tuple[Optional[str], Optional[str]]:
    if not s or not str(s).strip():
        return "income", None
    key = str(s).strip().lower()
    if key in TABLE_TYPE_ARG_ALIASES:
        return TABLE_TYPE_ARG_ALIASES[key], None
    raw = str(s).strip()
    if raw in TABLE_TYPE_ARG_ALIASES:
        return TABLE_TYPE_ARG_ALIASES[raw], None
    return None, f"table-type 无效: {s}，可选 income/利润表、balance/资产负债表、cashflow/现金流量表"

META_FIELD_CN = {k: INCOME_FIELD_CN[k] for k in META_FIELDS_EN}

TABLE_TYPE_TO_FIELD_MAP_A = {
    "income": INCOME_FIELD_CN,
    "balance": BALANCE_FIELD_CN,
    "cashflow": CASH_FLOW_FIELD_CN,
}

TABLE_TYPE_TO_FIELD_MAP_HK = {
    "income": HK_INCOME_FIELD_CN,
    "balance": HK_BALANCE_FIELD_CN,
    "cashflow": HK_CASH_FLOW_FIELD_CN,
}

TABLE_TYPE_TO_FIELD_MAP_US = {
    "income": US_INCOME_FIELD_CN,
    "balance": US_BALANCE_FIELD_CN,
    "cashflow": US_CASH_FLOW_FIELD_CN,
}


def _meta_cn_labels() -> Set[str]:
    labels: Set[str] = set()
    for field_map in (
        INCOME_FIELD_CN,
        BALANCE_FIELD_CN,
        CASH_FLOW_FIELD_CN,
        HK_INCOME_FIELD_CN,
        HK_BALANCE_FIELD_CN,
        HK_CASH_FLOW_FIELD_CN,
        US_INCOME_FIELD_CN,
        US_BALANCE_FIELD_CN,
        US_CASH_FLOW_FIELD_CN,
    ):
        for k in ALL_META_FIELDS_EN:
            if k in field_map:
                labels.add(field_map[k])
    return labels


META_CN_LABELS: Set[str] = _meta_cn_labels()


def _partition_financial_codes(
    codes: List[str], types: List[str]
) -> Tuple[List[str], List[str], List[str], List[Tuple[str, str]]]:
    """返回 (A股/存托凭证列表, 港股列表, 美股列表, 不支持的 (code, type) 列表)。"""
    a_list: List[str] = []
    hk_list: List[str] = []
    us_list: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for i, c in enumerate(codes):
        t = types[i] if i < len(types) else ""
        if t == "港股":
            hk_list.append(c)
        elif t == "美股":
            us_list.append(c)
        elif t in ("A股", "存托凭证(DR)"):
            a_list.append(c)
        else:
            skipped.append((c, t or "未知类型"))
    return a_list, hk_list, us_list, skipped


def _unsupported_financial_note(skipped: List[Tuple[str, str]]) -> Optional[str]:
    if not skipped:
        return None
    by_type: dict = {}
    for code, st in skipped:
        by_type.setdefault(st, []).append(code)
    parts: List[str] = []
    for st in sorted(by_type.keys()):
        cs = by_type[st]
        tail = ",".join(cs[:8])
        if len(cs) > 8:
            tail += "…"
        parts.append(f"{st}（{tail}）")
    return "[WARNING]存在部分标的类型不支持财务报表查询，已跳过：" + "；".join(parts)


def _load_security_codes_from_file(path: str) -> List[str]:
    full_path = path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"证券文件不存在: {path}")
    df = pd.read_csv(full_path)
    if "security_code" not in df.columns:
        raise ValueError("证券文件须包含 security_code 列（完整代码或证券名称等关键词）")
    return [str(x) for x in df["security_code"].dropna().tolist()]


def _fmt_yyyymmdd(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    t = str(val).strip()
    if len(t) == 8 and t.isdigit():
        return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
    return t


def _map_financial_periods_cli_to_api(
    periods: Optional[List[str]],
    market: str = "a_share",
) -> Tuple[List[str], Optional[str]]:
    """将 Q1~Q4、Q0 转为 open 接口 period 列表；默认 Q0→latest。"""
    period_map = FINANCIAL_PERIOD_CLI_TO_API_HK if market == "hk" else (
        FINANCIAL_PERIOD_CLI_TO_API_US if market == "us" else FINANCIAL_PERIOD_CLI_TO_API_A
    )
    if not periods:
        return ["latest"], None
    out: List[str] = []
    for p in periods:
        k = p.strip().upper()
        if k not in period_map:
            return (
                [],
                f"period 仅支持 Q1/Q2/Q3/Q4/Q0（一季报/中报/三季报/年报/最新），无效: {p}",
            )
        out.append(period_map[k])
    return out, None


def _parse_income_statement_body(body: dict) -> pd.DataFrame:
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


def _drop_empty_value_rows_and_cols(df: pd.DataFrame) -> pd.DataFrame:
    """删除数值列全为空的行，以及数值列全为空的列（元数据列保留至仍有行）。"""
    if df.empty:
        return df
    d = df.copy()
    value_cols = [c for c in d.columns if c not in ALL_META_FIELDS_EN]
    if not value_cols:
        return d
    for c in value_cols:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    row_keep = d[value_cols].notna().any(axis=1)
    d = d.loc[row_keep].reset_index(drop=True)
    if d.empty:
        return d
    empty_val_cols = [c for c in value_cols if c in d.columns and d[c].notna().sum() == 0]
    d = d.drop(columns=empty_val_cols, errors="ignore")
    return d


def _rename_columns_for_table(
    df: pd.DataFrame, table_type: str, market: str = "a_share"
) -> pd.DataFrame:
    field_map = _table_field_map(table_type, market)
    if field_map:
        out = df.rename(columns=lambda c: field_map.get(c, c))
    else:
        out = df.rename(columns=lambda c: META_FIELD_CN.get(c, c))
    if market == "hk":
        out = out.rename(columns={k: v for k, v in HK_CN_META_ALIASES.items() if k in out.columns})
    elif market == "us":
        out = out.rename(columns={k: v for k, v in US_CN_META_ALIASES.items() if k in out.columns})
    return out


def _round_numeric_values(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    for c in d.columns:
        if c in META_CN_LABELS or c in ALL_META_FIELDS_EN:
            continue
        d[c] = pd.to_numeric(d[c], errors="coerce").round(2)
    return d


def _financial_codes_body_title(sub_df: pd.DataFrame, table_label: str) -> str:
    code_col = "证券代码" if "证券代码" in sub_df.columns else "securityCode"
    if code_col not in sub_df.columns:
        return f"{table_label}数据"
    codes = sub_df[code_col].dropna().astype(str).str.upper().unique().tolist()
    codes.sort()
    if not codes:
        return f"{table_label}数据"
    if len(codes) == 1:
        return f"{codes[0]}{table_label}数据"
    return f"{codes[0]}等{len(codes)}只标的{table_label}数据"


def _financial_title_segment_for_block(
    sub_df: pd.DataFrame, segment: str, table_label: str
) -> str:
    body = _financial_codes_body_title(sub_df, table_label)
    if segment == "a_share":
        return f"{body}（A股）"
    if segment == "hk":
        return f"{body}（港股）"
    if segment == "us":
        return f"{body}（美股）"
    return body


def _apply_security_abbr_from_map(df: pd.DataFrame, abbr_map: Dict[str, str]) -> pd.DataFrame:
    """用 batch_security_search 返回的 abbrs 覆盖 security_abbr。"""
    if df.empty or not abbr_map or "security_code" not in df.columns:
        return df
    out = df.copy()
    out["security_abbr"] = (
        out["security_code"].astype(str).str.strip().str.upper().map(lambda c: abbr_map.get(c, c))
    )
    front = list(_OUTPUT_META_COLS)
    columns = [c for c in front if c in out.columns] + [c for c in out.columns if c not in front]
    return out[columns]


def _format_financial_output_table(
    df: pd.DataFrame, table_type: str, market: str
) -> pd.DataFrame:
    """单市场块：清洗、列名映射、排序与导出列顺序。"""
    if df.empty:
        return df
    data = df.drop(columns=["category", "announcementDate"], errors="ignore")
    for date_col in ("endDate", "startDate"):
        if date_col in data.columns:
            data[date_col] = data[date_col].map(_fmt_yyyymmdd)
    data = _drop_empty_value_rows_and_cols(data)
    if data.empty:
        return data
    data = _rename_columns_for_table(data, table_type, market)
    data = _round_numeric_values(data)
    if "证券代码" in data.columns and "财报截止日期" in data.columns:
        data = data.sort_values(
            by=["证券代码", "财报截止日期"],
            ascending=[True, False],
        ).reset_index(drop=True)
    col_map = {
        "证券简称": "security_abbr",
        "证券代码": "security_code",
        "财报截止日期": "date",
    }
    for col in list(data.columns):
        if col in col_map:
            data.rename(columns={col: col_map[col]}, inplace=True)
    front = ["security_abbr", "security_code", "date"]
    columns = [c for c in front if c in data.columns] + [
        c for c in data.columns if c not in front
    ]
    return data[columns]


def _process_financial_market_block(
    frames: List[pd.DataFrame],
    table_type: str,
    market: str,
    margins_needed: Optional[Set[str]] = None,
    requested_en: Optional[List[str]] = None,
) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame()
    data = pd.concat(frames, ignore_index=True)
    if margins_needed:
        data = _apply_computed_income_margins(data, market, margins_needed)
    data = _format_financial_output_table(data, table_type, market)
    if requested_en:
        data = _filter_output_to_requested_fields(
            data, table_type, market, requested_en
        )
    return data


def _fetch_financial_reports_batch(
    report_url: str,
    headers: dict,
    codes: List[str],
    start_date: Optional[str],
    end_date: Optional[str],
    fiscal_year: Optional[List[str]],
    period_list: List[str],
    report_list: List[str],
    fields: List[str],
) -> List[pd.DataFrame]:
    frames: List[pd.DataFrame] = []
    if not codes:
        return frames
    with ThreadPoolExecutor(max_workers=min(len(codes), 8)) as ex:
        futs = [
            ex.submit(
                _fetch_financial_report,
                report_url,
                headers,
                code,
                start_date,
                end_date,
                fiscal_year,
                period_list,
                report_list,
                fields,
            )
            for code in codes
        ]
        for fut in as_completed(futs):
            part = fut.result()
            if not part.empty:
                frames.append(part)
    return frames


def _fetch_financial_report(
    report_url: str,
    headers: dict,
    security_code: str,
    start_date: Optional[str],
    end_date: Optional[str],
    fiscal_year: Optional[List[str]],
    period: List[str],
    report_type: List[str],
    field_list: List[str],
) -> pd.DataFrame:
    payload: dict = {
        "securityCode": security_code,
        "period": period,
        "reportType": report_type,
        "fieldList": field_list,
    }
    if start_date:
        payload["startDate"] = start_date
    else:
        payload["startDate"] = None
    if end_date:
        payload["endDate"] = end_date
    else:
        payload["endDate"] = None
    if fiscal_year:
        payload["fiscalYear"] = fiscal_year
    else:
        payload["fiscalYear"] = None
    try:
        r = requests.post(report_url, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return pd.DataFrame()
        body = r.json()
        return _parse_income_statement_body(body)
    except Exception:
        return pd.DataFrame()


def financial_data(
    securities: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    fiscal_year: Optional[List[str]] = None,
    period: Optional[List[str]] = None,
    report_type: Optional[List[str]] = None,
    field_list: Optional[List[str]] = None,
    table_type: str = "income",
    granularity: str = "accumulated",
):
    usage: dict = {}
    if not get_authorization_token():
        return format_response(
            {"state": "error", "message": "未配置 gangtise 授权，无法调用 open 接口", "data": [], "usage": usage},
            "financial",
        )

    table_norm, table_err = _normalize_table_type(table_type)
    if table_err:
        return format_response(
            {"state": "error", "message": table_err, "data": [], "usage": usage},
            "financial",
        )
    granularity_norm, granularity_err = _normalize_financial_granularity(granularity)
    if granularity_err:
        return format_response(
            {"state": "error", "message": granularity_err, "data": [], "usage": usage},
            "financial",
        )

    table_label = TABLE_TYPE_LABEL_CN[table_norm]

    headers = get_authorization_headers()

    report_list = report_type if report_type is not None else ["consolidated"]
    fields_raw = field_list if field_list is not None else []
    explicit_fields = bool(fields_raw)

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
            "financial",
        )
    codes = resolved.get("codes") or []
    abbr_map = resolved_code_abbr_map(resolved)
    resolved_types = list(resolved.get("types") or [])
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
            "financial",
        )

    while len(resolved_types) < len(codes):
        resolved_types.append("")

    a_codes, hk_codes, us_codes, skipped = _partition_financial_codes(codes, resolved_types)
    skip_note = _unsupported_financial_note(skipped)

    if not a_codes and not hk_codes and not us_codes:
        msg = skip_note if skip_note else "未解析到支持财务报表查询的证券（仅支持 A 股、存托凭证、港股与美股）"
        return format_response(
            {"state": "error", "message": msg, "data": [], "usage": usage},
            "financial",
        )

    period_a, period_err = _map_financial_periods_cli_to_api(period, "a_share")
    if period_err:
        return format_response(
            {"state": "error", "message": period_err, "data": [], "usage": usage},
            "financial",
        )
    period_hk, period_hk_err = _map_financial_periods_cli_to_api(period, "hk")
    if period_hk_err:
        return format_response(
            {"state": "error", "message": period_hk_err, "data": [], "usage": usage},
            "financial",
        )
    period_us, period_us_err = _map_financial_periods_cli_to_api(period, "us")
    if period_us_err:
        return format_response(
            {"state": "error", "message": period_us_err, "data": [], "usage": usage},
            "financial",
        )

    a_frames: List[pd.DataFrame] = []
    hk_frames: List[pd.DataFrame] = []
    us_frames: List[pd.DataFrame] = []

    a_api_fields = fields_raw
    a_margins_needed: Set[str] = set()
    a_requested_en: List[str] = []
    hk_api_fields = fields_raw
    hk_margins_needed: Set[str] = set()
    hk_requested_en: List[str] = []
    us_api_fields = fields_raw
    us_margins_needed: Set[str] = set()
    us_requested_en: List[str] = []
    if explicit_fields:
        a_api_fields, a_margins_needed, a_requested_en = _prepare_field_list(
            fields_raw, table_norm, "a_share"
        )
        hk_api_fields, hk_margins_needed, hk_requested_en = _prepare_field_list(
            fields_raw, table_norm, "hk"
        )
        us_api_fields, us_margins_needed, us_requested_en = _prepare_field_list(
            fields_raw, table_norm, "us"
        )

    if a_codes:
        a_url, a_url_err = _resolve_financial_report_url(table_norm, granularity_norm, "a_share")
        if a_url_err:
            return format_response(
                {"state": "error", "message": a_url_err, "data": [], "usage": usage},
                "financial",
            )
        a_frames = _fetch_financial_reports_batch(
            a_url,
            headers,
            a_codes,
            start_date,
            end_date,
            fiscal_year,
            period_a,
            report_list,
            a_api_fields,
        )

    if hk_codes:
        hk_url, hk_url_err = _resolve_financial_report_url(table_norm, granularity_norm, "hk")
        if hk_url_err:
            return format_response(
                {"state": "error", "message": hk_url_err, "data": [], "usage": usage},
                "financial",
            )
        hk_frames = _fetch_financial_reports_batch(
            hk_url,
            headers,
            hk_codes,
            start_date,
            end_date,
            fiscal_year,
            period_hk,
            report_list,
            hk_api_fields,
        )

    if us_codes:
        us_url, us_url_err = _resolve_financial_report_url(table_norm, granularity_norm, "us")
        if us_url_err:
            return format_response(
                {"state": "error", "message": us_url_err, "data": [], "usage": usage},
                "financial",
            )
        us_frames = _fetch_financial_reports_batch(
            us_url,
            headers,
            us_codes,
            start_date,
            end_date,
            fiscal_year,
            period_us,
            report_list,
            us_api_fields,
        )

    if not a_frames and not hk_frames and not us_frames:
        err_msg = f"未找到{table_label}数据"
        if skip_note:
            err_msg = f"{skip_note}；{err_msg}"
        return format_response(
            {"state": "error", "message": err_msg, "data": [], "usage": usage},
            "financial",
        )

    # A 股与港股科目列结构不同，不合并宽表；按市场分块，对应多份 CSV（同 quote.py）
    output_blocks: List[Tuple[pd.DataFrame, str]] = []
    if a_frames:
        data_a = _process_financial_market_block(
            a_frames,
            table_norm,
            "a_share",
            a_margins_needed if explicit_fields else None,
            a_requested_en if explicit_fields else None,
        )
        if not data_a.empty:
            output_blocks.append((_apply_security_abbr_from_map(data_a, abbr_map), "a_share"))
    if hk_frames:
        data_hk = _process_financial_market_block(
            hk_frames,
            table_norm,
            "hk",
            hk_margins_needed if explicit_fields else None,
            hk_requested_en if explicit_fields else None,
        )
        if not data_hk.empty:
            output_blocks.append((_apply_security_abbr_from_map(data_hk, abbr_map), "hk"))
    if us_frames:
        data_us = _process_financial_market_block(
            us_frames,
            table_norm,
            "us",
            us_margins_needed if explicit_fields else None,
            us_requested_en if explicit_fields else None,
        )
        if not data_us.empty:
            output_blocks.append((_apply_security_abbr_from_map(data_us, abbr_map), "us"))

    if not output_blocks:
        err_msg = f"过滤空值后无{table_label}数据"
        if skip_note:
            err_msg = f"{skip_note}；{err_msg}"
        return format_response(
            {"state": "error", "message": err_msg, "data": [], "usage": usage},
            "financial",
        )

    extra_notes: List[str] = []
    if len(output_blocks) > 1:
        extra_notes.append(
            "不同市场财务科目列结构不同，已分多张表输出，对应多份 CSV。"
        )
    if skip_note:
        extra_notes.insert(0, skip_note)

    title_segments = [
        _financial_title_segment_for_block(sub_df, seg, table_label)
        for sub_df, seg in output_blocks
    ]
    msg = f"已找到{'；'.join(title_segments)}"
    if extra_notes:
        msg += "\n" + "\n".join(extra_notes)

    parts = [
        {"data": sub_df.to_dict(orient="records"), "module": "financial", "type": "data"}
        for sub_df, _seg in output_blocks
    ]
    return format_response(
        {
            "state": "success",
            "message": msg,
            "data": parts,
            "usage": usage,
        },
        "financial",
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
        description="查询财务报表：利润表 / 资产负债表 / 现金流量表（A 股 open financial-report；港股 /hk；美股 /us）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-t",
        "--table-type",
        default="income",
        help="报表：income|利润表、balance|资产负债表、cashflow|现金流量表",
    )
    parser.add_argument(
        "-g",
        "--granularity",
        default="accumulated",
        help="口径：accumulated|累计、quarterly|单季度（仅 A 股利润表/现金流量表；港股/美股仅 accumulated）",
    )
    parser.add_argument("-sd", "--start-date", default=None, help="开始日期 yyyy-MM-dd")
    parser.add_argument("-ed", "--end-date", default=None, help="结束日期 yyyy-MM-dd")
    parser.add_argument(
        "--fiscal-year",
        default=None,
        help="财报年度，逗号分隔，如 2024,2025",
    )
    parser.add_argument(
        "--period",
        default="Q0",
        help="报告期：Q1/Q2/Q3/Q4/Q0（一季报/中报/三季报/年报/最新），逗号分隔；默认 Q0",
    )
    parser.add_argument(
        "--report-type",
        default="consolidated",
        help="报表类型：consolidated/consolidatedRestated/standalone/standaloneRestated，逗号分隔",
    )
    parser.add_argument(
        "--field-list",
        default=None,
        help="指定科目英文字段名或中文名，逗号分隔；毛利=营业收入-营业成本，毛利率/净利率为衍生比率；不传则 fieldList=[] 取全部",
    )
    parser.add_argument("--securities", default=None, help="证券逗号分隔：完整代码或名称/拼音等")
    parser.add_argument("--securities-file", default=None, help="csv 含 security_code 列（代码或名称）")
    args = parser.parse_args()

    fy: Optional[List[str]] = None
    if args.fiscal_year:
        fy = [x.strip() for x in args.fiscal_year.replace("，", ",").split(",") if x.strip()]

    period_list = [
        x.strip() for x in args.period.replace("，", ",").split(",") if x.strip()
    ]
    report_list = [x.strip() for x in args.report_type.replace("，", ",").split(",") if x.strip()]

    fl: Optional[List[str]] = None
    if args.field_list:
        fl = [x.strip() for x in args.field_list.replace("，", ",").replace("、", ",").split(",") if x.strip()]
    common_map = {
        "收入": "营业收入",
        "成本": "营业成本",
        "利润": "净利润",
    }
    if fl:
        fl = [common_map.get(x, x) for x in fl]

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

    out = financial_data(
        securities=securities,
        start_date=args.start_date,
        end_date=args.end_date,
        fiscal_year=fy,
        period=period_list if period_list else None,
        report_type=report_list if report_list else None,
        field_list=fl,
        table_type=args.table_type,
        granularity=args.granularity,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
