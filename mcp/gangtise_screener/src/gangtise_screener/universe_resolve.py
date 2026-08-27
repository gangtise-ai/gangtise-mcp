#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股范围解析（自包含，不依赖 gangtise-data）

流程：universe_strip 剥壳 → 板块搜索 →（必要时）证券搜索 → sectorId / gtsCode。
亦支持 -u 传入 CSV 路径，读取 code / security_code（或「代码」）列作为证券范围。
"""
import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .universe_strip import strip_universe
from .utils import (HTTP_TIMEOUT, SECTOR_SEARCH_URL, SECURITIES_SEARCH_URL, authorized_request, get_authorization_headers, get_authorization_token, get_headers_extra)

MATCH_SCORE_THRESHOLD = 0.6
# 接口 matchScore 常虚高（如「工业软件」→「AI应用」满分）；本地再卡字面相关度
NAME_RELEVANCE_MIN = 0.5
NAME_RELEVANCE_AUTO = 0.7
SEARCH_TOP = 10
_EXCLUDED_HIERARCHY_MARKERS = ("指数成份类", "指数成分类")
_GTS_CODE_RE = re.compile(
    r"^[0-9A-Za-z]+\.(SH|SZ|BJ|HK|O|N|A|US|SWI|CI|GT)$", re.IGNORECASE
)
_SECTOR_ID_RE = re.compile(r"^\d{10}$")


def _headers() -> dict:
    h = get_authorization_headers()
    h["Content-Type"] = "application/json"
    return h


def _match_score(item: dict) -> float:
    try:
        return float(item.get("matchScore") or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm_text(s: Any) -> str:
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or ""))
    t = re.sub(r"[\s　]+", "", t)
    return t.lower()


def _char_bigrams(s: str) -> set:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def textual_relevance(keyword: str, name: str, hierarchy: str = "") -> float:
    """关键词与板块名/路径的字面相关度 ∈ [0,1]。用于过滤接口虚高 matchScore。"""
    kw = _norm_text(keyword)
    nm = _norm_text(name)
    if not kw:
        return 0.0
    if kw == nm:
        return 1.0
    if kw in nm or (len(nm) >= 2 and nm in kw):
        return 0.95
    hier = _norm_text(hierarchy)
    if kw in hier:
        return 0.85
    kb = _char_bigrams(kw)
    if not kb:
        return 0.0
    scores = []
    nb = _char_bigrams(nm)
    if nb:
        scores.append(len(kb & nb) / len(kb))
    leaf = _norm_text((hierarchy or "").split("-")[-1])
    if leaf:
        lb = _char_bigrams(leaf)
        if lb:
            scores.append(len(kb & lb) / len(kb))
    return max(scores) if scores else 0.0


def _is_excluded_sector(item: dict) -> bool:
    hierarchy = str(item.get("hierarchy") or "")
    return any(m in hierarchy for m in _EXCLUDED_HIERARCHY_MARKERS)


def search_sectors(keyword: str, top: int = SEARCH_TOP) -> Tuple[List[dict], Optional[str]]:
    payload = {"keyword": keyword.strip(), "top": max(1, min(int(top), 10))}
    try:
        r = authorized_request("POST", SECTOR_SEARCH_URL, headers=_headers(), json=payload, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return [], f"板块检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"板块检索请求失败: {e}"
    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        msg = body.get("msg") or body.get("message") or "接口返回失败"
        return [], f"板块检索接口错误: {msg}"
    items = (body.get("data") or {}).get("list") or []
    if not isinstance(items, list):
        return [], "板块检索返回 list 格式异常"
    return items, None


def search_securities(
    keyword: str,
    top: int = SEARCH_TOP,
    category: Optional[List[str]] = None,
) -> Tuple[List[dict], Optional[str]]:
    payload: Dict[str, Any] = {"keyword": keyword.strip(), "top": max(1, min(int(top), 10))}
    if category:
        payload["category"] = category
    try:
        r = authorized_request("POST", 
            SECURITIES_SEARCH_URL, headers=_headers(), json=payload, timeout=HTTP_TIMEOUT
        )
        if r.status_code != 200:
            return [], f"证券检索 HTTP {r.status_code}: {r.text[:500]}"
        body = r.json()
    except Exception as e:
        return [], f"证券检索请求失败: {e}"
    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        msg = body.get("msg") or body.get("message") or "接口返回失败"
        return [], f"证券检索接口错误: {msg}"
    items = (body.get("data") or {}).get("list") or []
    if not isinstance(items, list):
        return [], "证券检索返回 list 格式异常"
    return items, None


def _rank_sectors(
    items: List[dict],
    keyword: str,
    system: Optional[str] = None,
    market: Optional[str] = None,
) -> List[dict]:
    scored = []
    for it in items:
        if _is_excluded_sector(it):
            continue
        score = _match_score(it)
        if score < MATCH_SCORE_THRESHOLD:
            continue
        name = str(it.get("sectorName") or "")
        hierarchy = str(it.get("hierarchy") or "")
        rel = textual_relevance(keyword, name, hierarchy)
        if rel < NAME_RELEVANCE_MIN:
            continue
        bonus = 0.0
        if system and system in hierarchy:
            bonus += 0.15
        elif not system and "中信行业类" in hierarchy:
            bonus += 0.08
        if market and market in hierarchy:
            bonus += 0.1
        scored.append({
            **it,
            "_relevance": rel,
            "_rank": score * 0.55 + rel * 0.45 + bonus,
        })
    scored.sort(key=lambda x: (-x["_rank"], -x.get("_relevance", 0)))
    return scored


def _pick_sector(
    candidates: List[dict],
    system: Optional[str] = None,
) -> Tuple[Optional[dict], str]:
    """在强候选中择优：有 system 提示则对齐 hierarchy；否则默认中信行业类。

    唯一候选也须字面相关度 ≥ NAME_RELEVANCE_AUTO，否则 ambiguous 请用户确认。
    """
    if not candidates:
        return None, "not_found"

    pool = list(candidates)
    if system:
        matched = [c for c in pool if system in str(c.get("hierarchy") or "")]
        if matched:
            pool = matched
    else:
        citic = [c for c in pool if "中信行业类" in str(c.get("hierarchy") or "")]
        if citic:
            pool = citic

    pool.sort(key=lambda x: -x.get("_rank", _match_score(x)))

    def _auto_ok(c: dict) -> bool:
        return float(c.get("_relevance") or 0) >= NAME_RELEVANCE_AUTO

    if len(pool) == 1:
        return (pool[0], "ok") if _auto_ok(pool[0]) else (None, "ambiguous")
    perfect = [c for c in pool if _match_score(c) >= 0.999 and _auto_ok(c)]
    if len(perfect) == 1:
        return perfect[0], "ok"
    if len(pool) >= 2 and pool[0].get("_rank", 0) - pool[1].get("_rank", 0) >= 0.08 and _auto_ok(pool[0]):
        return pool[0], "ok"
    if len(pool) >= 2 and _match_score(pool[0]) - _match_score(pool[1]) >= 0.15 and _auto_ok(pool[0]):
        return pool[0], "ok"
    return None, "ambiguous"


def _stock_candidates(items: List[dict]) -> List[dict]:
    out = []
    for it in items:
        code = str(it.get("gtsCode") or "").strip().upper()
        cat = str(it.get("category") or "").strip().lower()
        if cat and cat not in ("stock", "dr"):
            continue
        # 排除明显指数形态
        if code.endswith((".SWI", ".CI", ".GT")):
            continue
        if re.match(r"^00\d{4}\.SH$", code) or re.match(r"^399\d{3}\.SZ$", code):
            continue
        out.append(it)
    out.sort(key=_match_score, reverse=True)
    return out


def _pick_security(candidates: List[dict]) -> Tuple[Optional[dict], str]:
    if not candidates:
        return None, "not_found"
    if len(candidates) == 1 or _match_score(candidates[0]) >= 0.999:
        return candidates[0], "ok"
    if len(candidates) >= 2 and _match_score(candidates[0]) - _match_score(candidates[1]) >= 0.15:
        return candidates[0], "ok"
    return None, "ambiguous"


def _looks_like_id(token: str) -> Optional[str]:
    t = token.strip()
    if _SECTOR_ID_RE.match(t):
        return t
    if _GTS_CODE_RE.match(t):
        return t.upper()
    return None


def resolve_one_universe(phrase: str) -> dict:
    """解析单条范围说法，返回结构化结果。"""
    raw = (phrase or "").strip()
    if not raw:
        return {"input": phrase, "status": "error", "error": "universe 不能为空", "universe": []}

    direct = _looks_like_id(raw)
    if direct:
        return {
            "input": phrase,
            "status": "ok",
            "kind": "id",
            "universe": [direct],
            "resolved": {"id": direct, "name": direct},
            "strip": None,
            "candidates": [],
        }

    if not get_authorization_token():
        return {
            "input": phrase,
            "status": "error",
            "error": "未配置 gangtise 授权",
            "universe": [],
        }

    stripped = strip_universe(raw)
    if stripped["status"] == "index_unsupported":
        return {
            "input": phrase,
            "status": "index_unsupported",
            "note": stripped.get("note"),
            "strip": stripped,
            "universe": [],
            "candidates": [],
        }
    if stripped["status"] == "alias" and stripped.get("alias"):
        alias = stripped["alias"]
        sid = str(alias.get("sectorId") or alias.get("id") or "").strip()
        name = str(alias.get("name") or alias.get("sectorName") or sid)
        if sid:
            return {
                "input": phrase,
                "status": "ok",
                "kind": "alias",
                "universe": [sid],
                "resolved": {"id": sid, "name": name, "hierarchy": alias.get("hierarchy")},
                "strip": stripped,
                "candidates": [],
            }

    keywords = list(stripped.get("keywords") or [])
    for rw in stripped.get("rewrite") or []:
        if rw not in keywords:
            keywords.append(rw)
    system = stripped.get("system")
    market = stripped.get("market")

    sector_cands: List[dict] = []
    rejected: List[dict] = []
    tried = []
    for kw in keywords[:6]:
        items, err = search_sectors(kw)
        tried.append({"keyword": kw, "n": len(items), "error": err})
        if err:
            continue
        for it in items:
            if _is_excluded_sector(it):
                continue
            name = str(it.get("sectorName") or "")
            hierarchy = str(it.get("hierarchy") or "")
            rel = textual_relevance(kw, name, hierarchy)
            if rel < NAME_RELEVANCE_MIN:
                rejected.append({
                    "sectorId": it.get("sectorId"),
                    "sectorName": name,
                    "hierarchy": hierarchy,
                    "matchScore": _match_score(it),
                    "relevance": round(rel, 3),
                    "rejected_by": "low_name_relevance",
                    "keyword": kw,
                })
        ranked = _rank_sectors(items, keyword=kw, system=system, market=market)
        for c in ranked:
            sid = str(c.get("sectorId") or "")
            if sid and not any(x.get("sectorId") == sid for x in sector_cands):
                sector_cands.append(c)

    sector_cands.sort(key=lambda x: -x.get("_rank", _match_score(x)))
    picked, st = _pick_sector(sector_cands, system=system)

    def _cand_rows(cands: List[dict]) -> List[dict]:
        out = []
        for c in cands[:8]:
            rel_v = c.get("_relevance")
            if rel_v is None:
                rel_v = textual_relevance(
                    keywords[0] if keywords else "",
                    str(c.get("sectorName") or ""),
                    str(c.get("hierarchy") or ""),
                )
            out.append({
                "sectorId": c.get("sectorId"),
                "sectorName": c.get("sectorName"),
                "hierarchy": c.get("hierarchy"),
                "matchScore": _match_score(c),
                "relevance": round(float(rel_v), 3),
            })
        return out

    if st == "ok" and picked:
        sid = str(picked.get("sectorId") or "").strip()
        return {
            "input": phrase,
            "status": "ok",
            "kind": "sector",
            "universe": [sid],
            "resolved": {
                "id": sid,
                "name": picked.get("sectorName"),
                "hierarchy": picked.get("hierarchy"),
                "matchScore": _match_score(picked),
                "relevance": round(float(picked.get("_relevance") or 0), 3),
            },
            "strip": stripped,
            "candidates": _cand_rows(sector_cands),
            "search": tried,
        }
    if st == "ambiguous":
        return {
            "input": phrase,
            "status": "ambiguous",
            "kind": "sector",
            "universe": [],
            "strip": stripped,
            "candidates": _cand_rows(sector_cands),
            "search": tried,
            "note": "板块候选不唯一或字面相关度不足，请指定更精确的板块名或直接传 sectorId",
        }

    # 板块无字面相关命中：不要落到证券兜底（「工业软件」不是股票名）
    # 仅当关键词很像个股（短、或已是代码形态）才走证券
    look_like_security = (
        len(keywords[0]) <= 4
        and not any(x in (keywords[0] or "") for x in ("板块", "概念", "行业", "软件", "制造", "产业"))
    ) if keywords else False

    if not look_like_security and (rejected or tried):
        return {
            "input": phrase,
            "status": "not_found",
            "universe": [],
            "strip": stripped,
            "candidates": [],
            "rejected": rejected[:8],
            "search": tried,
            "note": (
                "未找到与关键词字面相关的板块"
                + ("（已忽略接口虚高匹配）" if rejected else "")
                + "。可换更接近的板块正式名，或直接传 sectorId"
            ),
        }

    # 板块未命中且像个股名 → 尝试证券
    sec_cands: List[dict] = []
    sec_tried = []
    for kw in keywords[:4]:
        items, err = search_securities(kw, category=["stock", "dr"])
        sec_tried.append({"keyword": kw, "n": len(items), "error": err})
        if err:
            continue
        for c in _stock_candidates(items):
            code = str(c.get("gtsCode") or "").strip().upper()
            if code and not any(
                str(x.get("gtsCode") or "").upper() == code for x in sec_cands
            ):
                sec_cands.append(c)
    sec_cands.sort(key=_match_score, reverse=True)
    sec_picked, sec_st = _pick_security(sec_cands)
    if sec_st == "ok" and sec_picked:
        code = str(sec_picked.get("gtsCode") or "").strip().upper()
        return {
            "input": phrase,
            "status": "ok",
            "kind": "security",
            "universe": [code],
            "resolved": {
                "id": code,
                "name": sec_picked.get("gtsName"),
                "matchScore": _match_score(sec_picked),
            },
            "strip": stripped,
            "candidates": [
                {
                    "gtsCode": c.get("gtsCode"),
                    "gtsName": c.get("gtsName"),
                    "matchScore": _match_score(c),
                }
                for c in sec_cands[:5]
            ],
            "search": {"sector": tried, "security": sec_tried},
        }
    if sec_st == "ambiguous":
        return {
            "input": phrase,
            "status": "ambiguous",
            "kind": "security",
            "universe": [],
            "strip": stripped,
            "candidates": [
                {
                    "gtsCode": c.get("gtsCode"),
                    "gtsName": c.get("gtsName"),
                    "matchScore": _match_score(c),
                }
                for c in sec_cands[:5]
            ],
            "note": "证券候选不唯一，请指定代码或更精确名称",
            "search": {"sector": tried, "security": sec_tried},
        }

    return {
        "input": phrase,
        "status": "not_found",
        "universe": [],
        "strip": stripped,
        "candidates": [],
        "search": {"sector": tried, "security": sec_tried},
        "note": "未找到匹配板块或证券",
    }


# CSV 证券代码列（规范化后匹配；优先顺序从前到后）
_CSV_CODE_COLUMNS = ("security_code", "code", "代码")


def _norm_csv_header(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", (name or "").strip().lower())


def _is_csv_universe_path(token: str) -> bool:
    """是否按 CSV 范围文件处理（以 .csv 结尾）。"""
    t = (token or "").strip().strip('"').strip("'")
    return bool(t) and t.lower().endswith(".csv")


def load_codes_from_csv(path: str) -> Tuple[List[str], Optional[str]]:
    """从 CSV 读取证券代码列。

    识别列名（大小写/下划线不敏感）：``security_code``、``code``，
    以及本技能结果落盘常用的「代码」。
    """
    raw = (path or "").strip().strip('"').strip("'")
    raw = os.path.expanduser(raw)
    if not raw:
        return [], "CSV 路径为空"
    if not os.path.isfile(raw):
        return [], f"CSV 文件不存在: {raw}"

    try:
        with open(raw, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return [], f"CSV 无表头: {raw}"
            col_map = {_norm_csv_header(h): h for h in reader.fieldnames if h}
            col_name = None
            for pref in _CSV_CODE_COLUMNS:
                hit = col_map.get(_norm_csv_header(pref))
                if hit:
                    col_name = hit
                    break
            if not col_name:
                return [], (
                    f"CSV 未找到证券代码列（需含 code / security_code / 代码 之一）: {raw}；"
                    f"实际列={list(reader.fieldnames)}"
                )
            codes: List[str] = []
            seen = set()
            for row in reader:
                val = str((row or {}).get(col_name) or "").strip()
                if not val or val in seen:
                    continue
                seen.add(val)
                codes.append(val)
    except OSError as e:
        return [], f"读取 CSV 失败: {raw} ({e})"
    except csv.Error as e:
        return [], f"解析 CSV 失败: {raw} ({e})"

    if not codes:
        return [], f"CSV 代码列为空: {raw}（列={col_name}）"
    return codes, None


def resolve_universe(phrases: List[str]) -> dict:
    """解析多条范围（逗号分隔也可；``.csv`` 路径读 code/security_code 列），取并集。"""
    tokens: List[str] = []
    csv_sources: List[dict] = []
    for p in phrases:
        for part in re.split(r"[,，]", p or ""):
            part = part.strip()
            if not part:
                continue
            if _is_csv_universe_path(part):
                codes, err = load_codes_from_csv(part)
                if err:
                    failed = {
                        "input": part,
                        "status": "error",
                        "error": err,
                        "universe": [],
                        "kind": "csv",
                    }
                    return {
                        "status": "error",
                        "error": err,
                        "universe": [],
                        "details": [failed],
                        "failed": failed,
                    }
                csv_sources.append({"path": os.path.abspath(os.path.expanduser(part.strip().strip('"').strip("'"))), "count": len(codes)})
                tokens.extend(codes)
            else:
                tokens.append(part)
    if not tokens:
        return {"status": "error", "error": "universe 不能为空", "universe": [], "details": []}

    details = [resolve_one_universe(t) for t in tokens]
    ids: List[str] = []
    for d in details:
        if d.get("status") != "ok":
            return {
                "status": d["status"],
                "error": d.get("error") or d.get("note") or f"范围「{d.get('input')}」解析失败",
                "universe": [],
                "details": details,
                "failed": d,
                **({"csv_sources": csv_sources} if csv_sources else {}),
            }
        for u in d.get("universe") or []:
            if u not in ids:
                ids.append(u)
    out: Dict[str, Any] = {"status": "ok", "universe": ids, "details": details}
    if csv_sources:
        out["csv_sources"] = csv_sources
    return out


def main():
    ap = argparse.ArgumentParser(description="解析选股范围：剥壳 + 板块/证券搜索 / CSV 代码列")
    ap.add_argument(
        "phrase",
        nargs="+",
        help="板块/市场/证券说法、sectorId/证券代码，或含 code/security_code 列的 CSV 路径",
    )
    args = ap.parse_args()
    print(json.dumps(resolve_universe(args.phrase), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
