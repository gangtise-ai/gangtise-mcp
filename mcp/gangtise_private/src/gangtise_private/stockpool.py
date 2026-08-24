import os
import sys
from io import TextIOWrapper
from typing import List, Optional, Tuple

import pandas as pd
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (GET_POOL_LIST_URL, GET_STOCK_LIST_URL, authorized_request, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, is_code_arg, load_pool_ids_from_file)

DEFAULT_POOL_LIMIT = 100
IDS_FILE_HINT = "可以删除行或保留需要的池，再通过 --pool-file 参数读取文件获取证券"


def _parse_str_list(raw: str) -> List[str]:
    if not raw or not str(raw).strip():
        return []
    return [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]


def _api_error_message(body: dict, fallback: str = "") -> str:
    if isinstance(body, dict):
        return str(body.get("msg") or body.get("message") or fallback)
    return fallback


def _fetch_pool_list(headers: dict) -> Tuple[List[dict], Optional[str]]:
    try:
        r = authorized_request("POST", GET_POOL_LIST_URL, headers=headers, json={}, timeout=120)
        if r.status_code != 200:
            return [], f"股票池列表 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"股票池列表请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"股票池列表接口错误: {_api_error_message(body)}"

    data = body.get("data") or []
    if not isinstance(data, list):
        return [], "股票池列表返回 data 格式异常"
    return data, None


def _fetch_stock_list(headers: dict, pool_id_list: List[str]) -> Tuple[List[dict], Optional[str]]:
    ids = [str(x).strip() for x in pool_id_list if str(x).strip()]
    if not ids:
        return [], "股票池 ID 列表为空"

    payload = {"poolIdList": ids}
    try:
        r = authorized_request("POST", GET_STOCK_LIST_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return [], f"证券明细 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"证券明细请求失败: {e}"

    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return [], f"证券明细接口错误: {_api_error_message(body)}"

    data = body.get("data") or []
    if not isinstance(data, list):
        return [], "证券明细返回 data 格式异常"
    return data, None


def _pools_to_df(pools: List[dict]) -> pd.DataFrame:
    records = []
    for item in pools:
        records.append(
            {
                "pool_id": item.get("poolId"),
                "pool_name": item.get("poolName"),
            }
        )
    return pd.DataFrame(records)


def _stocks_to_df(stocks: List[dict]) -> pd.DataFrame:
    records = []
    for item in stocks:
        records.append(
            {
                "security_code": item.get("securityCode"),
                "security_abbr": item.get("securityName"),
            }
        )
    df = pd.DataFrame(records)
    if not df.empty and "security_code" in df.columns:
        df = df.drop_duplicates(subset=["security_code"], keep="first")
    return df


def _filter_pools_by_keyword(pools: List[dict], keyword: str) -> List[dict]:
    kw = (keyword or "").strip()
    if not kw:
        return pools
    return [
        p
        for p in pools
        if kw in str(p.get("poolName") or "") or kw in str(p.get("poolId") or "")
    ]


def _apply_pool_limit(pools: List[dict], limit: Optional[int]) -> List[dict]:
    if limit is None or limit <= 0:
        return pools
    return pools[: int(limit)]


def _exact_match_pools(keyword: str, pools: List[dict]) -> List[dict]:
    kw = keyword.strip()
    return [
        p
        for p in pools
        if str(p.get("poolName") or "").strip() == kw
        or str(p.get("poolId") or "").strip() == kw
    ]


def _resolve_pools_from_arg(raw: str, limit: int) -> Tuple[Optional[List[str]], Optional[str]]:
    if is_code_arg(raw):
        return _parse_str_list(raw), None

    if not get_authorization_token():
        return None, "未配置 gangtise 授权，无法调用 open 接口"

    headers = get_authorization_headers()
    pools, err = _fetch_pool_list(headers)
    if err:
        return None, err

    resolved: List[str] = []
    search_outputs: List[str] = []
    for token in _parse_str_list(raw):
        filtered = _filter_pools_by_keyword(pools, token)
        exact = _exact_match_pools(token, filtered)
        if len(exact) == 1:
            pid = str(exact[0].get("poolId") or "").strip()
            if pid:
                resolved.append(pid)
            continue
        if not filtered:
            return None, f"未找到与「{token}」相关的股票池"
        if len(exact) > 1:
            prefix = f"「{token}」存在 {len(exact)} 条完全匹配，请指定更精确的名称或 ID：\n\n"
        else:
            prefix = f"未找到与「{token}」完全匹配的股票池，以下为候选结果：\n\n"
        search_outputs.append(prefix + stockpool_search(keyword=token, limit=limit))

    if search_outputs:
        return None, "\n\n".join(search_outputs)
    return resolved, None


def stockpool_search(
    keyword: str = "",
    limit: int = DEFAULT_POOL_LIMIT,
    append_file_hint: bool = False,
    output_dir: Optional[str] = None,
    **kwargs,
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
            "stockpool", output_dir=output_dir)

    headers = get_authorization_headers()
    pools, err = _fetch_pool_list(headers)
    if err:
        return format_response(
            {"state": "error", "message": err, "data": [], "usage": usage},
            "stockpool", output_dir=output_dir)
    if not pools:
        return format_response(
            {
                "state": "error",
                "message": "当前账号下无自选股股票池",
                "data": [],
                "usage": usage,
            },
            "stockpool", output_dir=output_dir)

    pools = _filter_pools_by_keyword(pools, keyword)
    pools = _apply_pool_limit(pools, limit)
    if not pools:
        kw_hint = f"与「{keyword.strip()}」匹配的" if (keyword or "").strip() else ""
        return format_response(
            {
                "state": "error",
                "message": f"未找到{kw_hint}股票池",
                "data": [],
                "usage": usage,
            },
            "stockpool", output_dir=output_dir)

    df = _pools_to_df(pools)
    parts = [
        {
            "data": df.to_dict(orient="records"),
            "module": "stockpool_pools",
            "type": "data",
        }
    ]
    msg = f"已获取 {len(df)} 个自选股股票池"
    if (keyword or "").strip():
        msg += f"（关键词：{keyword.strip()}）"
    if append_file_hint:
        msg += f"；{IDS_FILE_HINT}"
    return format_response(
        {"state": "success", "message": msg, "data": parts, "usage": usage},
        "stockpool", output_dir=output_dir)


def stockpool_get(
    pool_ids: List[str],
    all_pools: bool = False,
    output_dir: Optional[str] = None,
    **kwargs,
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
            "stockpool", output_dir=output_dir)

    if all_pools:
        pool_id_list = ["all"]
    else:
        pool_id_list = [str(x).strip() for x in pool_ids if str(x).strip()]
        if not pool_id_list:
            return format_response(
                {
                    "state": "error",
                    "message": "股票池 ID 列表不能为空（或使用 --all 获取全部池去重证券）",
                    "data": [],
                    "usage": usage,
                },
                "stockpool", output_dir=output_dir)

    headers = get_authorization_headers()
    stocks, err = _fetch_stock_list(headers, pool_id_list)
    if err:
        return format_response(
            {"state": "error", "message": err, "data": [], "usage": usage},
            "stockpool", output_dir=output_dir)
    if not stocks:
        return format_response(
            {
                "state": "error",
                "message": "股票池中无证券或 ID 无效",
                "data": [],
                "usage": usage,
            },
            "stockpool", output_dir=output_dir)

    df = _stocks_to_df(stocks)
    parts = [
        {
            "data": df.to_dict(orient="records"),
            "module": "stockpool_stocks",
            "type": "data",
        }
    ]
    if all_pools:
        msg = f"已获取全部自选股股票池去重后的 {len(df)} 只证券"
    else:
        msg = f"已获取 {len(pool_id_list)} 个股票池共 {len(df)} 只证券（已按代码去重）"
    return format_response(
        {"state": "success", "message": msg, "data": parts, "usage": usage},
        "stockpool", output_dir=output_dir)


def stockpool_finder(
    keyword: str = "",
    pool_ids: Optional[str] = None,
    all_pools: bool = False,
    limit: int = DEFAULT_POOL_LIMIT,
    output_dir: Optional[str] = None,
):
    """检索自选股池列表，或在提供 pool_ids / all_pools 时获取池内成分股。"""
    if all_pools:
        return stockpool_get(pool_ids=[], all_pools=True, output_dir=output_dir)
    if pool_ids and str(pool_ids).strip():
        resolved, err = _resolve_pools_from_arg(str(pool_ids).strip(), limit)
        if err:
            return err
        ids = resolved or []
        if ids:
            return stockpool_get(pool_ids=ids, all_pools=False, output_dir=output_dir)
    return stockpool_search(keyword=keyword, limit=limit, append_file_hint=True, output_dir=output_dir)


def main():
    import argparse

    try:
        if not check_version():
            update_sh = os.path.join(script_dir, "update.sh")
            print(
                f"[WARNING] 存在 Gangtise private 版本更新，可以执行 {update_sh} 更新，请与用户确认是否更新\n"
            )
    except Exception:
        print("[WARNING] 检查 Gangtise private 版本失败\n")

    parser = argparse.ArgumentParser(
        description="自选股股票池：检索股票池列表或按池 ID 取证券明细",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["search", "get"],
        default="search",
        help="search=列股票池；get=按池 ID 取证券；提供 --pool/--pool-file/--all 时自动为 get",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        default="",
        help="股票池名称/ID 关键词过滤（search 模式，可选）",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=DEFAULT_POOL_LIMIT,
        help="search 模式下列出/过滤的股票池数量上限",
    )
    parser.add_argument(
        "--pool",
        default="",
        help="股票池 ID 或名称，逗号分隔；纯字母数字视为 ID 直接 get，否则 search，唯一完全匹配时自动 get",
    )
    parser.add_argument(
        "--pool-file",
        default=None,
        help="从 csv 读取 pool_id / poolId 列（get 模式）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help='get 模式传 poolIdList=["all"]，返回全部池去重证券',
    )
    parser.add_argument(
        "-od",
        "--output-dir",
        default=None,
        help="结果保存目录路径，建议使用绝对路径",
    )

    args = parser.parse_args()
    pool_arg = args.pool
    if args.pool_file:
        try:
            pool_arg = ",".join(load_pool_ids_from_file(args.pool_file))
        except Exception as e:
            print(f"读取股票池文件失败: {e}")
            sys.exit(1)

    print(
        stockpool_finder(
            keyword=(args.keyword or "").strip(),
            pool_ids=pool_arg,
            all_pools=bool(args.all),
            limit=int(args.limit),
            output_dir=args.output_dir or None,
        )
    )


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
