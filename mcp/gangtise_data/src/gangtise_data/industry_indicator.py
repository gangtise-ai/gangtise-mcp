import os
import re
import sys
from datetime import date, timedelta
from io import TextIOWrapper
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (EDB_GET_DATA_URL, EDB_SEARCH_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra)

SEARCH_DEFAULT_LIMIT = 10
SEARCH_MAX_LIMIT = 200
GET_BATCH_SIZE = 10
POINTS_PER_INDICATOR = 3
INDICATORS_FILE_HINT = "可以删除行或编辑参数列，再通过--indicators-file参数读取文件查询指标代码和参数"

FREQUENCY_MODULE_SUFFIX = {
    "日": "daily",
    "周": "weekly",
    "月": "monthly",
    "季": "quarterly",
    "季度": "quarterly",
    "半年": "halfyearly",
    "年": "yearly",
}


def _parse_str_list(raw: str) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    return [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]


def _is_indicators_arg_ids(raw: str) -> bool:
    s = (raw or "").strip()
    return bool(s) and re.fullmatch(r"[a-zA-Z,，0-9]+", s) is not None


def _load_indicators_from_file(path: str) -> List[str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"指标文件不存在: {path}")
    df = pd.read_csv(path)
    col = None
    for c in ("indicator_id", "indicatorId", "indicatorIdList"):
        if c in df.columns:
            col = c
            break
    if col is None:
        raise ValueError("指标文件须包含 indicator_id 或 indicatorId 列")
    return [str(x).strip() for x in df[col].dropna().tolist() if str(x).strip()]


def _frequency_module_tag(freq: Optional[str]) -> str:
    if not freq:
        return "unknown"
    f = str(freq).strip()
    if f in FREQUENCY_MODULE_SUFFIX:
        return FREQUENCY_MODULE_SUFFIX[f]
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in f)
    return safe or "unknown"


def _chunks(items: List[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _search_indicators(
    headers: dict,
    keyword: str,
    limit: int,
) -> Tuple[List[dict], Optional[str]]:
    req_limit = max(1, min(int(limit), SEARCH_MAX_LIMIT))
    payload = {"keyword": keyword.strip(), "limit": req_limit}
    try:
        r = authorized_request("POST", EDB_SEARCH_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return [], f"指标检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"指标检索请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"指标检索接口错误: {_api_error_message(body)}"

    data = body.get("data") or []
    if not isinstance(data, list):
        return [], "指标检索返回 data 格式异常"
    return data, None


def _parse_get_data_body(body: dict) -> pd.DataFrame:
    if not body or str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return pd.DataFrame()
    block = body.get("data") or {}
    field_list = block.get("fieldList") or []
    rows = block.get("dataList") or []
    if not field_list or not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=field_list)
    if "date" in df.columns:
        df["date"] = df["date"].astype(str).str.slice(0, 10)
    for col in df.columns:
        if col != "date":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _fetch_get_data_batch(
    headers: dict,
    indicator_ids: List[str],
    start_date: str,
    end_date: str,
) -> Tuple[pd.DataFrame, Optional[str]]:
    payload = {
        "indicatorIdList": indicator_ids,
        "startDate": start_date,
        "endDate": end_date,
    }
    try:
        r = authorized_request("POST", EDB_GET_DATA_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return pd.DataFrame(), f"HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return pd.DataFrame(), str(e)

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return pd.DataFrame(), _api_error_message(body, "时序数据接口错误")

    df = _parse_get_data_body(body)
    if df.empty:
        return df, "时序数据为空"
    return df, None


def fetch_indicator_timeseries_batched(
    headers: dict,
    indicator_ids: List[str],
    start_date: str,
    end_date: str,
) -> Tuple[pd.DataFrame, Optional[str], int]:
    """按每批最多 10 个指标拉取并 outer merge；返回 (df, warn, 成功拉取的指标数)。"""
    ids = [str(x).strip() for x in indicator_ids if str(x).strip()]
    if not ids:
        return pd.DataFrame(), "指标 ID 列表为空", 0

    parts: List[pd.DataFrame] = []
    errs: List[str] = []
    fetched_count = 0

    for batch in _chunks(ids, GET_BATCH_SIZE):
        df, err = _fetch_get_data_batch(headers, batch, start_date, end_date)
        if err:
            errs.append(f"[{','.join(batch)}] {err}")
            continue
        if df.empty:
            errs.append(f"[{','.join(batch)}] 无数据")
            continue
        metric_cols = [c for c in df.columns if c != "date"]
        fetched_count += len(metric_cols)
        parts.append(df)

    if not parts:
        return pd.DataFrame(), "；".join(errs) if errs else "未获取到时序数据", 0

    merged = parts[0]
    for part in parts[1:]:
        merged = merged.merge(part, on="date", how="outer")

    merged = merged.sort_values(by="date", ascending=False).reset_index(drop=True)
    warn = "；".join(errs) if errs else None
    return merged, warn, fetched_count


def _exact_match_items(keyword: str, items: List[dict]) -> List[dict]:
    kw = keyword.strip()
    kw_lower = kw.lower()
    return [
        x
        for x in items
        if str(x.get("indicatorName", "")).strip() == kw
        or str(x.get("indicatorId", "")).strip() == kw
        or str(x.get("indicatorCode", "")).strip().lower() == kw_lower
    ]


def _items_to_markdown(keyword: str, items: List[dict], exact_only: bool) -> str:
    lines = [f"### 关键词：{keyword}", ""]
    if exact_only:
        lines.append(f"共 {len(items)} 条完全匹配：")
    else:
        lines.append(f"共 {len(items)} 条候选：")
    lines.append("")
    for item in items:
        lines.append(
            f"- **{item.get('indicatorName')}** | ID: `{item.get('indicatorId')}` | "
            f"频率: {item.get('frequency')} | 单位: {item.get('unit')}"
        )
    lines.append("")
    lines.append(INDICATORS_FILE_HINT)
    return "\n".join(lines)


def _resolve_indicators_from_arg(
    headers: dict,
    raw: str,
    limit: int,
) -> Tuple[Optional[List[str]], Optional[str], Optional[str]]:
    """返回 (indicator_ids, search_markdown, error)。"""
    if _is_indicators_arg_ids(raw):
        return _parse_str_list(raw), None, None

    resolved: List[str] = []
    meta: List[dict] = []
    for token in _parse_str_list(raw):
        items, err = _search_indicators(headers, token, limit)
        if err:
            return None, None, err
        if not items:
            return None, None, f"未找到与「{token}」相关的行业指标"
        exact = _exact_match_items(token, items)
        if len(exact) == 1:
            item = exact[0]
            iid = str(item.get("indicatorId") or "").strip()
            if iid:
                resolved.append(iid)
                meta.append(item)
            continue
        display = exact if exact else items
        exact_only = bool(exact)
        if len(exact) > 1:
            prefix = f"「{token}」存在 {len(exact)} 条完全匹配，请指定更精确的指标：\n\n"
        else:
            prefix = f"未找到与「{token}」完全匹配的指标，以下为候选结果：\n\n"
        return None, prefix + _items_to_markdown(token, display, exact_only), None
    return resolved, None, None


def _meta_list_to_df(items: List[dict]) -> pd.DataFrame:
    records = []
    for item in items:
        records.append(
            {
                "indicator_id": item.get("indicatorId"),
                "indicator_name": item.get("indicatorName"),
                "data_source": item.get("dataSource"),
                "frequency": item.get("frequency"),
                "unit": item.get("unit"),
            }
        )
    return pd.DataFrame(records)


def _apply_id_name_columns(df: pd.DataFrame, meta: List[dict]) -> pd.DataFrame:
    id_to_name = {
        str(m.get("indicatorId")): str(m.get("indicatorName"))
        for m in meta
        if m.get("indicatorId") and m.get("indicatorName")
    }
    rename = {k: v for k, v in id_to_name.items() if k in df.columns}
    if rename:
        df = df.rename(columns=rename)
    return df


def _filter_by_date(df: pd.DataFrame, start_date: Optional[str], end_date: Optional[str]) -> pd.DataFrame:
    if df.empty or "date" not in df.columns:
        return df
    out = df.copy()
    dt = pd.to_datetime(out["date"], errors="coerce")
    if start_date:
        out = out[dt >= pd.to_datetime(start_date, errors="coerce")]
        dt = pd.to_datetime(out["date"], errors="coerce")
    if end_date:
        out = out[dt <= pd.to_datetime(end_date, errors="coerce")]
    return out.reset_index(drop=True)


def _parts_from_timeseries(
    df: pd.DataFrame,
    meta: List[dict],
    title: str,
) -> List[dict]:
    if df.empty:
        return []

    id_to_freq = {
        str(m.get("indicatorId")): m.get("frequency")
        for m in meta
        if m.get("indicatorId")
    }
    name_to_freq = {
        str(m.get("indicatorName")): m.get("frequency")
        for m in meta
        if m.get("indicatorName")
    }

    metric_cols = [c for c in df.columns if c != "date"]
    freq_groups: Dict[str, List[str]] = {}
    for col in metric_cols:
        freq = id_to_freq.get(col) or name_to_freq.get(col) or "unknown"
        tag = _frequency_module_tag(freq)
        freq_groups.setdefault(tag, []).append(col)

    parts = []
    for tag, cols in freq_groups.items():
        sub = df[["date"] + cols].copy()
        sub = sub.sort_values(by="date", ascending=False).reset_index(drop=True)
        metric_cols = [c for c in sub.columns if c != "date"]
        if metric_cols:
            sub = sub.dropna(subset=metric_cols, how="all")
        if sub.empty:
            continue
        parts.append(
            {
                "data": sub.to_dict(orient="records"),
                "module": f"industry_indicator_{tag}",
                "type": "data",
            }
        )
    if not parts and not df.empty:
        parts.append(
            {
                "data": df.to_dict(orient="records"),
                "module": "industry_indicator",
                "type": "data",
            }
        )
    return parts


def _default_dates(
    start_date: Optional[str],
    end_date: Optional[str],
) -> Tuple[str, str]:
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
    return start_date, end_date


def industry_indicator_search(
    keyword: str,
    limit: int = SEARCH_DEFAULT_LIMIT,
    append_file_hint: bool = False,
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
            "industry_indicator", output_dir=output_dir)

    kw = (keyword or "").strip()
    if not kw:
        return format_response(
            {"state": "error", "message": "keyword 不能为空", "data": [], "usage": usage},
            "industry_indicator", output_dir=output_dir)

    headers = get_authorization_headers()
    items, err = _search_indicators(headers, kw, limit)
    if err:
        return format_response(
            {"state": "error", "message": err, "data": [], "usage": usage},
            "industry_indicator", output_dir=output_dir)
    if not items:
        return format_response(
            {
                "state": "error",
                "message": f"未找到与「{kw}」相关的行业指标",
                "data": [],
                "usage": usage,
            },
            "industry_indicator", output_dir=output_dir)

    meta_df = _meta_list_to_df(items)
    parts = [
        {
            "data": meta_df.to_dict(orient="records"),
            "module": "industry_indicator_search",
            "type": "data",
            "footer": INDICATORS_FILE_HINT if append_file_hint else None,
        }
    ]
    msg = f"已检索到 {len(meta_df)} 条与「{kw}」相关的行业指标"
    return format_response(
        {
            "state": "success",
            "message": msg,
            "data": parts,
            "usage": usage,
        },
        "industry_indicator", output_dir=output_dir)


def industry_indicator_get(
    indicator_ids: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    meta: Optional[List[dict]] = None,
    limit: Optional[int] = None,
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
            "industry_indicator", output_dir=output_dir)

    ids = [str(x).strip() for x in indicator_ids if str(x).strip()]
    if not ids:
        return format_response(
            {"state": "error", "message": "指标 ID 列表不能为空", "data": [], "usage": usage},
            "industry_indicator", output_dir=output_dir)
    if limit is not None and limit > 0:
        ids = ids[: int(limit)]

    start_date, end_date = _default_dates(start_date, end_date)
    headers = get_authorization_headers()

    df, warn, fetched = fetch_indicator_timeseries_batched(
        headers, ids, start_date, end_date
    )
    if df.empty:
        return format_response(
            {
                "state": "error",
                "message": warn or "未获取到指标时序数据",
                "data": [],
                "usage": usage,
            },
            "industry_indicator", output_dir=output_dir)

    if meta:
        df = _apply_id_name_columns(df, meta)
    df = _filter_by_date(df, start_date, end_date)

    if fetched > 0:
        usage["edb_get_data"] = usage.get("edb_get_data", 0) + fetched * POINTS_PER_INDICATOR

    title = f"{len(ids)} 个行业指标"
    parts = _parts_from_timeseries(df, meta or [], title)
    if not parts:
        return format_response(
            {"state": "error", "message": "时序数据组装失败", "data": [], "usage": usage},
            "industry_indicator", output_dir=output_dir)

    msg = f"已获取{title}时序数据（{start_date} 至 {end_date}）"
    if warn:
        msg += f"；部分批次异常：{warn}"
    return format_response(
        {"state": "success", "message": msg, "data": parts, "usage": usage},
        "industry_indicator", output_dir=output_dir)


def industry_indicator_data(
    keyword: Optional[str] = None,
    indicator_ids: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = SEARCH_DEFAULT_LIMIT,
    output_dir: Optional[str] = None,
):
    """检索行业指标元信息，或在提供 indicator_ids 时拉取时序数据。"""
    ids_raw = (indicator_ids or "").strip()
    if ids_raw:
        if not get_authorization_token():
            return format_response(
                {
                    "state": "error",
                    "message": "未配置 gangtise 授权，无法调用 open 接口",
                    "data": [],
                    "usage": {},
                },
                "industry_indicator", output_dir=output_dir)
        headers = get_authorization_headers()
        resolved, search_md, err = _resolve_indicators_from_arg(headers, ids_raw, limit)
        if err:
            return format_response(
                {"state": "error", "message": err, "data": [], "usage": {}},
                "industry_indicator", output_dir=output_dir)
        if search_md:
            return search_md
        return industry_indicator_get(
            indicator_ids=resolved or [],
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            output_dir=output_dir,
        )

    kw = (keyword or "").strip()
    if kw:
        return industry_indicator_search(keyword=kw, limit=limit, append_file_hint=True, output_dir=output_dir)

    return format_response(
        {
            "state": "error",
            "message": "请提供 keyword 检索指标，或 indicator_ids 拉取时序数据",
            "data": [],
            "usage": {},
        },
        "industry_indicator", output_dir=output_dir)


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
        description="行业指标（EDB）：检索指标元信息或拉取时序数据",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    today_str = date.today().strftime("%Y-%m-%d")
    default_start = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")

    parser.add_argument(
        "-m",
        "--mode",
        choices=["search", "get"],
        default="search",
        help="search=检索指标元信息；get=拉取时序；提供 --indicators 时自动为 get",
    )
    parser.add_argument("-k", "--keyword", default="", help="检索关键词（search 模式）")
    parser.add_argument(
        "--indicators",
        default=None,
        help="指标 ID 或名称，逗号分隔；纯字母数字视为 ID 直接 get，否则 search，唯一完全匹配时自动 get",
    )
    parser.add_argument(
        "--indicators-file",
        default=None,
        help="CSV 含 indicator_id / indicatorId 列（get 模式）",
    )
    parser.add_argument(
        "-sd",
        "--start-date",
        default=None,
        help=f"时序开始日期 yyyy-MM-dd（get），默认 {default_start}",
    )
    parser.add_argument(
        "-ed",
        "--end-date",
        default=None,
        help=f"时序结束日期 yyyy-MM-dd（get），默认 {today_str}",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=SEARCH_DEFAULT_LIMIT,
        help=f"search：检索条数上限（最大 {SEARCH_MAX_LIMIT}）；get：指标数量上限（可超 {GET_BATCH_SIZE}，自动分批）",
    )

    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="结果保存目录路径，建议使用绝对路径",
    )

    args = parser.parse_args()
    limit = int(args.limit)

    indicator_ids_raw = (args.indicators or "").strip() or None
    if args.indicators_file:
        try:
            loaded = _load_indicators_from_file(args.indicators_file)
            indicator_ids_raw = ",".join(loaded)
        except Exception as e:
            print(f"读取指标文件失败: {e}")
            sys.exit(1)

    if indicator_ids_raw:
        print(
            industry_indicator_data(
                indicator_ids=indicator_ids_raw,
                start_date=args.start_date,
                end_date=args.end_date,
                limit=limit,
                output_dir=args.output_dir or None,
            )
        )
        return

    kw = (args.keyword or "").strip()
    if kw:
        print(
            industry_indicator_data(
                keyword=kw,
                limit=limit,
                output_dir=args.output_dir or None,
            )
        )
        return

    parser.error("请提供 -k/--keyword 检索，或 --indicators/--indicators-file 拉取数据")


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
