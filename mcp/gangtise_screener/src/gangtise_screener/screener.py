#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gangtise-screener 解析引擎

职责：把口语化的指标名 / 板块名 / 筛选条件变成一条可执行的
指标选股 HTTP 请求。指标与板块的真值一律来自在线接口（indicator search /
sector-search / securities-search），本地只保留口语→检索词的改写提示表。

子命令
  resolve-indicator <指标说法>   → 排序后的候选指标（含参数元数据、status）
  resolve-universe  <范围说法>   → 剥壳 + 板块/证券搜索 → sectorId / gtsCode
  run  -u … -i … -e …            → 自动串联：解析范围/指标 → 补参数 → 选股（推荐）
  run  <payload.json|->          → 校验已构造好的 payload 并执行
"""
import argparse
import difflib
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .universe_resolve import resolve_universe
from .utils import (HTTP_TIMEOUT, INDICATOR_STOCK_URL, INDICATOR_URL, QUOTE_URL, authorized_request, get_authorization_headers, get_authorization_token, get_headers_extra, save_data_csv)


HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")


def load_asset(name, default):
    p = os.path.join(ASSETS, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


import datetime
TODAY = datetime.date.today().isoformat()

HINTS = load_asset("hints.json", {})
REWRITE = HINTS.get("indicator_rewrite", {})
NOT_AVAILABLE = HINTS.get("indicator_unavailable", {})

# ---------------------------------------------------------------- 基础工具


def norm(s):
    """全角→半角、去空格、统一括号、小写"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = (s.replace("（", "(").replace("）", ")").replace("：", ":")
          .replace("，", ",").replace("、", ",").replace("％", "%"))
    s = re.sub(r"[\s　]+", "", s)
    return s.lower()


def cn2num(s):
    """把「十二/12」这类混写统一成阿拉伯数字，只处理 1-99 的小数字"""
    d = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
         "六": 6, "七": 7, "八": 8, "九": 9}

    def rep(m):
        t = m.group(0)
        if t == "十":
            return "10"
        if t.startswith("十"):
            return str(10 + d.get(t[1], 0))
        if t.endswith("十"):
            return str(d.get(t[0], 0) * 10)
        if "十" in t:
            a, b = t.split("十")
            return str(d.get(a, 0) * 10 + d.get(b, 0))
        return str(d.get(t, t))

    return re.sub(r"[零一二两三四五六七八九]?十[零一二两三四五六七八九]?|[零一二两三四五六七八九]", rep, s)


def _alt(words):
    """把候选词按长度倒序拼成正则备选，保证「要求」优先于「要」"""
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))



def latest_report_end(ref=None, annual_only=True):
    """最近一个「披露期已过」的报告期末。

    **默认取年报**：选股里的比率与增速（ROE / 毛利率 / 营收同比…）默认口径就是年度，
    落到季报上会把「ROE>15%」这类条件筛成空集（季度 ROE 天然远小于年度）。
    需要季度口径时显式传 report_date。
    """
    ref = ref or TODAY
    d = datetime.date.fromisoformat(ref)
    ends = []
    for y in (d.year, d.year - 1, d.year - 2):
        for md in (((12, 31),) if annual_only else ((12, 31), (9, 30), (6, 30), (3, 31))):
            ends.append(datetime.date(y, *md))
    deadline = {3: 4, 6: 5, 9: 4, 12: 5}   # 报告期末 → 到「安全可用」需要的月数
    for e in sorted(ends, reverse=True):
        safe = e + datetime.timedelta(days=int(deadline[e.month] * 30.5))
        if safe <= d:
            return e.isoformat()
    return f"{d.year - 2}-12-31"


REPORT_PHRASE = [
    (r"(20\d{2})\s*年?\s*(?:年报|年度报告|全年)", 12, 31),
    (r"(20\d{2})\s*年?\s*(?:中报|半年报|半年度报告|h1|上半年)", 6, 30),
    (r"(20\d{2})\s*年?\s*(?:三季报|3季报|q3)", 9, 30),
    (r"(20\d{2})\s*年?\s*(?:一季报|1季报|q1|首季)", 3, 31),
]


# 报告期词（年报/中报/季报…）是财报日期口径，不是行情周期：
# 必须先于口径轴摘掉，否则「年报XX」会被 freq 轴的「年」吃掉、裸名变成「报XX」
REPORT_WORD = re.compile(
    r"20\d{2}\s*年?\s*(?:年报|年度报告|中报|半年报|三季报|一季报|q[134]|h1)"
    r"|(?:最新|最近)?(?:年报|年度报告|中报|半年报|三季报|一季报|季报)")


def parse_report_date(text):
    """从提问里抽报告期：「2025年报」→2025-12-31；「最新报告期」→最近已披露的任一报告期"""
    t = norm(text)
    for pat, m, dd in REPORT_PHRASE:
        g = re.search(pat, t)
        if g:
            return f"{g.group(1)}-{m:02d}-{dd:02d}"
    for pat, m, dd in ((r"年报|年度报告|全年", 12, 31), (r"中报|半年报|上半年", 6, 30),
                       (r"三季报|q3", 9, 30), (r"一季报|q1|首季", 3, 31)):
        if re.search(pat, t):
            # 没写年份时，取该类报告里「披露期已过」的最近一期
            today = datetime.date.fromisoformat(TODAY)
            need = {3: 4, 6: 5, 9: 4, 12: 5}[m]
            for y in (today.year, today.year - 1, today.year - 2):
                e = datetime.date(y, m, dd)
                if e + datetime.timedelta(days=int(need * 30.5)) <= today:
                    return e.isoformat()
    if re.search(r"最新报告期|最近一期|最新一期|最新财报", t):
        return latest_report_end(annual_only=False)
    return None


# ------------------------------------------------------- 口径（caliber）识别

# 用户口语侧：词 → (轴, 值)。顺序敏感，长词在前。
USER_CALIBER = [
    # 期间
    (r"ttm|滚动12个月|滚动十二个月|近12个月|近十二个月|过去12个月|最近四个季度|滚动", ("period", "ttm")),
    (r"单季度|单季|当季|季度单|本季", ("period", "qtr")),
    (r"累计|年初至今", ("period", "cum")),
    (r"mrq|最新报告期|最近一期", ("period", "mrq")),
    # 增长
    (r"同比增长|同比增速|同比|yoy|增长率|增速|增长", ("growth", "yoy")),
    (r"环比增长|环比增速|环比|qoq|mom", ("growth", "mom")),
    # N 期统计
    (r"n期均值|多期均值|近n期均值|均值|平均值", ("stat", "avg")),
    (r"n期最小值|最小值|最低值", ("stat", "min")),
    (r"n期最大值|最大值|最高值", ("stat", "max")),
    # 预测
    (r"预测|一致预期|预期|wind一致|万得一致", ("fcst", "yes")),
    # 行情周期
    (r"区间", ("freq", "intvl")),
    (r"半年|近半年|过去半年", ("freq", "halfyr")),
    (r"年内|近一年|过去一年|近1年|年度|全年|年", ("freq", "yr")),
    (r"近一季|过去一季|季度|近1季|季", ("freq", "qtr")),
    (r"近一月|过去一月|近1月|月度|月", ("freq", "mo")),
    (r"近一周|过去一周|近1周|周度|周", ("freq", "wk")),
    (r"当日|单日|今日|日度", ("freq", "day")),
]

# 指标规范名侧
NAME_PERIOD = [
    (r"\(ttm[^)]*\).*\(报告期\)", "ttm2"),
    (r"\(ttm[^)]*\)", "ttm"),
    (r"单季度|\(单季\)", "qtr"),
    (r"累计", "cum"),
    (r"期末", "eop"),
    (r"\(mrq\)", "mrq"),
]
NAME_FREQ = [("区间", "intvl"), ("半年", "halfyr"), ("周", "wk"),
             ("月", "mo"), ("季", "qtr"), ("年", "yr"), ("日", "day")]


def user_caliber(phrase):
    """从用户口语里抽口径，返回 (口径dict, 去掉口径词后的裸名)"""
    s = cn2num(norm(phrase))
    s = REPORT_WORD.sub("", s).strip()   # 先摘报告期词，保护「年报」不被 freq 轴误吃
    cal, base = {}, s
    for pat, (axis, val) in USER_CALIBER:
        if axis in cal:
            continue
        m = re.search(pat, base)
        if m:
            # freq 轴只在行情类词汇出现时才可信，先记下待后续核验
            cal[axis] = val
            base = base[:m.start()] + base[m.end():]
    base = re.sub(r"[的了]+$", "", base).strip("()%（）,")
    base = re.sub(r"^[\-_/.·\s]+|[\-_/.·\s]+$", "", base)
    return cal, base


def name_caliber(name):
    """从指标规范名抽口径"""
    n = norm(name)
    cal = {}
    for pat, val in NAME_PERIOD:
        if re.search(pat, n):
            cal["period"] = val
            break
    if "同比" in n:
        cal["growth"] = "yoy"
    elif "环比" in n:
        cal["growth"] = "mom"
    if "n期均值" in n:
        cal["stat"] = "avg"
    elif "n期最小值" in n:
        cal["stat"] = "min"
    elif "n期最大值" in n:
        cal["stat"] = "max"
    if n.startswith("预测"):
        cal["fcst"] = "yes"
    for kw, val in NAME_FREQ:
        if n.startswith(kw):
            cal["freq"] = val
            break
    if "最高" in n:
        cal.setdefault("stat", "max")
    if "最低" in n:
        cal.setdefault("stat", "min")
    return cal


# 会改变科目口径的限定词：用户提了就必须出现在指标名里，反之亦然
QUALIFIERS = [
    (r"归母|归属|母公司股东", "归母"),
    (r"扣非|扣除非经常", "扣非"),
    (r"少数股东", "少数股东"),
    (r"基本每股|基本eps", "基本"),
    (r"稀释", "稀释"),
    (r"自由流通", "自由流通"),
    (r"每股", "每股"),
]

PAREN = re.compile(r"\((利润表|负债表|流量表|现金流量表)[^)]*\)|\(ttm[^)]*\)|"
                   r"\(报告期\)|\(单季度\)|\(累计\)|\(期末\)|\(mrq\)|\(%\)|"
                   r"\(近12个月\)|\(期末股本摊薄\)|\(反推法\)")


def name_base(name):
    """指标规范名去掉口径装饰，得到可比对的裸名"""
    n = norm(name)
    n = re.sub(r"^(其中|加|减|其中\)|减\)|加\)):", "", n)   # 报表科目的「其中:应收账款」前缀
    n = PAREN.sub("", n)
    n = n.replace("n期均值", "").replace("n期最小值", "").replace("n期最大值", "")
    n = re.sub(r"^(日|周|月|季|年|半年|区间)", "", n)
    n = re.sub(r"^预测", "", n)
    n = n.replace("同比", "").replace("环比", "")
    return n.strip("(),%")


# ------------------------------------------------------------ 指标解析

SEARCH_LIMIT = 100
JUNK_SCORE = 150.0      # api score 低于此值基本是无关噪声
WEAK_SCORE = 1200.0     # 低于此值属弱命中，需要提示人工确认


class SearchError(Exception):
    """指标搜索接口失败（鉴权过期 / 限流 / 服务端异常），msg 含可展示的原始信息。"""


def _api_error_msg(body: Optional[dict], fallback: str = "") -> str:
    """从业务响应里取出可展示的错误文案。"""
    if isinstance(body, dict):
        msg = body.get("msg") or body.get("message")
        if msg is not None and str(msg).strip():
            return str(msg).strip()
    return (fallback or "").strip()


def search_indicator(keyword: str, limit: int = SEARCH_LIMIT, *, _headers: Optional[dict] = None) -> list:
    """搜索指标，返回 List[Dict]；接口失败时抛 SearchError，绝不返回字符串。"""
    headers = dict(_headers) if _headers else get_authorization_headers()
    headers["Content-Type"] = "application/json"
    payload = {"keyword": keyword, "limit": limit}
    try:
        resp = authorized_request("POST", INDICATOR_URL, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    except Exception as e:
        raise SearchError(f"indicator search 请求失败: {e}") from e
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = None
    if not isinstance(resp_json, dict):
        snippet = (resp.text or "").strip()[:500]
        raise SearchError(
            f"indicator search HTTP {resp.status_code}: {snippet or '响应不是 JSON'}"
        )
    if resp.status_code >= 400 or str(resp_json.get("code")) != "000000" or not resp_json.get("status"):
        msg = _api_error_msg(resp_json, (resp.text or "")[:500] or f"HTTP {resp.status_code}")
        raise SearchError(f"indicator search 返回错误: {msg}")
    data = resp_json.get("data") or []
    res = []
    for x in data:
        try:
            x["score"] = float(x.get("score") or 0)
        except Exception:
            x["score"] = 0.0
        res.append(x)
    return res



def _keyword_candidates(phrase):
    """生成检索词序列：裸名 → 改写表 → 逐步截短"""
    cal, base = user_caliber(phrase)
    kws, seen = [], set()

    prio = {}

    def add(k, w=1.0):
        k = k.strip()
        if k and k not in seen:
            seen.add(k)
            kws.append(k)
            prio[k] = w
        elif k:
            prio[k] = max(prio.get(k, 0), w)
        # 改写表对每个关键词都查一遍（「股票价格」退化出的「价格」也要能查到改写）
        for t in REWRITE.get(k, []):
            t = norm(t)
            if t and t not in seen:
                seen.add(t)
                kws.append(t)
                prio[t] = max(w, 1.0)

    add(base)
    add(norm(phrase))
    # 接口对英文缩写大小写敏感（roe 命中弱、ROE 命中强），补上原文与大写形态
    raw = re.sub(r"[\s　]+", "", unicodedata.normalize("NFKC", str(phrase)))
    add(raw)
    if raw != raw.upper():
        add(raw.upper())
    if base != base.upper() and re.fullmatch(r"[a-z0-9/&]+", base):
        add(base.upper())
    # 去掉常见修饰后缀再试
    for suf in ("规模", "水平", "情况", "指标", "数据", "金额", "总额", "比例",
                "占比", "倍数", "比率", "系数", "大小", "高低", "多少", "次数",
                "体量", "总量", "总数", "程度", "质量", "能力", "总规模",
                "余额规模", "规模总额", "估值", "口径"):
        if base.endswith(suf) and len(base) > len(suf) + 1:
            add(base[: -len(suf)], 0.94)
    # 去掉常见修饰前缀（「公司经营范围」→经营范围、「静态市盈率」→市盈率）
    for pre in ("公司", "企业", "静态", "动态", "综合", "整体", "总体", "最新",
                "当前", "当日", "全部", "该股", "个股", "标的", "股票", "证券",
                "上市公司", "本期", "报告期"):
        if base.startswith(pre) and len(base) > len(pre) + 1:
            add(base[len(pre):], 0.94)
    # 复合结构取中心词：X占Y / X占比 / X的Y → 先用 X 再搜一遍
    for sep in ("占", "对", "较", "的"):
        if sep in base:
            head = base.split(sep)[0]
            if len(head) >= 2:
                add(head, 0.9)
    # 兜底退化词：只有主关键词全部弱命中时才动用（盲切首尾噪声大）
    fallback = []
    for cut in (1, 2):
        for cand in (base[cut:], base[: -cut]):
            if len(cand) >= 2 and cand not in seen and cand not in fallback:
                fallback.append(cand)
    return cal, base, kws, fallback, prio


def _sim(a, b):
    if a == b:
        return 1.0
    if a and (a in b or b in a):
        short, long_ = (a, b) if len(a) <= len(b) else (b, a)
        return 0.55 + 0.35 * (len(short) / max(len(long_), 1))
    return difflib.SequenceMatcher(None, a, b).ratio() * 0.6


def rank_candidates(base, cal, cands, rewrite_targets=(), kw_prio=None):
    """对 indicator search 的返回做本地重排"""
    if not cands:
        return []
    top_api = max(c["score"] for c in cands)
    top_rel = max(c.get("rel", c["score"]) for c in cands) or 1
    # 文本相似度整体很低时（英文缩写 PE/ROE/EPS 这类），接口自身的容错名匹配
    # 分数才是唯一可靠信号 —— 此时把 api score 的权重放大
    kw_prio = kw_prio or {base: 1.0}

    def sim_of(nb0):
        return max(_sim(k, nb0) * w for k, w in kw_prio.items()) if kw_prio else _sim(base, nb0)

    best_sim = max((sim_of(name_base(c.get("indicatorName", ""))) for c in cands), default=0.0)
    if re.fullmatch(r"[a-z0-9/&+.]{1,6}", base):
        # 英文缩写（PE/ROE/EPS/ROIC…）与中文指标名之间没有可用的字面相似度，
        # 只能靠接口的容错名匹配分数排序
        w_api = 150.0
    else:
        w_api = 22.0 + 110.0 * max(0.0, 1.0 - best_sim / 0.75)
    out = []
    for c in cands:
        nm = c.get("indicatorName", "")
        nb = name_base(nm)
        ncal = name_caliber(nm)
        if c.get("indicatorCode", "").startswith("frcst_"):
            ncal["fcst"] = "yes"

        # ---- 文本相似：对所有检索词取最大（退化词按优先级打折）
        sim = sim_of(nb)

        s = 100.0 * sim
        # api score 是接口侧的容错名匹配信号，弱文本相似时权重更高
        s += w_api * (c.get("rel", c["score"]) / top_rel)

        # ---- 口径一致性
        for axis in ("period", "growth", "stat", "fcst", "freq"):
            want, got = cal.get(axis), ncal.get(axis)
            if want and got == want:
                s += 26
            elif want and got and got != want:
                s -= 60
            elif want and not got:
                # 用户点名了口径但候选没有 → 只在 period/growth/fcst 上重罚
                s -= 80 if axis == "growth" else (45 if axis in ("period", "fcst") else 12)
            elif not want and got:
                # 用户没点名，候选却是变体 → 偏好裸指标。
                # period 轴上 cum/eop 是「裸口径」（营业收入(利润表,累计) 就是最朴素的那个），不罚
                if axis == "period":
                    s -= {"cum": 0, "eop": 0, "ttm": 26, "qtr": 30,
                          "ttm2": 40, "mrq": 8}.get(got, 20)
                elif axis == "freq":
                    s -= 0 if got == "day" else 30
                else:
                    s -= {"fcst": 85, "growth": 75, "stat": 55}.get(axis, 20)
        # 改写表是人工校准过的「这个说法就是那个指标」，命中目标名直接加分
        if rewrite_targets and nb in rewrite_targets:
            s += 48
        # 「毛利占比 / 分红比率 / 负债占比」要的是比率指标；「研发开支 / 负债总额」要的是绝对额
        want_ratio = bool(re.search(r"占比|比率|比重|占.{0,5}(比|率|重)|率$", base))
        is_ratio = bool(re.search(r"率|占比|/", nb))
        if want_ratio and is_ratio:
            s += 34
        elif want_ratio and not is_ratio:
            s -= 30
        elif not want_ratio and is_ratio and not re.search(r"率|倍|周转|比", base):
            s -= 26
        # 中文是修饰语在前、中心词在后：「负债合计」比「合同负债」更像用户说的「负债」
        if any(nb.startswith(k) and nb != k for k in kw_prio):
            s += 12
        # 归母 / 扣非 / 少数股东这类限定词错配，是彻头彻尾的换了个科目
        for qpat, qtok in QUALIFIERS:
            want, got = bool(re.search(qpat, base)), qtok in nb
            if want and got:
                s += 30
            elif want and not got:
                s -= 42
            elif not want and got:
                s -= 30
        # 现金流量表里的同名科目（如「净利润(现金流量表)」）只有在用户明说现金流时才该命中
        if "(现金流量表)" in norm(nm) and not re.search(r"现金流|流量表|现金", base):
            s -= 30
        # 比值型指标（名字里带 /）不该抢走「基础指标」的位置
        if "/" in nb and "/" not in base:
            s -= 28
        # 同分时偏好更短、更朴素的名字（EBITDA → 息税折旧摊销前利润，而不是它的各种比值）
        s -= 0.6 * max(0, len(nb) - len(base))
        out.append({
            "code": c.get("indicatorCode"),
            "name": nm,
            "base": nb,
            "api_score": round(c["score"], 1),
            "rank_score": round(s, 2),
            "caliber": ncal,
            "params": [p.get("paramKey") for p in (c.get("parameterList") or [])],
            "required": [p.get("paramKey") for p in (c.get("parameterList") or []) if p.get("required")],
            "param_detail": c.get("parameterList") or [],
            "scope_list": c.get("scopeList") or [],
            "markets": sorted({m.get("market") for m in (c.get("scopeList") or []) if m.get("market")}),
            "desc": (c.get("description") or "").strip(),
        })
    out.sort(key=lambda x: (-x["rank_score"], -x["api_score"]))
    return out


def resolve_indicator(phrase, top=5, caliber=None):
    """caliber: 调用方（模型）可以显式给出口径，形如
    {"period":"qtr|cum|ttm|ttm2|eop|mrq", "growth":"yoy|mom", "stat":"avg|min|max",
     "fcst":"yes", "freq":"day|wk|mo|qtr|halfyr|yr|intvl"}。
    给了就以它为准，不再从短语里猜——短语里的口径词仍会被剥掉以便检索。"""
    cal, base, kws, fallback, prio = _keyword_candidates(phrase)
    if caliber:
        cal = {k: v for k, v in caliber.items() if v}

    # 已知平台不提供的指标，直接给结论，别让接口返回噪声误导
    for k, note in NOT_AVAILABLE.items():
        if k in base or k in norm(phrase):
            return {"query": phrase, "status": "unavailable", "caliber": cal,
                    "base": base, "note": note, "candidates": []}

    pool, used, errors = {}, [], []

    def sweep(todo):
        prefetched_headers = get_authorization_headers()
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = [
                (kw, ex.submit(search_indicator, kw, _headers=prefetched_headers))
                for kw in todo
            ]
            for kw, fu in futures:
                try:
                    got = fu.result()
                except SearchError as e:
                    used.append({"keyword": kw, "error": str(e)})
                    errors.append(str(e))
                    continue
                used.append({"keyword": kw, "n": len(got)})
                w = prio.get(kw, 0.8)
                for g in got:
                    g = dict(g)
                    g["rel"] = g["score"] * w             # 全局可比的分数，按发现它的词优先级打折
                    prev = pool.get(g["indicatorCode"])
                    if not prev or g["rel"] > prev.get("rel", 0):
                        pool[g["indicatorCode"]] = g

    sweep(kws[:10])
    if not pool and errors:
        # 一个候选都没拿到且全部请求失败 → 接口/鉴权问题，不是「没有这个指标」
        return {"query": phrase, "status": "search_error", "caliber": cal,
                "base": base, "keywords": used, "error": errors[0], "candidates": []}
    if not any(g["score"] >= WEAK_SCORE for g in pool.values()):
        for k in fallback[:4]:
            prio.setdefault(k, 0.7)
        sweep(fallback[:4])          # 主关键词全弱 → 才启用盲切退化词
    cands = [c for c in pool.values() if c["score"] >= JUNK_SCORE]
    if not cands and pool:      # 全都弱命中时也别丢空，交给排序 + status 提示人工确认
        cands = sorted(pool.values(), key=lambda x: -x["score"])[:8]
    targets = {name_base(t) for t in (REWRITE.get(base, []) + REWRITE.get(norm(phrase), []))}
    ranked = rank_candidates(base, cal, cands, targets,
                             {k: prio.get(k, 0.8) for k in list(prio) + fallback[:4]})

    if not ranked:
        status = "not_found"
    elif ranked[0]["api_score"] < WEAK_SCORE and ranked[0]["base"] != base:
        status = "weak"
    elif len(ranked) > 1 and ranked[0]["rank_score"] - ranked[1]["rank_score"] < 8:
        status = "ambiguous"
    else:
        status = "ok"
    return {"query": phrase, "status": status, "caliber": cal, "base": base,
            "keywords": used, "candidates": ranked[:top]}


# ------------------------------------------------------------ 交易日 / 参数补全

SCALE_VALUES = {
    "0": "0", "个": "0",
    "3": "3", "千": "3",
    "4": "4", "万": "4",
    "6": "6", "百万": "6",
    "8": "8", "亿": "8",
    "9": "9", "十亿": "9",
    "12": "12", "万亿": "12",
}


def _parse_json_dict(raw: Optional[str], label: str = "--params") -> Dict[str, Any]:
    if not raw or not str(raw).strip():
        return {}
    text = str(raw).strip()
    try:
        from json_repair import repair_json
        obj = repair_json(text, return_objects=True)
    except Exception:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{label} 须为合法 JSON 字典: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"{label} 须为 JSON 对象（字典）")
    return obj


def _normalize_scale(val: Any) -> str:
    text = str(val).strip()
    return SCALE_VALUES.get(text, SCALE_VALUES.get(text.lower(), text))


_TRADE_DATE_CACHE: Dict[str, Optional[str]] = {}


def fetch_trade_dates(lookback_days: int = 30) -> List[str]:
    """用茅台日 K 取近期交易日列表（升序）。失败返回空列表。"""
    if not get_authorization_token():
        return []
    end = datetime.date.today()
    start = end - datetime.timedelta(days=lookback_days)
    headers = get_authorization_headers()
    headers["Content-Type"] = "application/json"
    payload = {
        "securityList": ["600519.SH"],
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "limit": 100,
        "fieldList": ["tradeDate", "close"],
    }
    try:
        r = authorized_request("POST", QUOTE_URL, headers=headers, json=payload, timeout=120)
        if r.status_code != 200:
            return []
        body = r.json()
    except Exception:
        return []
    if str(body.get("code", "")) != "000000" or body.get("status") is not True:
        return []
    block = body.get("data") or {}
    field_list = block.get("fieldList") or []
    rows = block.get("list") or []
    dates: List[str] = []
    if field_list and rows:
        try:
            idx = field_list.index("tradeDate")
        except ValueError:
            idx = 0
        for row in rows:
            if isinstance(row, (list, tuple)) and idx < len(row) and row[idx]:
                dates.append(str(row[idx])[:10])
    dates = sorted({d for d in dates if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)})
    return dates


def latest_trade_date(prev: bool = False) -> Optional[str]:
    """最近交易日；prev=True 时取上一交易日（两融 mgn_* 用）。"""
    key = "prev" if prev else "latest"
    if key in _TRADE_DATE_CACHE:
        return _TRADE_DATE_CACHE[key]
    dates = fetch_trade_dates()
    if not dates:
        _TRADE_DATE_CACHE[key] = None
        return None
    val = dates[-2] if prev and len(dates) >= 2 else dates[-1]
    if prev and len(dates) < 2:
        val = dates[-1]
    _TRADE_DATE_CACHE[key] = val
    return val


def _default_params_for_indicator(
    cand: dict,
    trade_date: Optional[str],
    report_date: Optional[str],
    prev_trade_date: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> List[dict]:
    """按指标参数元数据自动补 tradeDate / reportDate / scale 等。"""
    params_keys = set(cand.get("params") or [])
    required = set(cand.get("required") or [])
    code = str(cand.get("code") or "")
    detail_by_key = {
        str(p.get("paramKey")): p
        for p in (cand.get("param_detail") or [])
        if isinstance(p, dict) and p.get("paramKey")
    }
    out: Dict[str, str] = {}

    use_prev = code.startswith("mgn_")
    td = prev_trade_date if use_prev else trade_date
    if "tradeDate" in params_keys and td:
        out["tradeDate"] = td
    if "reportDate" in params_keys and report_date:
        out["reportDate"] = report_date

    # 有默认值的非日期必填参数：用 param_detail.defaultValue
    for pk in required:
        if pk in out or pk in ("tradeDate", "reportDate"):
            continue
        detail = detail_by_key.get(pk) or {}
        dv = detail.get("defaultValue")
        if dv is not None and str(dv).strip() and str(dv).strip() != "—":
            out[pk] = str(dv)

    if extra:
        for k, v in extra.items():
            pk = _normalize_param_key(k)
            if pk in ("scale", "量纲"):
                out["scale"] = _normalize_scale(v)
            else:
                out[pk] = str(v)

    # scale 不在 params 列表里时，若用户显式传了仍可带上（部分行情指标接受）
    return [{"paramKey": k, "paramValue": v} for k, v in out.items()]


def _normalize_param_key(key: Any) -> str:
    """去掉展示用必填尾缀 *（如 periodNum* → periodNum，避免误抄进 -p）。"""
    k = str(key).strip()
    while k.endswith("*"):
        k = k[:-1].rstrip()
    return k


def _split_indicator_tokens(raw: str) -> List[str]:
    return [t.strip() for t in re.split(r"[,，]", raw or "") if t.strip()]


_EXPR_CMP = re.compile(
    r"^(?P<lhs>.+?)\s*(?P<op>>=|<=|!=|==|>|<|contains|notcontains)\s*(?P<rhs>.+)$",
    re.IGNORECASE,
)
_EXPR_SPLIT = re.compile(r"(\s*&&\s*|\s*\|\|\s*)")
_SCALE_SUFFIXES = [
    ("万亿", "12"),
    ("十亿", "9"),
    ("亿", "8"),
    ("万", "4"),
    ("百万", "6"),
    ("千", "3"),
]


def _extract_indicator_phrases_from_expression(expression: str) -> List[str]:
    """从 `ROE > 15 && 总市值 > 500` 抽出左侧指标说法（跳过已是 F1 的）。"""
    phrases: List[str] = []
    seen = set()
    for part in _EXPR_SPLIT.split(expression or ""):
        if not part or part.strip() in ("&&", "||"):
            continue
        part = part.strip()
        # 去掉外层括号
        while part.startswith("(") and part.endswith(")"):
            part = part[1:-1].strip()
        m = _EXPR_CMP.match(part)
        if not m:
            continue
        lhs = m.group("lhs").strip().strip("\"'")
        if re.fullmatch(r"F[1-9]\d*", lhs):
            continue
        if lhs and lhs not in seen:
            seen.add(lhs)
            phrases.append(lhs)
    return phrases


def _normalize_expr_scale_suffixes(
    expression: str,
) -> Tuple[str, Dict[str, Dict[str, str]]]:
    """把 `总市值 > 500亿` 规范为 `总市值 > 500`，并返回 {说法: {scale: …}}。

    contains / notcontains 的右侧字符串字面量必须保留引号（API 要求如 `F1 contains '酒'`）。
    """
    auto_params: Dict[str, Dict[str, str]] = {}
    pieces = _EXPR_SPLIT.split(expression or "")
    out = []
    for part in pieces:
        if not part or part.strip() in ("&&", "||") or re.fullmatch(r"\s*&&\s*|\s*\|\|\s*", part):
            out.append(part)
            continue
        raw = part
        stripped = part.strip()
        parens = ""
        core = stripped
        if core.startswith("(") and core.endswith(")"):
            parens = "()"
            core = core[1:-1].strip()
        m = _EXPR_CMP.match(core)
        if not m:
            out.append(raw)
            continue
        lhs, op, rhs = m.group("lhs").strip(), m.group("op"), m.group("rhs").strip()
        op_l = op.lower()

        # 文本运算符：统一成单引号字面量，切勿剥掉引号
        if op_l in ("contains", "notcontains"):
            rhs_out = _normalize_string_literal(rhs)
            new_core = f"{lhs} {op_l} {rhs_out}"
            if parens:
                new_core = f"({new_core})"
            lead = raw[: len(raw) - len(raw.lstrip())] if raw.strip() else ""
            trail = raw[len(raw.rstrip()) :] if raw.strip() else ""
            out.append(f"{lead}{new_core}{trail}")
            continue

        rhs_body = rhs.strip().strip("\"'")
        scale_val = None
        for suf, sval in _SCALE_SUFFIXES:
            if rhs_body.endswith(suf) and len(rhs_body) > len(suf):
                num = rhs_body[: -len(suf)].strip()
                if re.fullmatch(r"-?\d+(\.\d+)?", num):
                    rhs_body = num
                    scale_val = sval
                break
        if scale_val and not re.fullmatch(r"F[1-9]\d*", lhs):
            auto_params.setdefault(lhs, {})["scale"] = scale_val
            new_rhs = rhs_body  # 数值，无引号
        else:
            # 未改量纲时保持原 RHS（含引号）
            new_rhs = rhs
        new_core = f"{lhs} {op} {new_rhs}"
        if parens:
            new_core = f"({new_core})"
        lead = raw[: len(raw) - len(raw.lstrip())] if raw.strip() else ""
        trail = raw[len(raw.rstrip()) :] if raw.strip() else ""
        out.append(f"{lead}{new_core}{trail}")
    return "".join(out), auto_params


def _normalize_string_literal(rhs: str) -> str:
    """contains 右侧 → 单引号字面量：\"白酒\" / '白酒' / 白酒 → '白酒'。"""
    s = (rhs or "").strip()
    if len(s) >= 2 and ((s[0] == s[-1] == "'") or (s[0] == s[-1] == '"')):
        inner = s[1:-1]
    else:
        inner = s.strip("\"'")
    inner = inner.replace("\\'", "'").replace('\\"', '"')
    # 字面量内单引号加倍转义（API 示例用单引号）
    inner = inner.replace("'", "\\'")
    return f"'{inner}'"


def _ensure_contains_quotes(expression: str) -> str:
    """兜底：rewrite 后若 contains 右侧仍无引号则补上。"""
    pieces = _EXPR_SPLIT.split(expression or "")
    out = []
    for part in pieces:
        if not part or part.strip() in ("&&", "||") or re.fullmatch(r"\s*&&\s*|\s*\|\|\s*", part):
            out.append(part)
            continue
        raw = part
        stripped = part.strip()
        wrap = stripped.startswith("(") and stripped.endswith(")")
        core = stripped[1:-1].strip() if wrap else stripped
        m = _EXPR_CMP.match(core)
        if not m:
            out.append(raw)
            continue
        lhs, op, rhs = m.group("lhs").strip(), m.group("op"), m.group("rhs").strip()
        if op.lower() not in ("contains", "notcontains"):
            out.append(raw)
            continue
        new_core = f"{lhs} {op.lower()} {_normalize_string_literal(rhs)}"
        if wrap:
            new_core = f"({new_core})"
        lead = raw[: len(raw) - len(raw.lstrip())] if raw.strip() else ""
        trail = raw[len(raw.rstrip()) :] if raw.strip() else ""
        out.append(f"{lead}{new_core}{trail}")
    return "".join(out)


def _replace_expr_alias(expression: str, alias: str, field: str) -> str:
    if not alias or alias == field:
        return expression
    if re.fullmatch(r"[A-Za-z0-9_]+", alias):
        return re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])",
            field,
            expression,
            flags=re.IGNORECASE,
        )
    return expression.replace(alias, field)


def _rewrite_expression_to_fields(
    expression: str,
    aliases_by_field: Dict[str, List[str]],
) -> str:
    """把表达式里的指标说法/编码替换为 F1、F2…（长词优先）。

    不改动引号内的字符串字面量，避免 `contains '白酒'` 被误替换。
    """
    pairs: List[Tuple[str, str]] = []
    for field, aliases in aliases_by_field.items():
        for a in aliases:
            a = (a or "").strip()
            if a and a != field:
                pairs.append((a, field))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)

    # 拆出 '...' / "..." 字面量
    lit_pat = re.compile(r"('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")")
    parts = lit_pat.split(expression or "")
    out_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # 字面量
            out_parts.append(part)
            continue
        chunk = part
        for alias, field in pairs:
            chunk = _replace_expr_alias(chunk, alias, field)
        out_parts.append(chunk)
    return "".join(out_parts)


def _params_for_token(
    token: str,
    field: str,
    code: str,
    name: str,
    params_arg: Dict[str, Any],
) -> Dict[str, Any]:
    """从 -p 字典里取出该指标的覆盖参数（支持 token / field / code / name 键）。"""
    shared: Dict[str, Any] = {}
    nested: Dict[str, Any] = {}
    for key, val in (params_arg or {}).items():
        if isinstance(val, dict) and key in (token, field, code, name, code.lower()):
            nested.update(val)
        elif not isinstance(val, (dict, list)):
            if key in ("scale", "量纲", "currency", "calendarType", "日期类型"):
                shared[key] = val
            elif key in (token, field, code, name):
                shared[key] = val
    return {**shared, **nested}


def build_payload_auto(
    universe_phrases: List[str],
    indicators_raw: Optional[str],
    expression: str,
    params_arg: Optional[Dict[str, Any]] = None,
    trade_date: Optional[str] = None,
    report_date: Optional[str] = None,
    top: int = 5,
) -> dict:
    """自动串联：解析范围 + 解析指标 + 补参数 → payload；失败时 status != ok。

    `-e` 支持 `F1 > 15` 或 `ROE > 15 && 总市值 > 500`（指标名写法）；
    未传 `-i` 时从表达式左侧自动抽取指标说法。
    """
    params_arg = dict(params_arg or {})
    uni = resolve_universe(universe_phrases)
    if uni.get("status") != "ok":
        return {
            "status": uni.get("status") or "error",
            "blocked": True,
            "stage": "universe",
            "error": uni.get("error") or "范围解析失败",
            "universe_resolve": uni,
        }

    if not (expression or "").strip():
        return {
            "status": "error",
            "blocked": True,
            "stage": "expression",
            "error": "--expression 不能为空",
        }

    raw_expression = expression.strip()
    expression, scale_from_expr = _normalize_expr_scale_suffixes(raw_expression)
    for k, v in scale_from_expr.items():
        if k not in params_arg:
            params_arg[k] = v
        elif isinstance(params_arg[k], dict) and "scale" not in params_arg[k]:
            params_arg[k] = {**params_arg[k], **v}

    tokens = _split_indicator_tokens(indicators_raw or "")
    if not tokens:
        tokens = _extract_indicator_phrases_from_expression(expression)
    if not tokens:
        return {
            "status": "error",
            "blocked": True,
            "stage": "indicator",
            "error": "未找到指标：请提供 -i/--indicators，或在 -e 中写「指标名 > 阈值」",
        }

    td = trade_date or latest_trade_date(prev=False)
    prev_td = latest_trade_date(prev=True)
    rd = report_date or latest_report_end(annual_only=True)

    indicator_list = []
    resolves = []
    aliases_by_field: Dict[str, List[str]] = {}

    for i, token in enumerate(tokens):
        field = f"F{i + 1}"
        resolved = resolve_indicator(token, top=top)
        resolves.append(resolved)
        st = resolved.get("status")
        if st != "ok":
            return {
                "status": st,
                "blocked": True,
                "stage": "indicator",
                "error": f"指标「{token}」解析为 {st}，请确认后重试或改用更精确说法",
                "indicator_resolve": resolves,
                "universe_resolve": uni,
            }
        cands = resolved.get("candidates") or []
        if not cands:
            return {
                "status": "not_found",
                "blocked": True,
                "stage": "indicator",
                "error": f"指标「{token}」无候选",
                "indicator_resolve": resolves,
                "universe_resolve": uni,
            }
        cand = cands[0]
        code = cand["code"]
        name = cand.get("name") or code
        base = cand.get("base") or ""
        aliases_by_field[field] = list(dict.fromkeys(
            [a for a in (token, name, base, code, code.lower() if code else "") if a]
        ))
        extra = _params_for_token(token, field, code, name, params_arg)
        parameters = _default_params_for_indicator(cand, td, rd, prev_td, extra)
        indicator_list.append({
            "field": field,
            "indicatorCode": code,
            "parameters": parameters,
        })

    expr_fields = _rewrite_expression_to_fields(expression, aliases_by_field)
    expr_fields = _ensure_contains_quotes(expr_fields)
    # 若仍含未映射的中文/英文指标名（没有变成 F*），提示
    dangling = _extract_indicator_phrases_from_expression(expr_fields)
    if dangling:
        return {
            "status": "error",
            "blocked": True,
            "stage": "expression",
            "error": f"表达式中仍有未映射的指标说法: {', '.join(dangling)}。"
                     f"请写入 -i，或确保与 -i 中的说法一致",
            "expression": expr_fields,
            "indicator_resolve": resolves,
            "universe_resolve": uni,
        }

    payload = {
        "universe": uni["universe"],
        "expression": expr_fields,
        "indicatorList": indicator_list,
    }
    return {
        "status": "ok",
        "payload": payload,
        "expression_raw": raw_expression,
        "universe_resolve": uni,
        "indicator_resolve": resolves,
        "dates": {
            "tradeDate": td,
            "prevTradeDate": prev_td,
            "reportDate": rd,
        },
    }


def stock_screener(payload: dict) -> dict:
    """HTTP POST 到指标选股 API，返回原始响应 dict（含业务错误 code/msg）。"""
    headers = get_authorization_headers()
    headers["Content-Type"] = "application/json"
    try:
        resp = authorized_request("POST", INDICATOR_STOCK_URL, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
    except requests.RequestException as e:
        return {"code": "-1", "status": False, "msg": f"请求失败: {e}", "data": ""}
    except Exception as e:
        return {"code": "-1", "status": False, "msg": str(e), "data": ""}

    try:
        body = resp.json()
    except Exception:
        body = None

    if isinstance(body, dict):
        # 业务失败常伴随 HTTP 400，仍以响应体 msg 为准
        msg = _api_error_msg(body)
        if msg and not body.get("msg"):
            body = {**body, "msg": msg}
        if resp.status_code >= 400:
            body.setdefault("status", False)
        return body

    snippet = (resp.text or "").strip()[:800]
    return {
        "code": str(resp.status_code) if resp.status_code >= 400 else "-1",
        "status": False,
        "msg": snippet or f"HTTP {resp.status_code}",
        "data": "",
    }


def _sanitize_payload_param_keys(payload: dict) -> dict:
    """规范化 indicatorList.parameters[].paramKey（剥掉误抄的 * 尾缀）。"""
    inds = payload.get("indicatorList")
    if not isinstance(inds, list):
        return payload
    for ind in inds:
        if not isinstance(ind, dict):
            continue
        params = ind.get("parameters")
        if not isinstance(params, list):
            continue
        for p in params:
            if isinstance(p, dict) and p.get("paramKey") is not None:
                p["paramKey"] = _normalize_param_key(p["paramKey"])
    return payload


def validate_payload(payload: dict) -> list:
    """校验指标选股 payload 格式，返回 blockers 列表（空 = 通过）。

    覆盖：必填字段、universe 元素类型、field 格式与重复、expression 引用完整性、
    parameters 的 paramKey/paramValue 成对。校验在发起实际选股请求之前进行。
    """
    blockers = []
    for key in ("universe", "expression", "indicatorList"):
        if key not in payload:
            blockers.append(f"payload 缺少必填字段: {key}")
    if not payload.get("universe"):
        blockers.append("universe 不能为空")
    elif not all(isinstance(u, str) and u.strip() for u in payload["universe"]):
        blockers.append("universe 必须是非空字符串列表（证券代码或10位板块ID）")
    expr = payload.get("expression")
    if expr is not None and not (isinstance(expr, str) and expr.strip()):
        blockers.append("expression 必须是非空字符串")
    if not payload.get("indicatorList"):
        blockers.append("indicatorList 不能为空")
    fields = set()
    for i, ind in enumerate(payload.get("indicatorList") or []):
        prefix = f"indicatorList[{i}]"
        field = ind.get("field")
        if not field:
            blockers.append(f"{prefix} 缺少 field（如 'F1'）")
        elif not re.fullmatch(r"F[1-9]\d*", str(field)):
            blockers.append(f"{prefix}.field '{field}' 格式错误，必须为 F 加正整数（F1, F2…）")
        elif field in fields:
            blockers.append(f"{prefix}.field '{field}' 重复，同一次请求中不可重复")
        else:
            fields.add(field)
        if not ind.get("indicatorCode"):
            blockers.append(f"{prefix} 缺少 indicatorCode")
        params = ind.get("parameters")
        if params is not None:
            if not isinstance(params, list):
                blockers.append(f"{prefix} parameters 必须是列表，每个元素为 {{paramKey, paramValue}}")
            else:
                for j, p in enumerate(params):
                    if not isinstance(p, dict):
                        blockers.append(f"{prefix}.parameters[{j}] 必须是对象 {{paramKey, paramValue}}")
                        continue
                    has_key, has_val = bool(p.get("paramKey")), p.get("paramValue") not in (None, "")
                    if not has_key and not has_val:
                        blockers.append(f"{prefix}.parameters[{j}] 缺少 paramKey 与 paramValue")
                    elif not has_key:
                        blockers.append(f"{prefix}.parameters[{j}] 缺少 paramKey（paramValue='{p.get('paramValue')}'）")
                    elif not has_val:
                        blockers.append(f"{prefix}.parameters[{j}].paramKey='{p.get('paramKey')}' 缺少 paramValue")
    # expression 引用的变量必须都在 indicatorList 里定义
    if isinstance(expr, str) and fields:
        undefined = sorted({t for t in re.findall(r"F[1-9]\d*", expr) if t not in fields})
        if undefined:
            blockers.append(f"expression 引用了未定义的变量: {', '.join(undefined)}"
                            f"（已定义: {', '.join(sorted(fields))}）")
    return blockers


def cmd_run(payload: dict, meta: Optional[dict] = None, output_dir: Optional[str] = None) -> dict:
    """校验 payload + 执行指标选股 HTTP 请求。"""
    payload = _sanitize_payload_param_keys(payload)
    blockers = validate_payload(payload)
    if blockers:
        return {"blocked": True, "blockers": blockers, "payload": payload, **(meta or {})}

    resp = stock_screener(payload)

    if not resp.get("status") or str(resp.get("code")) != "000000":
        return {
            "exit_code": 1,
            "error": _api_error_msg(resp) or "未知错误",
            "errorType": resp.get("errorType"),
            "code": resp.get("code"),
            "traceId": resp.get("traceId"),
            "payload": payload,
            **(meta or {}),
        }

    data = resp.get("data") or {}
    codes = data.get("securityCodeList") or []
    names = data.get("securityNameList") or []
    inds = data.get("indicatorList") or []
    values = data.get("values") or []

    rows = []
    for i, code in enumerate(codes):
        row = {"security": code, "name": names[i] if i < len(names) else ""}
        for j, ind in enumerate(inds):
            key = ind.get("name") or ind.get("code") or ind.get("field")
            if key in row:                      # 同名指标（不同参数）用 field 消歧
                key = f"{key}({ind.get('field')})"
            row[key] = values[i][j] if i < len(values) and j < len(values[i]) else None
        rows.append(row)

    out = {
        "exit_code": 0,
        "total": len(codes),
        "rows": rows,
        "indicators": [{"field": ind.get("field"), "code": ind.get("code"),
                        "name": ind.get("name")} for ind in inds],
        "payload": payload,
    }
    if meta:
        out.update(meta)
    if rows:
        try:
            out["saved_file"] = save_data_csv(
                _rows_to_csv_records(rows),
                method_name="screener",
                module_name="screener",
                output_dir=output_dir,
            )
        except Exception as e:
            out["save_error"] = str(e)
    return out


def cmd_run_auto(
    universe: List[str],
    indicators: Optional[str],
    expression: str,
    params: Optional[Dict[str, Any]] = None,
    trade_date: Optional[str] = None,
    report_date: Optional[str] = None,
    dry_run: bool = False,
    output_dir: Optional[str] = None,
) -> dict:
    """company_indicator 风格串联：范围/指标解析 → 补参 →（可选）选股。"""
    built = build_payload_auto(
        universe_phrases=universe,
        indicators_raw=indicators,
        expression=expression,
        params_arg=params,
        trade_date=trade_date,
        report_date=report_date,
    )
    if built.get("status") != "ok":
        return built
    meta = {
        "universe_resolve": built.get("universe_resolve"),
        "indicator_resolve": [
            {
                "query": r.get("query"),
                "status": r.get("status"),
                "picked": {
                    "code": (r.get("candidates") or [{}])[0].get("code"),
                    "name": (r.get("candidates") or [{}])[0].get("name"),
                },
            }
            for r in (built.get("indicator_resolve") or [])
        ],
        "dates": built.get("dates"),
        "expression_raw": built.get("expression_raw"),
    }
    if dry_run:
        return {
            "status": "ok",
            "dry_run": True,
            "payload": _sanitize_payload_param_keys(built["payload"]),
            **meta,
        }
    return cmd_run(built["payload"], meta=meta, output_dir=output_dir)


# ------------------------------------------------------------ Markdown 输出


def _md_escape_cell(val: Any) -> str:
    if val is None:
        return "—"
    s = re.sub(r"\s+", " ", str(val)).replace("|", "\\|").strip()
    return s if s else "—"


def _md_table(headers: List[str], rows: List[List[Any]], max_rows: int = 20) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    shown = rows[:max_rows]
    for row in shown:
        lines.append("| " + " | ".join(_md_escape_cell(c) for c in row) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n> 仅展示前 {max_rows} 条，共 {len(rows)} 条。")
    return "\n".join(lines)


def _md_table_head_tail(
    headers: List[str],
    rows: List[List[Any]],
    head: int = 3,
    tail: int = 3,
) -> Tuple[str, str]:
    """返回 (markdown 表, 脚注)。行数 ≤ head+tail 时全展示。"""
    n = len(rows)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    if n <= head + tail:
        for row in rows:
            lines.append("| " + " | ".join(_md_escape_cell(c) for c in row) + " |")
        return "\n".join(lines), f"\n共 {n} 行\n"

    for row in rows[:head]:
        lines.append("| " + " | ".join(_md_escape_cell(c) for c in row) + " |")
    lines.append("| " + " | ".join(["…"] + ["…"] * (len(headers) - 1)) + " |")
    for row in rows[-tail:]:
        lines.append("| " + " | ".join(_md_escape_cell(c) for c in row) + " |")
    note = f"\n（展示前 {head} 行与后 {tail} 行，共 {n} 行；完整数据见文件）\n"
    return "\n".join(lines), note


def _format_universe_summary(res: dict) -> str:
    uni = res.get("universe_resolve") or {}
    details = uni.get("details") or []
    if details:
        parts = []
        for d in details:
            resolved = d.get("resolved") or {}
            name = resolved.get("name") or resolved.get("id") or d.get("input")
            hid = resolved.get("id") or ""
            hier = resolved.get("hierarchy") or ""
            # 体系取 hierarchy 第二段（如 中信行业类）
            system = ""
            if hier:
                segs = hier.split("-")
                system = segs[1] if len(segs) > 1 else hier
            label = f"{name}"
            if system:
                label += f"（{system}）"
            if hid:
                label += f" `{hid}`"
            parts.append(label)
        summary = "、".join(parts)
    else:
        payload = res.get("payload") or {}
        ids = payload.get("universe") or []
        summary = "、".join(f"`{u}`" for u in ids) if ids else "—"
    if res.get("universe_defaulted"):
        summary += "（未指定 -u，默认全A）"
    return summary


def _format_indicator_lines(res: dict) -> List[str]:
    payload = res.get("payload") or {}
    inds = payload.get("indicatorList") or []
    resolve = {
        (item.get("picked") or {}).get("code"): item
        for item in (res.get("indicator_resolve") or [])
        if (item.get("picked") or {}).get("code")
    }
    # also map by field order from indicators summary
    name_by_code = {
        x.get("code"): x.get("name")
        for x in (res.get("indicators") or [])
        if x.get("code")
    }
    lines = []
    for ind in inds:
        field = ind.get("field") or "?"
        code = ind.get("indicatorCode") or ""
        name = name_by_code.get(code) or (resolve.get(code) or {}).get("picked", {}).get("name") or code
        params = ind.get("parameters") or []
        param_bits = []
        for p in params:
            if not isinstance(p, dict):
                continue
            pk, pv = p.get("paramKey"), p.get("paramValue")
            if pk == "scale":
                scale_label = {"0": "元", "4": "万", "8": "亿", "9": "十亿", "12": "万亿"}.get(
                    str(pv), str(pv)
                )
                param_bits.append(f"量纲 {scale_label}")
            elif pk:
                param_bits.append(f"{pk}={pv}")
        tail = f"（{'；'.join(param_bits)}）" if param_bits else ""
        lines.append(f"- `{field}` {name} (`{code}`){tail}")
    return lines


def _format_scope_list(scope_list: Optional[List[dict]], markets: Optional[List[Any]] = None) -> str:
    """适用范围文案（对齐 company_indicator）。"""
    if scope_list:
        parts = []
        for s in scope_list:
            if not isinstance(s, dict):
                continue
            market = s.get("market") or ""
            st = s.get("securityType") or ""
            label = " / ".join(x for x in (market, st) if x)
            restriction = s.get("usageRestriction")
            if restriction and str(restriction).strip() and str(restriction).strip().lower() != "null":
                label = f"{label}（{str(restriction).strip()}）" if label else str(restriction).strip()
            if label:
                parts.append(label)
        if parts:
            return "；".join(parts)
    if markets:
        return "；".join(str(m) for m in markets if m)
    return "—"


def _format_parameter_list(param_list: Optional[List[dict]]) -> str:
    """请求参数明细表（对齐 company_indicator；-p 键用「参数编码」）。"""
    if not param_list:
        return "_无额外参数_"
    lines = [
        "| 参数编码 | 参数名称 | 类型 | 必填 | 默认值 | 说明 | 枚举 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in param_list:
        if not isinstance(p, dict):
            continue
        enums = p.get("enumList") or []
        enum_text = "；".join(
            f"{e.get('label', '')}={e.get('value', '')}"
            for e in enums
            if isinstance(e, dict)
        ) or "—"
        dv = p.get("defaultValue")
        lines.append(
            "| {paramKey} | {paramName} | {paramType} | {required} | {defaultValue} | {paramDescription} | {enum} |".format(
                paramKey=_md_escape_cell(p.get("paramKey", "")),
                paramName=_md_escape_cell(p.get("paramName", "")),
                paramType=_md_escape_cell(p.get("paramType", "")),
                required="是" if p.get("required") else "否",
                defaultValue=_md_escape_cell(dv if dv is not None else "—"),
                paramDescription=_md_escape_cell((p.get("paramDescription") or "—").replace("\n", " ")),
                enum=_md_escape_cell(enum_text),
            )
        )
    return "\n".join(lines)


def _format_indicator_candidate_detail(c: dict, index: int, preferred: bool = False) -> str:
    """单条指标候选的完整说明（含参数表）。"""
    mark = " ← 首选" if preferred else ""
    code = c.get("code") or ""
    name = c.get("name") or ""
    scope = _format_scope_list(c.get("scope_list"), c.get("markets"))
    desc = (c.get("desc") or "—").strip() or "—"
    rank = c.get("rank_score")
    api = c.get("api_score")
    score_bits = []
    if rank is not None:
        score_bits.append(f"rank={rank}")
    if api is not None:
        score_bits.append(f"api={api}")
    score_txt = "；".join(score_bits) if score_bits else "—"
    params_md = _format_parameter_list(c.get("param_detail") or [])
    return (
        f"### {index}. {name} (`{code}`){mark}\n\n"
        f"- **相关度**：{score_txt}\n"
        f"- **适用范围**：{scope}\n"
        f"- **算法说明**：{desc}\n\n"
        f"**请求参数**（`-p` 键填「参数编码」，不要带必填标记）\n\n"
        f"{params_md}\n"
    )


def _format_candidates_md(failed: dict) -> str:
    """范围/指标歧义时的候选列表。"""
    cands = failed.get("candidates") or []
    if not cands:
        # resolve_universe 包在 failed 里
        return ""
    lines = []
    if failed.get("kind") == "indicator" or (
        "param_detail" in (cands[0] or {}) or (
            "code" in (cands[0] or {}) and "name" in (cands[0] or {}) and "gtsCode" not in (cands[0] or {})
            and "sectorId" not in (cands[0] or {})
        )
    ):
        # 指标候选：展开参数说明
        return "\n".join(
            _format_indicator_candidate_detail(c, i + 1, preferred=False)
            for i, c in enumerate(cands[:5])
        )
    if failed.get("kind") == "sector" or "sectorId" in (cands[0] or {}):
        lines.append("| sectorId | 名称 | hierarchy | score |")
        lines.append("| --- | --- | --- | --- |")
        for c in cands[:8]:
            lines.append(
                "| {id} | {name} | {hier} | {score} |".format(
                    id=_md_escape_cell(c.get("sectorId")),
                    name=_md_escape_cell(c.get("sectorName")),
                    hier=_md_escape_cell(c.get("hierarchy")),
                    score=_md_escape_cell(c.get("matchScore")),
                )
            )
    elif "gtsCode" in (cands[0] or {}):
        lines.append("| 代码 | 名称 | score |")
        lines.append("| --- | --- | --- |")
        for c in cands[:8]:
            lines.append(
                "| {code} | {name} | {score} |".format(
                    code=_md_escape_cell(c.get("gtsCode")),
                    name=_md_escape_cell(c.get("gtsName")),
                    score=_md_escape_cell(c.get("matchScore")),
                )
            )
    return "\n".join(lines)


def _rows_to_csv_records(rows: List[dict]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for row in rows:
        rec: Dict[str, Any] = {
            "代码": row.get("security"),
            "名称": row.get("name"),
        }
        for k, v in row.items():
            if k in ("security", "name"):
                continue
            rec[k] = v
        records.append(rec)
    return records


def format_run_markdown(res: dict, output_dir: Optional[str] = None) -> str:
    """将 run 结果组装为简洁可读的 Markdown；成功时完整结果落盘 CSV。"""
    # ---- 参数/用法错误
    if res.get("blocked") and res.get("blockers"):
        bullets = "\n".join(f"- {b}" for b in res["blockers"])
        return f"## 选股未执行\n\npayload 校验失败：\n\n{bullets}\n"

    if res.get("blocked") or (res.get("status") and res.get("status") != "ok" and res.get("dry_run") is not True):
        stage = res.get("stage") or ""
        err = res.get("error") or res.get("note") or "解析失败"
        title = "## 选股未执行\n\n"
        body = f"**原因**：{err}"
        if stage:
            body = f"**阶段**：{stage}\n\n{body}"

        # 范围歧义
        failed = res.get("failed") or (res.get("universe_resolve") or {}).get("failed")
        if not failed and res.get("stage") == "universe":
            failed = (res.get("universe_resolve") or {}).get("failed") or res.get("universe_resolve")
        # indicator 歧义：从 indicator_resolve 最后一项取 candidates
        extra = ""
        if res.get("stage") == "indicator":
            resolves = res.get("indicator_resolve") or []
            last = resolves[-1] if resolves else {}
            cands = last.get("candidates") or []
            if cands:
                extra = "\n\n### 指标候选\n\n" + _format_candidates_md(
                    {"candidates": cands, "kind": "indicator"}
                )
            elif last.get("note"):
                extra = f"\n\n{last.get('note')}"
        elif failed and isinstance(failed, dict):
            cands_md = _format_candidates_md(failed)
            if cands_md:
                extra = f"\n\n### 候选\n\n{cands_md}"
            rejected = failed.get("rejected") or []
            if rejected:
                rows = [
                    "| sectorId | 名称 | hierarchy | api分 | 字面相关度 |",
                    "| --- | --- | --- | --- | --- |",
                ]
                for c in rejected[:8]:
                    rows.append(
                        "| {id} | {name} | {hier} | {score} | {rel} |".format(
                            id=_md_escape_cell(c.get("sectorId")),
                            name=_md_escape_cell(c.get("sectorName")),
                            hier=_md_escape_cell(c.get("hierarchy")),
                            score=_md_escape_cell(c.get("matchScore")),
                            rel=_md_escape_cell(c.get("relevance")),
                        )
                    )
                extra += "\n\n### 已忽略的虚高匹配\n\n" + "\n".join(rows)
            note = failed.get("note")
            if note and note != err:
                extra += f"\n\n{note}"
        return title + body + extra + "\n"

    # ---- HTTP / 业务错误
    if res.get("exit_code") not in (0, None) and res.get("error"):
        et = res.get("errorType")
        code = res.get("code")
        meta_bits = []
        if et:
            meta_bits.append(str(et))
        if code not in (None, ""):
            meta_bits.append(f"code={code}")
        head = ""
        if meta_bits:
            head = f"**{' / '.join(meta_bits)}**\n\n"
        tid = res.get("traceId")
        tid_line = f"\n\n`traceId`: `{tid}`" if tid else ""
        return f"## 选股失败\n\n{head}{res.get('error')}{tid_line}\n"

    payload = res.get("payload") or {}
    expression = payload.get("expression") or "—"
    uni_summary = _format_universe_summary(res)
    ind_lines = _format_indicator_lines(res)

    # ---- dry-run
    if res.get("dry_run"):
        parts = [
            "## 选股预览（dry-run，未发起实际请求）\n",
            f"- **范围**：{uni_summary}",
            f"- **条件**：`{expression}`",
        ]
        raw_expr = res.get("expression_raw")
        if raw_expr and str(raw_expr).strip() != str(expression).strip():
            parts.append(f"- **原式**：`{raw_expr}`")
        if ind_lines:
            parts.append("- **指标**：")
            parts.extend("  " + line for line in ind_lines)
        dates = res.get("dates") or {}
        if dates:
            parts.append(
                f"- **日期**：tradeDate={dates.get('tradeDate') or '—'}；"
                f"reportDate={dates.get('reportDate') or '—'}"
            )
        parts.append("\n确认无误后去掉 `--dry-run` 再执行。\n")
        return "\n".join(parts)

    # ---- 成功
    total = res.get("total", 0)
    rows = res.get("rows") or []
    parts = [
        "## 选股结果\n",
        f"- **范围**：{uni_summary}",
        f"- **条件**：`{expression}`",
    ]
    raw_expr = res.get("expression_raw")
    if raw_expr and str(raw_expr).strip() != str(expression).strip():
        parts.append(f"- **原式**：`{raw_expr}`")
    if ind_lines:
        parts.append("- **口径**：")
        parts.extend("  " + line for line in ind_lines)
    parts.append(f"- **命中**：{total} 只")

    if not rows:
        parts.append("")
        parts.append("无符合条件的标的。若结果意外为空，请检查报告期 / 量纲 / 占位值（见 troubleshooting）。\n")
        return "\n".join(parts)

    # 完整结果落盘 CSV（对齐 gangtise-data quote 等脚本）
    csv_path = res.get("saved_file")
    if csv_path:
        parts.append(f"- **完整数据**：已保存到 csv：\n  `{csv_path}`\n")
    elif res.get("save_error"):
        parts.append(f"- **落盘失败**：{res.get('save_error')}\n")
    else:
        # 兼容：若上游未落盘则此处补写
        try:
            csv_path = save_data_csv(
                _rows_to_csv_records(rows),
                method_name="screener",
                module_name="screener",
                output_dir=output_dir,
            )
            res["saved_file"] = csv_path
            parts.append(f"- **完整数据**：已保存到 csv：\n  `{csv_path}`\n")
        except Exception as e:
            parts.append(f"- **落盘失败**：{e}\n")

    # 表头：代码、名称、各指标列（跳过 security/name）
    ind_cols = []
    for row in rows[:1]:
        for k in row:
            if k not in ("security", "name"):
                ind_cols.append(k)
    if not ind_cols and res.get("indicators"):
        ind_cols = [x.get("name") or x.get("code") for x in res["indicators"]]

    headers = ["代码", "名称"] + ind_cols
    table_rows = []
    for row in rows:
        table_rows.append(
            [row.get("security"), row.get("name")] + [row.get(c) for c in ind_cols]
        )
    # 正文展示前 3 + 后 3；完整数据看 csv（与 gangtise-data format_response 一致）
    parts.append("#### 样例数据\n")
    table_md, sample_note = _md_table_head_tail(headers, table_rows, head=3, tail=3)
    parts.append(table_md)
    parts.append(sample_note)
    return "\n".join(parts)


def format_resolve_indicator_markdown(results) -> str:
    """resolve-indicator 输出（对齐 company_indicator：候选 + 参数明细表）。"""
    items = results if isinstance(results, list) else [results]
    blocks = []
    for res in items:
        query = res.get("query") or ""
        status = res.get("status") or "?"
        cal = res.get("caliber") or {}
        cal_txt = "、".join(f"{k}={v}" for k, v in cal.items()) if cal else "默认"
        head = f"## 指标解析：{query}\n\n- **状态**：`{status}`\n- **口径**：{cal_txt}"
        if res.get("note"):
            head += f"\n- **说明**：{res['note']}"
        if res.get("error"):
            head += f"\n- **错误**：{res['error']}"
        cands = res.get("candidates") or []
        if not cands:
            blocks.append(head + "\n\n无候选。\n")
            continue
        details = [
            _format_indicator_candidate_detail(
                c, i, preferred=(i == 1 and status == "ok")
            )
            for i, c in enumerate(cands[:5], 1)
        ]
        hint = ""
        if status == "ambiguous":
            hint = "\n前两名接近，请选用更精确说法或直接传编码。\n"
        elif status == "weak":
            hint = "\n弱命中，请人工确认首选是否正确。\n"
        blocks.append(head + "\n\n" + "\n".join(details) + hint)
    return "\n".join(blocks).rstrip() + "\n"


def format_resolve_universe_markdown(res: dict) -> str:
    """resolve-universe 输出为简洁 Markdown。"""
    status = res.get("status") or "?"
    if status == "ok":
        lines = ["## 范围解析\n", f"- **状态**：`ok`", f"- **universe**：{', '.join(f'`{u}`' for u in (res.get('universe') or []))}"]
        for d in res.get("details") or []:
            resolved = d.get("resolved") or {}
            kind = d.get("kind") or ""
            lines.append(
                f"- **{d.get('input')}** → {resolved.get('name') or resolved.get('id')} "
                f"（{kind}；`{resolved.get('id')}`）"
            )
            hier = resolved.get("hierarchy")
            if hier:
                lines.append(f"  - hierarchy：{hier}")
        return "\n".join(lines) + "\n"

    # 失败 / 歧义
    failed = res.get("failed") or {}
    err = (
        res.get("error")
        or failed.get("note")
        or failed.get("error")
        or "解析失败"
    )
    lines = ["## 范围解析\n", f"- **状态**：`{status}`", f"- **原因**：{err}"]
    target = failed if failed else res
    if not target.get("note") and not target.get("candidates"):
        for d in res.get("details") or []:
            if d.get("status") and d.get("status") != "ok":
                target = d
                if d.get("note"):
                    lines[2] = f"- **原因**：{d.get('note')}"
                break
    cands = target.get("candidates") or []
    if cands:
        lines.append("\n### 候选\n")
        lines.append(_format_candidates_md({**target, "candidates": cands}))
    rejected = target.get("rejected") or []
    if rejected:
        lines.append("\n### 已忽略的虚高匹配\n")
        lines.append("| sectorId | 名称 | hierarchy | api分 | 字面相关度 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for c in rejected[:8]:
            lines.append(
                "| {id} | {name} | {hier} | {score} | {rel} |".format(
                    id=_md_escape_cell(c.get("sectorId")),
                    name=_md_escape_cell(c.get("sectorName")),
                    hier=_md_escape_cell(c.get("hierarchy")),
                    score=_md_escape_cell(c.get("matchScore")),
                    rel=_md_escape_cell(c.get("relevance")),
                )
            )
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ CLI

def main():
    ap = argparse.ArgumentParser(prog="screener.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("resolve-indicator", help="解析指标说法 → 候选编码与参数元数据")
    p1.add_argument("phrase", nargs="+")
    p1.add_argument("--top", type=int, default=5)
    p1.add_argument("--json", action="store_true", help="输出原始 JSON（默认 Markdown）")

    p_uni = sub.add_parser("resolve-universe", help="解析范围说法 → sectorId / gtsCode")
    p_uni.add_argument("phrase", nargs="+")
    p_uni.add_argument("--json", action="store_true", help="输出原始 JSON（默认 Markdown）")

    p2 = sub.add_parser(
        "run",
        help="执行选股。推荐 -u/-e 自动串联；或传入已构造的 payload JSON",
    )
    p2.add_argument(
        "spec",
        nargs="?",
        default=None,
        help="payload json 文件路径，或 - 表示从 stdin 读（与 -u/-e 互斥）",
    )
    p2.add_argument("-u", "--universe", action="append", default=None,
                    help="选股范围（可选，默认全A）：板块名/别名/sectorId/证券代码")
    p2.add_argument("-i", "--indicators", default=None,
                    help="指标名或编码，逗号分隔；可省略（从 -e 左侧自动抽取）")
    p2.add_argument("-e", "--expression", default=None,
                    help='条件表达式：支持 "F1 > 15" 或 "ROE > 15 && 总市值 > 500"')
    p2.add_argument(
        "-p", "--params", default=None,
        help='参数覆盖 JSON，如 {"总市值":{"scale":"8"}} 或 {"F2":{"scale":"亿"}}',
    )
    p2.add_argument("--trade-date", default=None, help="覆盖 tradeDate（默认最近交易日）")
    p2.add_argument("--report-date", default=None, help="覆盖 reportDate（默认最近已披露年报）")
    p2.add_argument("--dry-run", action="store_true",
                    help="只构造并打印 payload，不发起实际选股请求")
    p2.add_argument("--json", action="store_true",
                    help="输出原始 JSON（默认 Markdown）")
    p2.add_argument(
        "-od", "--output-dir", default=None,
        help="结果保存目录路径，建议使用绝对路径",
    )

    a = ap.parse_args()
    if a.cmd == "resolve-indicator":
        outs = [resolve_indicator(p, a.top) for p in a.phrase]
        payload = outs if len(outs) > 1 else outs[0]
        if a.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print(format_resolve_indicator_markdown(payload))
        return

    if a.cmd == "resolve-universe":
        payload = resolve_universe(a.phrase)
        if a.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        else:
            print(format_resolve_universe_markdown(payload))
        if payload.get("status") != "ok":
            sys.exit(1)
        return

    # run
    auto = a.universe is not None or a.indicators is not None or a.expression is not None
    if auto and a.spec:
        res = {
            "blocked": True,
            "error": "自动串联（-u/-i/-e）与 payload 文件/stdin 不能同时使用",
        }
        print(format_run_markdown(res, output_dir=getattr(a, "output_dir", None) or None) if not a.json else json.dumps(res, ensure_ascii=False, indent=1))
        sys.exit(1)

    if auto:
        if not a.expression:
            res = {
                "blocked": True,
                "error": "自动串联须提供 -e/--expression（-u 可省略，默认全A；-i 可省略，从表达式抽取）",
            }
            print(format_run_markdown(res, output_dir=getattr(a, "output_dir", None) or None) if not a.json else json.dumps(res, ensure_ascii=False, indent=1))
            sys.exit(1)
        try:
            params = _parse_json_dict(a.params)
        except ValueError as e:
            res = {"blocked": True, "error": str(e)}
            print(format_run_markdown(res, output_dir=getattr(a, "output_dir", None) or None) if not a.json else json.dumps(res, ensure_ascii=False, indent=1))
            sys.exit(1)
        universe = a.universe if a.universe else ["全A"]
        res = cmd_run_auto(
            universe=universe,
            indicators=a.indicators,
            expression=a.expression,
            params=params,
            trade_date=a.trade_date,
            report_date=a.report_date,
            dry_run=a.dry_run,
            output_dir=getattr(a, "output_dir", None) or None,
        )
        if not a.universe and isinstance(res, dict):
            res["universe_defaulted"] = True
    else:
        if not a.spec:
            res = {
                "blocked": True,
                "error": "请使用 -e 自动串联（-u 可选），或传入 payload.json / -",
            }
            print(format_run_markdown(res, output_dir=getattr(a, "output_dir", None) or None) if not a.json else json.dumps(res, ensure_ascii=False, indent=1))
            sys.exit(1)
        raw = json.load(sys.stdin) if a.spec == "-" else json.load(open(a.spec, encoding="utf-8"))
        if a.dry_run:
            res = {"status": "ok", "dry_run": True, "payload": _sanitize_payload_param_keys(raw)}
        else:
            res = cmd_run(raw, output_dir=getattr(a, "output_dir", None) or None)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(format_run_markdown(res, output_dir=getattr(a, "output_dir", None) or None))
    if res.get("blocked") or res.get("exit_code") not in (0, None):
        sys.exit(1)


if __name__ == "__main__":
    main()
