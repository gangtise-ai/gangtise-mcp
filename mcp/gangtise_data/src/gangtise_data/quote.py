import os
import sys
from datetime import date, datetime, timedelta
from io import TextIOWrapper
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .security import batch_security_search, resolved_code_abbr_map

from .utils import (QUOTE_ADJUST_FACTOR_URL, QUOTE_MINUTE_URL, QUOTE_REALTIME_URL, QUOTE_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, parse_str_list)

# 与 open 日 K 文档一致
_FIELD_LIST = [
    "securityCode",
    "tradeDate",
    "open",
    "high",
    "low",
    "close",
    "preClose",
    "change",
    "pctChange",
    "volume",
    "amount",
]

API_FIELD_TO_CN = {
    "securityCode": "证券代码",
    "tradeDate": "日期",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "close": "收盘价",
    "preClose": "昨收价",
    "change": "涨跌额",
    "pctChange": "涨跌幅",
    "volume": "成交量",
    "amount": "成交额",
    "adjustFactor": "复权因子",
}

MINUTE_FIELD_LIST = [
    "securityCode",
    "tradeTime",
    "open",
    "high",
    "low",
    "close",
    "change",
    "pctChange",
    "volume",
    "amount",
]

MINUTE_API_FIELD_TO_CN = {
    "securityCode": "证券代码",
    "tradeTime": "日期",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "close": "收盘价",
    "change": "涨跌额",
    "pctChange": "涨跌幅",
    "volume": "成交量",
    "amount": "成交额",
}

SNAP_FIELD_LIST = [
    "securityCode",
    "exchange",
    "tradeDate",
    "tradeTime",
    "latestPrice",
    "open",
    "high",
    "low",
    "preClose",
    "change",
    "pctChange",
    "volume",
    "amount",
    "amplitude",
]

SNAP_API_FIELD_TO_CN = {
    "securityCode": "证券代码",
    "exchange": "交易所",
    "tradeDate": "交易日期",
    "tradeTime": "行情时间",
    "latestPrice": "最新价",
    "open": "开盘价",
    "high": "最高价",
    "low": "最低价",
    "preClose": "昨收价",
    "change": "涨跌额",
    "pctChange": "涨跌幅",
    "volume": "成交量",
    "amount": "成交额",
    "amplitude": "振幅",
}

_SNAP_PRICE_COLUMNS = (
    "latestPrice",
    "open",
    "high",
    "low",
    "close",
    "preClose",
)


def _snap_row_has_valid_price(row: pd.Series) -> bool:
    """实时行情行是否含有效价格（非零且非 NaN）。全零/NaN 常见于美股未开市等场景。"""
    for col in _SNAP_PRICE_COLUMNS:
        if col not in row.index:
            continue
        v = row[col]
        if pd.isna(v):
            continue
        try:
            if float(v) != 0.0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _sanitize_snap_quote(df: pd.DataFrame) -> Tuple[pd.DataFrame, bool]:
    """过滤无效实时行情行；若过滤后为空则视为失败（degenerate=True）。"""
    if df.empty:
        return df, True
    price_cols = [c for c in _SNAP_PRICE_COLUMNS if c in df.columns]
    if not price_cols:
        return df, False
    valid = df[df.apply(_snap_row_has_valid_price, axis=1)].copy()
    return valid, valid.empty

DEFAULT_QUOTE_LOOKBACK_DAYS = 7
ALL_MARKET_DATE_LOOKBACK_DAYS = 15

_ALL_MARKET_LABEL = {"cn": "A股", "hk": "H股", "us": "美股"}
_ALL_MARKET_TO_TOKEN = {"cn": "aShares", "hk": "hkStocks", "us": "usStocks"}


def _parse_all_market_arg(value: Optional[str]) -> Optional[Tuple[str, ...]]:
    """解析 --all-market：None=未启用；无值/空=cn；可逗号分隔 cn/hk/us。"""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return ("cn",)
    out: List[str] = []
    for part in raw.replace("，", ",").split(","):
        key = part.strip().lower()
        if not key:
            continue
        if key in {"cn", "a", "ashare", "ashares", "a股"}:
            mkt = "cn"
        elif key in {"hk", "h", "hkstock", "hkstocks", "港股", "h股"}:
            mkt = "hk"
        elif key in {"us", "u", "usstock", "usstocks", "美股"}:
            mkt = "us"
        else:
            raise ValueError(f"不支持的 --all-market 市场: {part}（可选 cn/hk/us）")
        if mkt not in out:
            out.append(mkt)
    return tuple(out) if out else ("cn",)


def _snap_tokens_for_markets(markets: Tuple[str, ...]) -> Tuple[str, ...]:
    return tuple(_ALL_MARKET_TO_TOKEN[m] for m in markets if m in _ALL_MARKET_TO_TOKEN)


def _market_code_mask(codes_u: pd.Series, market: str) -> pd.Series:
    if market == "cn":
        return codes_u.str.endswith((".SH", ".SZ", ".BJ"))
    if market == "hk":
        return codes_u.str.endswith(".HK")
    return codes_u.str.endswith(".US") | (
        ~codes_u.str.endswith((".SH", ".SZ", ".BJ", ".HK")) & codes_u.str.contains(".", regex=False)
    )


def _partition_daily_quote_codes(
    codes: List[str], types: List[str]
) -> Tuple[List[str], List[str], List[str], List[str], List[Tuple[str, str]]]:
    """返回 (A股, 港股, 指数, 美股, 不支持的 (code, security_type))。指数日 K 支持交易所/概念/行业指数。"""
    a_list: List[str] = []
    hk_list: List[str] = []
    idx_list: List[str] = []
    us_list: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for i, c in enumerate(codes):
        t = types[i] if i < len(types) else ""
        if t == "A股":
            a_list.append(c)
        elif t == "港股":
            hk_list.append(c)
        elif t == "美股":
            us_list.append(c)
        elif t in ("交易所指数", "行业指数", "概念指数", "其他指数", "指数"):
            idx_list.append(c)
        else:
            skipped.append((c, t or "未知类型"))
    return a_list, hk_list, idx_list, us_list, skipped


def _partition_snap_quote_codes(
    codes: List[str], types: List[str]
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """截面行情：A/港/美股及交易所/概念/行业指数走同一实时接口。"""
    ok_list: List[str] = []
    skipped: List[Tuple[str, str]] = []
    for i, c in enumerate(codes):
        t = types[i] if i < len(types) else ""
        if t in ("A股", "港股", "美股", "交易所指数", "行业指数", "概念指数", "其他指数", "指数"):
            ok_list.append(c)
        else:
            skipped.append((c, t or "未知类型"))
    return ok_list, skipped


def _unsupported_quote_note(skipped: List[Tuple[str, str]]) -> Optional[str]:
    if not skipped:
        return None
    by_type: Dict[str, List[str]] = {}
    for code, st in skipped:
        by_type.setdefault(st, []).append(code)
    parts: List[str] = []
    for st in sorted(by_type.keys()):
        cs = by_type[st]
        tail = ",".join(cs[:8])
        if len(cs) > 8:
            tail += "…"
        parts.append(f"{st}（{tail}）")
    return "[WARNING]存在部分标的类型不支持行情查询，已跳过：" + "；".join(parts)


def _sort_by_date_desc_time_asc(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """按日历 yyyy-mm-dd 倒序；同日若有时间则按 HH:MM:SS 正序。"""
    dt = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
    day = dt.dt.normalize()
    within_day_sec = (dt - day).dt.total_seconds()
    keys = {"_k_day": day, "_k_intra": within_day_sec}
    out = df.assign(**keys)
    if "证券代码" in out.columns:
        out = out.sort_values(
            by=["证券代码", "_k_day", "_k_intra"],
            ascending=[True, False, True],
            na_position="last",
        )
    else:
        out = out.sort_values(
            by=["_k_day", "_k_intra"],
            ascending=[False, True],
            na_position="last",
        )
    return out.drop(columns=["_k_day", "_k_intra"]).reset_index(drop=True)


def _quote_format_output_table(
    data: pd.DataFrame,
    data_type: str,
    adj_mode: str,
    prefer_adjusted_columns: bool,
) -> pd.DataFrame:
    """列顺序、排序、导出列名。prefer_adjusted_columns 仅当该表确有复权列时使用。"""
    preferred = [
        "证券简称",
        "证券代码",
        "日期",
        "开盘价",
        "最高价",
        "最低价",
        "收盘价",
        "昨收价",
        "涨跌额",
        "涨跌幅",
        "成交量",
        "成交额",
    ]
    if data_type == "daily" and prefer_adjusted_columns and adj_mode != "none":
        sfx = _adj_price_suffix(adj_mode)
        preferred = [
            "证券简称",
            "证券代码",
            "日期",
            f"开盘价{sfx}",
            f"最高价{sfx}",
            f"最低价{sfx}",
            f"收盘价{sfx}",
            f"昨收价{sfx}",
            f"涨跌额{sfx}",
            f"涨跌幅{sfx}",
            "成交量",
            "成交额",
        ]
    if data_type == "minute":
        preferred = [c for c in preferred if c not in {"昨收价"}]
    if data_type == "snap":
        preferred = [
            "证券简称",
            "证券代码",
            "交易所",
            "交易日期",
            "行情时间",
            "最新价",
            "开盘价",
            "最高价",
            "最低价",
            "昨收价",
            "涨跌额",
            "涨跌幅",
            "成交量",
            "成交额",
            "振幅",
        ]
    cols = [c for c in preferred if c in data.columns]
    cols += [c for c in data.columns if c not in cols]
    data = data[cols].copy()

    if data_type == "snap" and "证券代码" in data.columns:
        data = data.sort_values(by="证券代码", ascending=True).reset_index(drop=True)
    elif "日期" in data.columns:
        data = _sort_by_date_desc_time_asc(data, "日期")

    if data_type == "snap":
        col_map = {
            "证券简称": "security_abbr",
            "证券代码": "security_code",
            "交易日期": "trade_date",
            "行情时间": "quote_time",
        }
        front_columns = ["security_abbr", "security_code", "trade_date", "quote_time"]
    else:
        col_map = {
            "证券简称": "security_abbr",
            "证券代码": "security_code",
            "日期": "date",
        }
        front_columns = ["security_abbr", "security_code", "date"]
    for col in list(data.columns):
        if col in col_map:
            data.rename(columns={col: col_map[col]}, inplace=True)
    columns = [c for c in front_columns if c in data.columns] + [
        c for c in data.columns if c not in front_columns
    ]
    data = data[columns]
    return data


def _quote_codes_body_title(sub_df: pd.DataFrame, k_label: str) -> str:
    """如 600519.SH日K行情数据 或 600519.SH等2只标的日K行情数据"""
    if "证券代码" not in sub_df.columns:
        return f"{k_label}行情数据"
    codes = sub_df["证券代码"].dropna().astype(str).str.upper().unique().tolist()
    codes.sort()
    if not codes:
        return f"{k_label}行情数据"
    if len(codes) == 1:
        return f"{codes[0]}{k_label}行情数据"
    return f"{codes[0]}等{len(codes)}只标的{k_label}行情数据"


def _quote_title_segment_for_block(
    sub_df: pd.DataFrame,
    segment: str,
    prefer_adj: bool,
    data_type: str,
    adj_mode: str,
    hk_code_set: frozenset,
    idx_code_set: frozenset,
    a_code_set: frozenset,
    us_code_set: frozenset = frozenset(),
    all_market_markets: Optional[Tuple[str, ...]] = None,
) -> str:
    """按结果表与业务分段生成「标的摘要（市场 复权）」。"""
    if data_type == "snap":
        body = _quote_codes_body_title(sub_df, "截面")
        return f"{body}（实时快照）"

    k_label = "分钟K" if data_type == "minute" else "日K"
    body = _quote_codes_body_title(sub_df, k_label)
    adj_map = {"none": "不复权", "forward": "前复权", "backward": "后复权"}

    if data_type == "minute":
        return f"{body}（不复权）"

    if segment == "all_market":
        markets = all_market_markets or ("cn",)
        mkt = "、".join(_ALL_MARKET_LABEL.get(m, m) for m in markets)
        if prefer_adj and adj_mode != "none":
            return f"全市场{k_label}行情数据（{mkt} {adj_map[adj_mode]}）"
        return f"全市场{k_label}行情数据（{mkt} 不复权）"

    if segment == "a_share":
        if prefer_adj and adj_mode != "none":
            return f"{body}（A股 {adj_map[adj_mode]}）"
        return f"{body}（A股 不复权）"

    if segment == "us_share":
        if prefer_adj and adj_mode != "none":
            return f"{body}（美股 {adj_map[adj_mode]}）"
        return f"{body}（美股 不复权）"

    if segment == "hk_share":
        if prefer_adj and adj_mode != "none":
            return f"{body}（H股 {adj_map[adj_mode]}）"
        return f"{body}（H股 不复权）"

    if segment == "hk_index":
        codes_u = (
            sub_df["证券代码"].dropna().astype(str).str.upper().unique().tolist()
            if "证券代码" in sub_df.columns
            else []
        )
        in_hk = all(c in hk_code_set for c in codes_u) if codes_u else False
        in_idx = all(c in idx_code_set for c in codes_u) if codes_u else False
        if in_hk and not in_idx:
            mkt = "H股"
        elif in_idx and not in_hk:
            mkt = "指数"
        else:
            mkt = "港股及指数"
        return f"{body}（{mkt} 不复权）"

    if segment == "single":
        codes_u = (
            sub_df["证券代码"].dropna().astype(str).str.upper().unique().tolist()
            if "证券代码" in sub_df.columns
            else []
        )
        if not codes_u:
            return f"{body}（不复权）"
        all_a = all(c in a_code_set for c in codes_u)
        all_hk = all(c in hk_code_set for c in codes_u)
        all_idx = all(c in idx_code_set for c in codes_u)
        all_us = all(c in us_code_set for c in codes_u)
        if all_a and not (all_hk or all_idx or all_us):
            if prefer_adj and adj_mode != "none":
                return f"{body}（A股 {adj_map[adj_mode]}）"
            return f"{body}（A股 不复权）"
        if all_hk and not (all_a or all_idx or all_us):
            if prefer_adj and adj_mode != "none":
                return f"{body}（H股 {adj_map[adj_mode]}）"
            return f"{body}（H股 不复权）"
        if all_us and not (all_a or all_hk or all_idx):
            if prefer_adj and adj_mode != "none":
                return f"{body}（美股 {adj_map[adj_mode]}）"
            return f"{body}（美股 不复权）"
        if all_idx and not (all_a or all_hk or all_us):
            return f"{body}（指数 不复权）"
        if prefer_adj and adj_mode != "none":
            return f"{body}（多品种 {adj_map[adj_mode]}）"
        return f"{body}（多品种 不复权）"

    return f"{body}（不复权）"


def _last_day_of_month(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


def _end_date_includes_today(end_date: str) -> bool:
    ed = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
    return ed >= date.today()


def _daily_kline_codes_on_date(df: pd.DataFrame, date_str: str) -> set:
    """返回日 K 数据中在指定交易日已有行情的证券代码（大写）。"""
    if df.empty or "securityCode" not in df.columns or "tradeDate" not in df.columns:
        return set()
    td = pd.to_datetime(df["tradeDate"], errors="coerce").dt.strftime("%Y-%m-%d")
    mask = td.eq(date_str)
    if not mask.any():
        return set()
    return set(df.loc[mask, "securityCode"].astype(str).str.upper())


def _daily_kline_code_date_pairs(df: pd.DataFrame) -> set:
    """日 K 中已有的 (证券代码, 交易日) 集合，代码为大写。"""
    if df.empty or "securityCode" not in df.columns or "tradeDate" not in df.columns:
        return set()
    td = pd.to_datetime(df["tradeDate"], errors="coerce").dt.strftime("%Y-%m-%d")
    codes = df["securityCode"].astype(str).str.upper()
    pairs = set()
    for code, d in zip(codes, td):
        if d and d != "NaT":
            pairs.add((code, d))
    return pairs


def _code_max_kline_date(df: pd.DataFrame, code: str) -> Optional[str]:
    if df.empty or "securityCode" not in df.columns or "tradeDate" not in df.columns:
        return None
    sub = df[df["securityCode"].astype(str).str.upper() == code.upper()]
    if sub.empty:
        return None
    td = pd.to_datetime(sub["tradeDate"], errors="coerce")
    if td.isna().all():
        return None
    return td.max().strftime("%Y-%m-%d")


def _codes_needing_snap_supplement(
    data_parts: List[pd.DataFrame], candidate_codes: List[str], check_date: str
) -> List[str]:
    """候选证券中，日 K 在 check_date（通常为 end_date）尚无行情的才需实时补全。

    注意：当整批日 K 都还没有 check_date（例如港股盘中，日 K 最新只到昨收）时，
    不得用「该券已有全局最大交易日」跳过补全——否则单查一只港股时永远补不上当天。
    「== global_max」启发式仅在全局日 K 已覆盖到 check_date 及以后时启用
    （如美股时区导致 end 为日历次日、但 K 线已到 T 日）。
    """
    kline_all = (
        pd.concat(data_parts, ignore_index=True) if data_parts else pd.DataFrame()
    )
    have_on_date: set = set()
    for part in data_parts:
        have_on_date |= _daily_kline_codes_on_date(part, check_date)

    global_max_date: Optional[str] = None
    if not kline_all.empty and "tradeDate" in kline_all.columns:
        td = pd.to_datetime(kline_all["tradeDate"], errors="coerce")
        if td.notna().any():
            global_max_date = td.max().strftime("%Y-%m-%d")

    out: List[str] = []
    for code in candidate_codes:
        cu = code.upper()
        if cu in have_on_date:
            continue
        code_max = _code_max_kline_date(kline_all, cu)
        # 日 K 已覆盖到目标日（或更晚）则无需 snap
        if code_max and code_max >= check_date:
            continue
        # 仅当批内已有 ≥ check_date 的日 K 时，才用「已对齐全局最新」跳过（美股时区边界）
        if (
            global_max_date
            and global_max_date >= check_date
            and code_max == global_max_date
        ):
            continue
        out.append(code)
    return out


def _filter_snap_supplement_not_in_kline(
    snap_supplement: pd.DataFrame, daily_data: pd.DataFrame
) -> pd.DataFrame:
    """去掉与日 K 已存在 (证券, 交易日) 重复的 snap 行。"""
    if snap_supplement.empty:
        return snap_supplement
    existing = _daily_kline_code_date_pairs(daily_data)
    if not existing:
        return snap_supplement
    keep = []
    for _, row in snap_supplement.iterrows():
        code = str(row.get("securityCode", "")).upper()
        d = pd.to_datetime(row.get("tradeDate"), errors="coerce")
        ds = d.strftime("%Y-%m-%d") if pd.notna(d) else ""
        keep.append((code, ds) not in existing)
    return snap_supplement.loc[keep].copy()


def _fetch_snap_kline(
    headers: dict,
    all_market_markets: Optional[Tuple[str, ...]],
    snap_codes: List[str],
    error_prefix: str = "实时行情",
) -> Tuple[pd.DataFrame, List[str]]:
    """拉取实时截面行情，返回原始 API 列与错误列表。"""
    errors: List[str] = []
    parts: List[pd.DataFrame] = []
    snap_payload_base = {"fieldList": list(SNAP_FIELD_LIST)}
    if all_market_markets:
        for token in _snap_tokens_for_markets(all_market_markets):
            payload = {**snap_payload_base, "securityList": [token]}
            data_m, err_m = _fetch_kline_data(QUOTE_REALTIME_URL, headers, payload)
            if err_m:
                errors.append(f"{error_prefix}({token})请求失败: {err_m}")
            elif not data_m.empty:
                data_m, degenerate = _sanitize_snap_quote(data_m)
                if degenerate:
                    errors.append(
                        f"{error_prefix}({token})返回无效行情（价格全为零或缺失，可能未开市）"
                    )
                else:
                    parts.append(data_m)
    elif snap_codes:
        payload = {**snap_payload_base, "securityList": list(dict.fromkeys(snap_codes))}
        data_snap, err_snap = _fetch_kline_data(QUOTE_REALTIME_URL, headers, payload)
        if err_snap:
            errors.append(f"{error_prefix}请求失败: {err_snap}")
        elif not data_snap.empty:
            data_snap, degenerate = _sanitize_snap_quote(data_snap)
            if degenerate:
                errors.append(
                    f"{error_prefix}返回无效行情（价格全为零或缺失，可能未开市）"
                )
            else:
                parts.append(data_snap)
    if not parts:
        return pd.DataFrame(), errors
    return pd.concat(parts, ignore_index=True), errors


def _snap_rows_to_daily_kline(snap_df: pd.DataFrame) -> pd.DataFrame:
    """将 snap 字段映射为日 K 列结构（close 取 latestPrice）。"""
    if snap_df.empty:
        return snap_df
    out = pd.DataFrame()
    out["securityCode"] = snap_df["securityCode"]
    if "tradeDate" in snap_df.columns:
        out["tradeDate"] = snap_df["tradeDate"]
    else:
        out["tradeDate"] = date.today().strftime("%Y-%m-%d")
    for src, dst in [
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("preClose", "preClose"),
        ("change", "change"),
        ("pctChange", "pctChange"),
        ("volume", "volume"),
        ("amount", "amount"),
    ]:
        if src in snap_df.columns:
            out[dst] = snap_df[src]
    if "latestPrice" in snap_df.columns:
        out["close"] = snap_df["latestPrice"]
    elif "close" in snap_df.columns:
        out["close"] = snap_df["close"]
    return out


def _month_date_chunks(start_date: str, end_date: str) -> List[Tuple[str, str]]:
    s = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
    e = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
    out: List[Tuple[str, str]] = []
    cur = s
    while cur <= e:
        month_start = cur.replace(day=1)
        month_end = _last_day_of_month(month_start)
        seg_s = cur
        seg_e = min(e, month_end)
        out.append((seg_s.isoformat(), seg_e.isoformat()))
        cur = seg_e + timedelta(days=1)
    return out


def normalize_adjust_mode(raw: Optional[str]) -> str:
    """返回 none | forward | backward；默认前复权 forward。"""
    if raw is None or not str(raw).strip():
        return "forward"
    s = str(raw).strip()
    sl = s.lower()
    if sl in {"none", "raw", "no", "noadj", "unadjusted"} or s == "不复权":
        return "none"
    if sl in {"forward", "qfq"} or s == "前复权":
        return "forward"
    if sl in {"backward", "hfq"} or s == "后复权":
        return "backward"
    return "forward"


def _adj_price_suffix(mode: str) -> str:
    return "(前复权)" if mode == "forward" else "(后复权)"


def _daily_kline_field_list(adj_mode: str) -> List[str]:
    """日 K fieldList；复权时附带 adjustFactor（A/港/美 日 K 均已支持）。"""
    fields = list(_FIELD_LIST)
    if normalize_adjust_mode(adj_mode) != "none":
        fields.append("adjustFactor")
    return fields


def _kline_embedded_adjust_factors_df(data_cn: pd.DataFrame) -> pd.DataFrame:
    """从日 K 结果中提取 adjustFactor（列名可为英文或已映射中文）。"""
    if data_cn.empty:
        return pd.DataFrame()
    if "adjustFactor" in data_cn.columns:
        code_col, date_col, fac_col = "securityCode", "tradeDate", "adjustFactor"
        if "证券代码" in data_cn.columns:
            code_col = "证券代码"
        if "日期" in data_cn.columns:
            date_col = "日期"
        fac = data_cn.rename(
            columns={code_col: "securityCode", date_col: "tradeDate", fac_col: "adjustFactor"}
        )[["securityCode", "tradeDate", "adjustFactor"]].copy()
    elif "复权因子" in data_cn.columns:
        fac = data_cn.rename(
            columns={"证券代码": "securityCode", "日期": "tradeDate", "复权因子": "adjustFactor"}
        )[["securityCode", "tradeDate", "adjustFactor"]].copy()
    else:
        return pd.DataFrame()
    fac["adjustFactor"] = pd.to_numeric(fac["adjustFactor"], errors="coerce")
    return fac.dropna(subset=["adjustFactor"])


def _apply_kline_daily_adjust(
    data_cn: pd.DataFrame,
    adj_mode: str,
    headers: Optional[dict] = None,
    security_list: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 5000,
) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[str], Optional[str], Optional[str]]:
    """
    优先使用日 K 内嵌 adjustFactor；无有效因子时再请求独立复权因子接口（兼容旧数据）。
    返回 (复权数据, 不复权数据, hard_error, fac_warn, split_warn)。
    """
    factors = _kline_embedded_adjust_factors_df(data_cn)
    fac_warn: Optional[str] = None
    if factors.empty and headers and security_list and start_date and end_date:
        factors, fac_warn = fetch_adjust_factors_batched(
            headers, security_list, start_date, end_date, limit
        )
    work = data_cn.drop(columns=["adjustFactor", "复权因子"], errors="ignore")
    out_adj, out_raw, split_warn = _apply_daily_adjust_with_factors_optional_today(work, factors, adj_mode)
    if out_adj.empty and out_raw.empty and not work.empty:
        return pd.DataFrame(), pd.DataFrame(), "无法处理行情复权", fac_warn, split_warn
    return out_adj, out_raw, None, fac_warn, split_warn


def _fetch_adjust_factor_body(
    headers: dict,
    security_list: List[str],
    start_date: str,
    end_date: str,
    limit: int,
) -> Tuple[pd.DataFrame, Optional[str]]:
    payload = {
        "securityList": security_list,
        "startDate": start_date[:10],
        "endDate": end_date[:10],
        "limit": min(int(limit), 10000),
    }
    try:
        r = authorized_request("POST", QUOTE_ADJUST_FACTOR_URL, headers=headers, json=payload, timeout=300)
        if r.status_code != 200:
            return pd.DataFrame(), r.text
        body = r.json()
    except Exception as ex:
        return pd.DataFrame(), str(ex)
    return _parse_kline_body(body), None


def fetch_adjust_factors_batched(
    headers: dict,
    security_list: List[str],
    start_date: str,
    end_date: str,
    limit_per_request: int,
) -> Tuple[pd.DataFrame, Optional[str]]:
    """按自然月拆请求，合并去重，降低单次超 10000 行风险。"""
    chunks = _month_date_chunks(start_date, end_date)
    parts: List[pd.DataFrame] = []
    errs: List[str] = []
    for sd, ed in chunks:
        df, err = _fetch_adjust_factor_body(headers, security_list, sd, ed, limit_per_request)
        if err:
            errs.append(f"{sd}~{ed}: {err}")
            continue
        if not df.empty:
            parts.append(df)
    if errs and not parts:
        return pd.DataFrame(), "；".join(errs)
    if not parts:
        return pd.DataFrame(), "复权因子接口无数据" if not errs else "；".join(errs)
    merged = pd.concat(parts, ignore_index=True)
    if "securityCode" in merged.columns and "tradeDate" in merged.columns:
        merged = merged.drop_duplicates(subset=["securityCode", "tradeDate"], keep="last")
    warn = "；".join(errs) if errs else None
    return merged, warn


def _apply_daily_adjust_with_factors(
    data: pd.DataFrame, factors: pd.DataFrame, mode: str
) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[str]]:
    """
    使用 adjustFactor 调整日 K。缺少有效复权因子的证券单独按不复权返回。
    返回 (复权结果, 不复权结果, 提示信息)。
    """
    if factors.empty:
        return pd.DataFrame(), data.copy(), "未获取到复权因子，相关行情按不复权输出"
    need = {"证券代码", "日期", "开盘价", "最高价", "最低价", "收盘价"}
    if not need.issubset(data.columns):
        return pd.DataFrame(), data.copy(), "行情数据缺少 OHLC 列，无法复权"

    fac = factors.rename(columns={"securityCode": "证券代码", "tradeDate": "日期", "adjustFactor": "_f"})
    fac["日期"] = pd.to_datetime(fac["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    fac["证券代码"] = fac["证券代码"].astype(str).str.upper()
    fac["_f"] = pd.to_numeric(fac["_f"], errors="coerce")

    d = data.copy()
    d["_dt"] = pd.to_datetime(d["日期"], errors="coerce")
    d["日期"] = d["_dt"].dt.strftime("%Y-%m-%d")
    d["证券代码"] = d["证券代码"].astype(str).str.upper()

    d = d.merge(fac[["证券代码", "日期", "_f"]], on=["证券代码", "日期"], how="left")
    d = d.sort_values(by=["证券代码", "_dt"])
    d["_f"] = d.groupby("证券代码", sort=False)["_f"].ffill().bfill()

    missing = d["_f"].isna() | (d["_f"] == 0)
    data_unadj = pd.DataFrame()
    warn: Optional[str] = None
    if missing.any():
        bad_codes = set(d.loc[missing, "证券代码"].astype(str).str.upper())
        codes_u = data["证券代码"].astype(str).str.upper()
        data_unadj = data[codes_u.isin(bad_codes)].copy()
        data = data[~codes_u.isin(bad_codes)].copy()
        d = d[~d["证券代码"].astype(str).str.upper().isin(bad_codes)]
        hint_list = sorted(bad_codes)[:20]
        hint = ",".join(hint_list)
        if len(bad_codes) > 20:
            hint += "…"
        warn = f"以下证券缺少有效复权因子，已按不复权输出：{hint}"

    if d.empty:
        return pd.DataFrame(), data_unadj, warn

    d = d.sort_values(by=["证券代码", "_dt"])
    if mode == "forward":
        d["_f_anchor"] = d.groupby("证券代码", sort=False)["_f"].transform("last")
    else:
        d["_f_anchor"] = d.groupby("证券代码", sort=False)["_f"].transform("first")

    mult = d["_f"] / d["_f_anchor"]
    mult = mult.mask(d["_f_anchor"].isna() | (d["_f_anchor"] == 0))

    suffix = _adj_price_suffix(mode)
    for col in ["开盘价", "最高价", "最低价", "收盘价"]:
        if col in d.columns:
            v = pd.to_numeric(d[col], errors="coerce")
            d[f"{col}{suffix}"] = (v * mult).round(2)

    close_adj = f"收盘价{suffix}"
    raw_pre = pd.to_numeric(d["昨收价"], errors="coerce") if "昨收价" in d.columns else pd.Series(pd.NA, index=d.index)
    d = d.sort_values(by=["证券代码", "_dt"])
    pre_from_shift = d.groupby("证券代码", sort=False)[close_adj].shift(1)
    pre_from_raw = raw_pre * mult
    pre_adj = pre_from_shift.copy()
    use_raw = pre_from_shift.isna() & raw_pre.notna() & mult.notna()
    pre_adj = pre_adj.where(~use_raw, pre_from_raw)
    ca = pd.to_numeric(d[close_adj], errors="coerce")
    pa = pd.to_numeric(pre_adj, errors="coerce")
    d[f"昨收价{suffix}"] = pa.round(2)
    d[f"涨跌额{suffix}"] = (ca - pa).round(4)
    pct = (ca - pa) / pa * 100
    pct = pct.replace([float("inf"), float("-inf")], pd.NA)
    d[f"涨跌幅{suffix}"] = pct.round(4)

    drop_cols = ["开盘价", "最高价", "最低价", "收盘价", "昨收价", "涨跌额", "涨跌幅", "_dt", "_f", "_f_anchor"]
    adj_result = d.drop(columns=[c for c in drop_cols if c in d.columns], errors="ignore")
    return adj_result, data_unadj, warn


def _daily_today_rows_as_adjusted(data_today: pd.DataFrame, mode: str) -> pd.DataFrame:
    """当天行情（多为 snap 补全）不依赖复权因子，原价写入复权列名以与历史块列结构一致。"""
    if data_today.empty:
        return data_today
    d = data_today.copy()
    suffix = _adj_price_suffix(mode)
    for col in ["开盘价", "最高价", "最低价", "收盘价"]:
        if col in d.columns:
            d[f"{col}{suffix}"] = pd.to_numeric(d[col], errors="coerce").round(2)
    if "昨收价" in d.columns:
        d[f"昨收价{suffix}"] = pd.to_numeric(d["昨收价"], errors="coerce").round(2)
    if "涨跌额" in d.columns:
        d[f"涨跌额{suffix}"] = pd.to_numeric(d["涨跌额"], errors="coerce").round(4)
    if "涨跌幅" in d.columns:
        d[f"涨跌幅{suffix}"] = pd.to_numeric(d["涨跌幅"], errors="coerce").round(4)
    drop_cols = ["开盘价", "最高价", "最低价", "收盘价", "昨收价", "涨跌额", "涨跌幅"]
    return d.drop(columns=[c for c in drop_cols if c in d.columns], errors="ignore")


def _split_daily_today_hist(data: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    if data.empty or "日期" not in data.columns:
        return data, pd.DataFrame()
    today_str = date.today().strftime("%Y-%m-%d")
    dt = pd.to_datetime(data["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    today_mask = dt == today_str
    return data[~today_mask].copy(), data[today_mask].copy()


def _apply_daily_adjust_with_factors_optional_today(
    data: pd.DataFrame, factors: pd.DataFrame, mode: str
) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[str]]:
    """历史交易日走复权因子；当天（snap 补全）不要求复权因子。"""
    data_hist, data_today = _split_daily_today_hist(data)
    adj_parts: List[pd.DataFrame] = []
    raw_parts: List[pd.DataFrame] = []
    warns: List[str] = []
    if not data_hist.empty:
        hist_adj, hist_raw, w = _apply_daily_adjust_with_factors(data_hist, factors, mode)
        if w:
            warns.append(w)
        if not hist_adj.empty:
            adj_parts.append(hist_adj)
        if not hist_raw.empty:
            raw_parts.append(hist_raw)
    if not data_today.empty:
        adj_parts.append(_daily_today_rows_as_adjusted(data_today, mode))
    adj_out = pd.concat(adj_parts, ignore_index=True) if adj_parts else pd.DataFrame()
    raw_out = pd.concat(raw_parts, ignore_index=True) if raw_parts else pd.DataFrame()
    warn_out = "；".join(warns) if warns else None
    return adj_out, raw_out, warn_out


def _load_security_codes_from_file(path: str) -> List[str]:
    full_path = path
    if not os.path.exists(full_path):
        raise FileNotFoundError(f"证券文件不存在: {path}")
    df = pd.read_csv(full_path)
    if "security_code" not in df.columns:
        raise ValueError("证券文件须包含 security_code 列（完整代码或证券名称等关键词）")
    return [str(x) for x in df["security_code"].dropna().tolist()]


def _parse_kline_body(body: dict) -> pd.DataFrame:
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


def _fetch_kline_data(url: str, headers: dict, payload: dict) -> Tuple[pd.DataFrame, Optional[str]]:
    try:
        r = authorized_request("POST", url, headers=headers, json=payload, timeout=300)
        if r.status_code != 200:
            return pd.DataFrame(), r.text
        body = r.json()
    except Exception as e:
        return pd.DataFrame(), str(e)

    data = _parse_kline_body(body)
    if data.empty:
        return pd.DataFrame(), "未找到行情数据"
    return data, None


def _resolve_quote_dates(
    all_market_markets: Optional[Tuple[str, ...]],
    start_date: Optional[str],
    end_date: Optional[str],
) -> Tuple[str, str, bool]:
    """返回 (start_date, end_date, all_market_date_fallback)。

    - 非全市场且均未指定：默认近 7 日（含今天）。
    - 全市场且均未指定：默认当天，并由调用方逐日回退取数。
    """
    both_empty = not start_date and not end_date
    today = date.today().strftime("%Y-%m-%d")
    all_market = all_market_markets is not None

    if all_market and both_empty:
        return today, today, True

    if not end_date:
        end_date = today
    if not start_date:
        if all_market:
            start_date = end_date
        else:
            end_d = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
            start_date = (end_d - timedelta(days=DEFAULT_QUOTE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    return start_date, end_date, False


def _fetch_full_market_daily_with_date_fallback(
    headers: dict,
    security_token: str,
    adj_mode: str,
    limit: int,
    snap_markets: Optional[Tuple[str, ...]] = None,
    max_lookback: int = ALL_MARKET_DATE_LOOKBACK_DAYS,
) -> Tuple[pd.DataFrame, Optional[str], Optional[str], Optional[str], bool]:
    """全市场日 K 未指定日期时：从当天起逐日向前查询，当天可先尝试实时快照补全。

    security_token 为市场标识（aShares/hkStocks/usStocks），统一走日 K 接口。
    返回 (data, err, used_date, fallback_note, used_snap_supplement)。
    """
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    last_err: Optional[str] = None
    daily_fields = _daily_kline_field_list(adj_mode)

    for offset in range(max_lookback):
        query_date = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        payload = {
            "startDate": query_date,
            "endDate": query_date,
            "limit": limit,
            "fieldList": daily_fields,
            "securityList": [security_token],
        }
        data_one, err_one = _fetch_kline_data(QUOTE_URL, headers, payload)
        if not err_one and not data_one.empty:
            fb_note = None
            if offset > 0:
                fb_note = f"当日（{today_str}）无数据，已回退至 {query_date}"
            return data_one, None, query_date, fb_note, False

        if err_one:
            last_err = err_one
        else:
            last_err = "未找到行情数据"

        if offset == 0 and query_date == today_str and snap_markets:
            snap_raw, _snap_errs = _fetch_snap_kline(
                headers,
                snap_markets,
                [],
                error_prefix="实时快照补全",
            )
            if not snap_raw.empty:
                snap_daily = _snap_rows_to_daily_kline(snap_raw)
                if not snap_daily.empty:
                    return snap_daily, None, query_date, None, True

    return pd.DataFrame(), last_err, None, None, False


def quote_data(
    securities: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 5000,
    all_market_markets: Optional[Tuple[str, ...]] = None,
    data_type: str = "daily",
    adjust_mode: Optional[str] = None,
):
    usage: dict = {}
    if not get_authorization_token():
        return format_response(
            {"state": "error", "message": "未配置 gangtise 授权，无法调用 open 接口", "data": [], "usage": usage},
            "quote",
        )

    headers = get_authorization_headers()
    data_type = (data_type or "daily").strip().lower()
    if data_type not in {"daily", "minute", "snap"}:
        return format_response(
            {"state": "error", "message": "type 仅支持 daily、minute 或 snap", "data": [], "usage": usage},
            "quote",
        )
    if data_type == "snap":
        if adjust_mode is not None and normalize_adjust_mode(adjust_mode) != "none":
            return format_response(
                {
                    "state": "error",
                    "message": "截面行情（snap）不支持复权调整",
                    "data": [],
                    "usage": usage,
                },
                "quote",
            )
        adj_mode = "none"
    elif data_type == "minute":
        if adjust_mode is not None and normalize_adjust_mode(adjust_mode) != "none":
            return format_response(
                {
                    "state": "error",
                    "message": "分钟 K 不支持复权调整，请使用 type=daily（日 K）",
                    "data": [],
                    "usage": usage,
                },
                "quote",
            )
        adj_mode = "none"
    else:
        adj_mode = normalize_adjust_mode(adjust_mode)

    start_date, end_date, all_market_date_fallback = _resolve_quote_dates(
        all_market_markets, start_date, end_date
    )
    fallback_note: Optional[str] = None
    all_market_snap_done: Set[str] = set()

    resolved_types: List[str] = []
    daily_a_codes: List[str] = []
    daily_hk_codes: List[str] = []
    daily_idx_codes: List[str] = []
    daily_us_codes: List[str] = []
    snap_codes: List[str] = []
    skipped_quote: List[Tuple[str, str]] = []
    abbr_map: Dict[str, str] = {}

    if not all_market_markets:
        codes: List[str] = []
        if securities:
            tokens = parse_str_list(securities)
            resolved = batch_security_search(
                tokens,
                category=["stock", "dr", "index"],
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
                    "quote",
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
                    "message": "未解析到有效证券代码，请传入代码或名称，或使用 --all-market",
                    "data": [],
                    "usage": usage,
                },
                "quote",
            )
        while len(resolved_types) < len(codes):
            resolved_types.append("")
        if data_type == "daily":
            daily_a_codes, daily_hk_codes, daily_idx_codes, daily_us_codes, skipped_quote = (
                _partition_daily_quote_codes(codes, resolved_types)
            )
        elif data_type == "snap":
            snap_codes, skipped_quote = _partition_snap_quote_codes(codes, resolved_types)
        else:
            # 分钟 K 支持 A 股个股及交易所/概念/行业指数；港股/美股等跳过不报错
            for i, c in enumerate(codes):
                t = resolved_types[i] if i < len(resolved_types) else ""
                if t in ("A股", "交易所指数", "行业指数", "概念指数", "其他指数", "指数"):
                    daily_a_codes.append(c)
                else:
                    skipped_quote.append((c, t or "未知类型"))

    data_parts: List[pd.DataFrame] = []
    snap_daily_supplement: Optional[pd.DataFrame] = None
    request_errors: List[str] = []
    capped_limit = min(limit, 10000)
    extra_notes: List[str] = []

    if data_type == "snap":
        data_snap, snap_errs = _fetch_snap_kline(headers, all_market_markets, snap_codes)
        request_errors.extend(snap_errs)
        if not data_snap.empty:
            data_parts.append(data_snap)
    elif data_type == "daily":
        daily_fields = _daily_kline_field_list(adj_mode)
        payload: dict = {
            "startDate": start_date,
            "endDate": end_date,
            "limit": capped_limit,
            "fieldList": daily_fields,
        }

        if all_market_markets:
            for mkt in all_market_markets:
                token = _ALL_MARKET_TO_TOKEN[mkt]
                label = _ALL_MARKET_LABEL[mkt]
                if all_market_date_fallback:
                    data_m, err_m, used_date, fb_note, used_snap = (
                        _fetch_full_market_daily_with_date_fallback(
                            headers,
                            token,
                            adj_mode,
                            capped_limit,
                            snap_markets=(mkt,),
                        )
                    )
                    if used_date:
                        start_date = end_date = used_date
                    if fb_note:
                        note = f"{label}{fb_note}"
                        fallback_note = f"{fallback_note}；{note}" if fallback_note else note
                    if used_snap:
                        all_market_snap_done.add(mkt)
                        extra_notes.append(
                            f"{label}当日行情由实时快照接口补全（日K接口不提供当天数据）"
                        )
                    if err_m:
                        request_errors.append(f"{label}接口请求失败: {err_m}")
                    elif not data_m.empty:
                        data_parts.append(data_m)
                else:
                    payload_m = dict(payload)
                    payload_m["securityList"] = [token]
                    data_m, err_m = _fetch_kline_data(QUOTE_URL, headers, payload_m)
                    if err_m:
                        request_errors.append(f"{label}接口请求失败: {err_m}")
                    elif not data_m.empty:
                        data_parts.append(data_m)
        else:
            daily_all_codes = list(
                dict.fromkeys(
                    daily_a_codes + daily_hk_codes + daily_idx_codes + daily_us_codes
                )
            )
            if daily_all_codes:
                payload_all = dict(payload)
                payload_all["securityList"] = daily_all_codes
                data_all, err_all = _fetch_kline_data(QUOTE_URL, headers, payload_all)
                if err_all:
                    request_errors.append(f"日K接口请求失败: {err_all}")
                elif not data_all.empty:
                    data_parts.append(data_all)

        if _end_date_includes_today(end_date):
            check_date = end_date[:10]
            snap_codes_for_supplement: List[str] = []
            if not all_market_markets:
                candidates = list(
                    dict.fromkeys(
                        daily_a_codes + daily_hk_codes + daily_idx_codes + daily_us_codes
                    )
                )
                snap_codes_for_supplement = _codes_needing_snap_supplement(
                    data_parts, candidates, check_date
                )
            markets_needing_snap = tuple(
                m for m in (all_market_markets or ()) if m not in all_market_snap_done
            )
            if markets_needing_snap or snap_codes_for_supplement:
                snap_raw, snap_supp_errs = _fetch_snap_kline(
                    headers,
                    markets_needing_snap if markets_needing_snap else None,
                    snap_codes_for_supplement,
                    error_prefix="实时快照补全",
                )
                request_errors.extend(snap_supp_errs)
                if not snap_raw.empty:
                    snap_daily_supplement = _snap_rows_to_daily_kline(snap_raw)
                    kline_so_far = (
                        pd.concat(data_parts, ignore_index=True)
                        if data_parts
                        else pd.DataFrame()
                    )
                    snap_daily_supplement = _filter_snap_supplement_not_in_kline(
                        snap_daily_supplement, kline_so_far
                    )
                    if not snap_daily_supplement.empty:
                        extra_notes.append(
                            "当日行情由实时快照接口补全（日K接口不提供当天数据）"
                        )
                    else:
                        snap_daily_supplement = None
    else:
        if all_market_markets:
            return format_response(
                {
                    "state": "error",
                    "message": "minute 类型不支持 --all-market，请指定 --securities / --securities-file",
                    "data": [],
                    "usage": usage,
                },
                "quote",
            )
        # 分钟接口仅支持 A 股 securityCode 单值；港股/指数等在 skipped_quote 中说明，不报错中断。
        minute_codes = list(dict.fromkeys(daily_a_codes))
        if not minute_codes:
            uns = _unsupported_quote_note(skipped_quote)
            msg = uns if uns else "minute 类型未找到有效证券代码"
            if uns:
                msg = f"{msg}；未拉取到分钟行情数据"
            return format_response(
                {"state": "error", "message": msg, "data": [], "usage": usage},
                "quote",
            )

        def _fetch_one_minute(code: str) -> Tuple[str, pd.DataFrame, Optional[str]]:
            payload_one = {
                "securityCode": code,
                "startTime": start_date,
                "endTime": end_date,
                "limit": capped_limit,
                "fieldList": list(MINUTE_FIELD_LIST),
            }
            data_one, err_one = _fetch_kline_data(QUOTE_MINUTE_URL, headers, payload_one)
            return code, data_one, err_one

        max_workers = min(8, max(1, len(minute_codes)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_fetch_one_minute, code) for code in minute_codes]
            for fut in as_completed(futures):
                code, data_one, err_one = fut.result()
                if err_one:
                    request_errors.append(f"{code} 请求失败: {err_one}")
                elif not data_one.empty:
                    data_parts.append(data_one)

    if not data_parts and snap_daily_supplement is None:
        uns = _unsupported_quote_note(skipped_quote)
        err_tail = "；".join(request_errors) if request_errors else ""
        pieces = [p for p in [uns, err_tail] if p]
        error_message = "；".join(pieces) if pieces else "未找到行情数据"
        return format_response(
            {"state": "error", "message": error_message, "data": [], "usage": usage},
            "quote",
        )

    if data_parts:
        data = pd.concat(data_parts, ignore_index=True)
    else:
        data = pd.DataFrame()

    if snap_daily_supplement is not None and not snap_daily_supplement.empty:
        snap_daily_supplement = _filter_snap_supplement_not_in_kline(
            snap_daily_supplement, data
        )
        if not snap_daily_supplement.empty:
            data = pd.concat([data, snap_daily_supplement], ignore_index=True)

    if data_type == "daily":
        field_map = API_FIELD_TO_CN
    elif data_type == "minute":
        field_map = MINUTE_API_FIELD_TO_CN
    else:
        field_map = SNAP_API_FIELD_TO_CN
    rename_map = {k: v for k, v in field_map.items() if k in data.columns}
    data = data.rename(columns=rename_map)

    if "证券代码" in data.columns:
        codes_u = data["证券代码"].astype(str).str.strip().str.upper()
        data["证券简称"] = codes_u.map(lambda c: abbr_map.get(c, c))
    else:
        data["证券简称"] = ""
    if data_type == "daily" and "日期" in data.columns:
        data["日期"] = pd.to_datetime(data["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    elif data_type == "minute" and "tradeTime" in data.columns:
        data["日期"] = pd.to_datetime(data["tradeTime"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
    elif data_type == "snap":
        if "交易日期" in data.columns:
            data["交易日期"] = pd.to_datetime(data["交易日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    if start_date and data_type == "daily":
        sd = pd.to_datetime(start_date, errors="coerce")
        data = data[pd.to_datetime(data["日期"], errors="coerce") >= sd]
    elif start_date and data_type == "minute":
        sd = pd.to_datetime(start_date, errors="coerce")
        data = data[pd.to_datetime(data["日期"], errors="coerce") >= sd]
    if end_date and data_type == "daily":
        ed = pd.to_datetime(end_date, errors="coerce")
        data = data[pd.to_datetime(data["日期"], errors="coerce") <= ed]
    elif end_date and data_type == "minute":
        ed = pd.to_datetime(end_date, errors="coerce") + timedelta(days=1)
        data = data[pd.to_datetime(data["日期"], errors="coerce") <= ed]
    if "日期" in data.columns:
        data = data.dropna(subset=["日期"])

    if data.empty:
        empty_msg = "未找到截面行情数据" if data_type == "snap" else "日期范围内未找到行情数据"
        return format_response(
            {"state": "error", "message": empty_msg, "data": [], "usage": usage},
            "quote",
        )

    # 日 K 复权：优先使用日 K 内嵌 adjustFactor（A/港/美）；缺失时再走独立复权因子接口
    # 指数通常无复权因子，保持原价量列；多市场混查时分块输出
    output_blocks: List[Tuple[pd.DataFrame, bool, str]] = []

    def _append_adjusted_block(
        sub: pd.DataFrame,
        codes: List[str],
        segment: str,
    ) -> None:
        if sub.empty:
            return
        out_adj, out_raw, adj_err, fac_warn, split_warn = _apply_kline_daily_adjust(
            sub,
            adj_mode,
            headers=headers,
            security_list=codes if codes else None,
            start_date=start_date,
            end_date=end_date,
            limit=capped_limit,
        )
        if fac_warn:
            extra_notes.append(f"复权因子接口：{fac_warn}")
        if split_warn:
            extra_notes.append(split_warn)
        if adj_err:
            raise ValueError(adj_err)
        if not out_adj.empty:
            output_blocks.append((out_adj, True, segment))
        if not out_raw.empty:
            output_blocks.append((out_raw, False, segment))

    if data_type == "snap":
        output_blocks.append((data, False, "single"))
    elif data_type == "daily" and adj_mode != "none":
        try:
            if all_market_markets:
                if len(all_market_markets) == 1 and all_market_markets[0] == "cn":
                    _append_adjusted_block(data, ["all"], "all_market")
                else:
                    codes_u = data["证券代码"].astype(str).str.upper()
                    seg_map = {
                        "cn": "all_market" if all_market_markets == ("cn",) else "a_share",
                        "hk": "hk_share",
                        "us": "us_share",
                    }
                    for mkt in all_market_markets:
                        sub = data[_market_code_mask(codes_u, mkt)].copy()
                        if not sub.empty:
                            _append_adjusted_block(sub, ["all"], seg_map[mkt])
                    if len(output_blocks) > 1:
                        extra_notes.append(
                            "多市场/多品种列结构可能不同（复权列 vs 原价量列），已分多张表输出。"
                        )
            elif daily_a_codes or daily_hk_codes or daily_us_codes:
                ac_set = {c.upper() for c in daily_a_codes}
                hk_set = {c.upper() for c in daily_hk_codes}
                us_set = {c.upper() for c in daily_us_codes}
                idx_set = {c.upper() for c in daily_idx_codes}
                codes_u = data["证券代码"].astype(str).str.upper()

                if daily_a_codes:
                    data_a = data[codes_u.isin(ac_set)].copy()
                    _append_adjusted_block(data_a, daily_a_codes, "a_share")

                if daily_hk_codes:
                    data_hk = data[codes_u.isin(hk_set)].copy()
                    _append_adjusted_block(data_hk, daily_hk_codes, "hk_share")

                if daily_us_codes:
                    data_us = data[codes_u.isin(us_set)].copy()
                    _append_adjusted_block(data_us, daily_us_codes, "us_share")

                if daily_idx_codes:
                    data_idx = data[codes_u.isin(idx_set)].copy()
                    if not data_idx.empty:
                        output_blocks.append((data_idx, False, "hk_index"))

                if len(output_blocks) > 1:
                    extra_notes.append(
                        "多市场/多品种列结构可能不同（复权列 vs 原价量列），已分多张表输出。"
                    )
            elif daily_idx_codes:
                output_blocks.append((data, False, "hk_index"))
            else:
                _append_adjusted_block(data, [], "single")
        except ValueError as adj_err:
            return format_response(
                {"state": "error", "message": str(adj_err), "data": [], "usage": usage},
                "quote",
            )
    else:
        output_blocks.append((data, False, "single"))

    formatted_tables: List[pd.DataFrame] = []
    for sub_df, prefer_adj, _seg in output_blocks:
        if sub_df is None or sub_df.empty:
            continue
        formatted_tables.append(
            _quote_format_output_table(sub_df, data_type, adj_mode, prefer_adj)
        )

    if not formatted_tables:
        return format_response(
            {"state": "error", "message": "日期范围内未找到行情数据", "data": [], "usage": usage},
            "quote",
        )

    hk_code_set = frozenset(c.upper() for c in daily_hk_codes)
    idx_code_set = frozenset(c.upper() for c in daily_idx_codes)
    a_code_set = frozenset(c.upper() for c in daily_a_codes)
    us_code_set = frozenset(c.upper() for c in daily_us_codes)
    title_segments: List[str] = []
    for sub_df, prefer_adj, seg in output_blocks:
        if sub_df is None or sub_df.empty:
            continue
        title_segments.append(
            _quote_title_segment_for_block(
                sub_df,
                seg,
                prefer_adj,
                data_type,
                adj_mode,
                hk_code_set,
                idx_code_set,
                a_code_set,
                us_code_set,
                all_market_markets=all_market_markets,
            )
        )
    default_title = {
        "minute": "分钟K行情数据",
        "snap": "截面行情数据",
    }.get(data_type, "日K行情数据")
    title = "；".join(title_segments) if title_segments else default_title

    skip_note = _unsupported_quote_note(skipped_quote)
    if skip_note:
        extra_notes.insert(0, skip_note)
    if fallback_note:
        extra_notes.append(fallback_note)

    msg = f"已找到{title}"
    if extra_notes:
        msg += "\n" + "\n".join(extra_notes)

    parts = [
        {"data": ft.to_dict(orient="records"), "module": "quote", "type": "data"}
        for ft in formatted_tables
    ]
    return format_response(
        {
            "state": "success",
            "message": msg,
            "data": parts,
            "usage": usage,
        },
        "quote",
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
        description="查询 A/港/美/指数行情（日K、分钟K、截面 snap）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-sd", "--start-date", default=None, help="开始日期 yyyy-mm-dd；未指定时非全市场默认近 7 日")
    parser.add_argument("-ed", "--end-date", default=None, help="结束日期 yyyy-mm-dd；未指定时默认今天")
    parser.add_argument(
        "--securities",
        default=None,
        help="证券逗号分隔（完整代码或名称/拼音等）；与 --all-market 二选一",
    )
    parser.add_argument(
        "--securities-file",
        default=None,
        help="csv 须含 security_code 列（代码或名称）",
    )
    parser.add_argument(
        "--all-market",
        nargs="?",
        const="cn",
        default=None,
        metavar="MARKET",
        help="全市场：不带值默认 A 股(cn)；可指定 cn/hk/us，逗号分隔",
    )
    parser.add_argument("-l", "--limit", type=int, default=5000, help="单次请求最大行数（上限 10000）")
    parser.add_argument(
        "--type",
        dest="data_type",
        choices=["daily", "minute", "snap"],
        default="daily",
        help="daily=日K（A/港/美/指数分流）；minute=分钟K（仅A股）；snap=实时截面行情（A/港/美，--all-market 可选市场）",
    )
    parser.add_argument(
        "--adjust",
        default=None,
        help="复权方式（仅日 K）：forward/qfq/前复权（默认）、backward/hfq/后复权、none/raw/不复权",
    )
    args = parser.parse_args()

    try:
        all_market_markets = _parse_all_market_arg(args.all_market)
    except ValueError as e:
        parser.error(str(e))

    if all_market_markets and (args.securities or args.securities_file):
        parser.error("--all-market 时不要使用 --securities / --securities-file")
    if args.data_type == "minute" and all_market_markets:
        parser.error("minute 类型不支持 --all-market，请指定 --securities / --securities-file")
    if args.data_type == "snap" and args.adjust:
        adj = normalize_adjust_mode(args.adjust)
        if adj != "none":
            parser.error("snap 类型不支持 --adjust")

    securities: Optional[List[str]] = None
    if args.securities:
        securities = [x.strip() for x in args.securities.replace("，", ",").split(",") if x.strip()]
    if not securities and args.securities_file:
        try:
            securities = _load_security_codes_from_file(args.securities_file)
        except Exception as e:
            print(f"根据证券文件解析证券失败: {e}")
            sys.exit(1)

    if not all_market_markets and not securities:
        parser.error("请指定 --securities / --securities-file，或使用 --all-market")

    out = quote_data(
        securities=securities,
        start_date=args.start_date,
        end_date=args.end_date,
        limit=args.limit,
        all_market_markets=all_market_markets,
        data_type=args.data_type,
        adjust_mode=args.adjust,
    )
    print(out)


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
