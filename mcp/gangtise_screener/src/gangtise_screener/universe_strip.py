#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块/市场短语剥壳工具

职责：把口语化的板块/市场短语剥壳为核心检索词，输出 JSON；
由 `universe_resolve.py` / `screener.py run -u` 自动串联搜索。

剥壳流程：
  1. 全角→半角、去空格
  2. 检查高频别名直通（全A / 两市 / 创业板 …）
  3. 逐层剥壳：去 UNI_TAIL 后缀 → 去 UNIVERSE_STRIP 修饰词
  4. 提取体系/市场提示
  5. 查 universe_rewrite 改写表

用法：
  python3 universe_strip.py "白酒板块"
  python3 universe_strip.py "中信半导体" "港股通"
"""
import argparse
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
if not os.path.isdir(ASSETS):
    ASSETS = os.path.join(os.path.dirname(HERE), "assets")


def load_asset(name, default):
    p = os.path.join(ASSETS, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return default


HINTS = load_asset("hints.json", {})
UNIVERSE_ALIAS = HINTS.get("universe_alias", {})
UNIVERSE_REWRITE = HINTS.get("universe_rewrite", {})

UNIVERSE_STRIP = ["的成分股", "成分股", "成份股", "成分", "成份", "产业链", "概念股",
                  "上市公司", "相关公司", "板块", "行业", "概念", "题材", "主题",
                  "领域", "个股", "公司", "股票", "全部", "所有", "里", "中", "内",
                  "范围", "市场", "这块", "那块", "这边", "方向", "赛道", "标的"]

UNI_TAIL = ["范围", "板块", "行业", "概念", "题材", "市场", "这块", "那块",
            "这边", "方向", "赛道", "领域", "里", "中", "内", "的",
            "标的", "概念股", "成分股", "成份股", "个股", "股票", "公司", "业"]

SYSTEM_HINT = [("中信", "中信行业类"), ("申万", "申万行业类"),
               ("恒生", "恒生行业类"), ("gics", "gics"),
               ("概念", "概念类"), ("题材", "概念类"), ("主题", "概念类")]

MARKET_HINT = [("港股", "港股"), ("h股", "港股"), ("香港", "港股"),
               ("美股", "美股"), ("中概", "美股"), ("纳斯达克", "美股"),
               ("a股", "中国内地股票"), ("沪深", "中国内地股票"), ("两市", "中国内地股票")]


def norm(s):
    """全角→半角、去空格、统一括号、小写"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = (s.replace("（", "(").replace("）", ")").replace("：", ":")
          .replace("，", ",").replace("、", ",").replace("％", "%"))
    s = re.sub(r"[\s　]+", "", s)
    return s.lower()


def strip_universe(phrase: str) -> dict:
    """剥壳主逻辑，返回结构化结果"""
    raw0 = norm(phrase)

    # 1) 高频别名直通
    alias_match = UNIVERSE_ALIAS.get(raw0)
    if alias_match:
        return {
            "input": phrase,
            "status": "alias",
            "keywords": [],
            "system": None,
            "market": None,
            "alias": alias_match,
            "rewrite": [],
        }

    # 2) 逐层剥壳：去 UNI_TAIL 后缀
    forms, queue = [raw0], [raw0]
    while queue:
        cur = queue.pop(0)
        for t in UNI_TAIL:
            if cur.endswith(t):
                nxt = cur[:-len(t)].strip()
                if len(nxt) >= 2 and nxt not in forms:
                    forms.append(nxt)
                    queue.append(nxt)

    # 3) 提取体系/市场提示（在剥壳前的原词上检测）
    sys_want = None
    for k, v in SYSTEM_HINT:
        if k in raw0:
            sys_want = v
            break

    mkt_want = None
    for k, v in MARKET_HINT:
        if k in raw0:
            mkt_want = v
            break

    # 5) 生成核心检索词
    #    原则：保留原始剥壳形态，只额外生成「去掉 UNIVERSE_STRIP 修饰词」的候选
    #    避免「中」这类短修饰词误伤「中信」等复合词
    kws, seen = [], set()

    # 先加入原始剥壳形态（优先级最高）
    for f in forms:
        c = f.strip()
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            kws.append(c)

    # 再加入「剥壳形态 去掉 UNIVERSE_STRIP 修饰词」的额外候选
    # 只用 >= 2 字符的修饰词，避免「中」「内」等单字误伤复合词
    for f in forms:
        for strip_word in UNIVERSE_STRIP:
            if len(strip_word) < 2:
                continue
            c = f.replace(strip_word, "").strip()
            if len(c) >= 2 and c not in seen:
                seen.add(c)
                kws.append(c)

    # 去掉体系/市场前缀的形态也加入（「中信半导体」→「半导体」）。
    # 只剥前缀、不做全局 replace：避免把「全a股票」中间的「a股」挖掉产出「全票」这类噪声词；
    # 后缀场景（「白酒概念」→「白酒」）已由 UNI_TAIL 的 endswith 剥壳覆盖
    hint_words = [k for k, _ in SYSTEM_HINT] + [k for k, _ in MARKET_HINT]
    for f in list(kws):
        c = f
        changed = True
        while changed and len(c) >= 2:
            changed = False
            for k in hint_words:
                if c.startswith(k) and len(c) - len(k) >= 2:
                    c = c[len(k):]
                    changed = True
        c = c.strip()
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            kws.append(c)

    # 6) 查改写表
    rewrite = []
    for f in forms:
        rw = UNIVERSE_REWRITE.get(norm(f), [])
        for t in rw:
            tn = norm(t)
            if tn not in seen:
                seen.add(tn)
                rewrite.append(t)

    # 7) 如果剥壳后什么都没剩下，用原词
    if not kws:
        kws = [raw0]

    return {
        "input": phrase,
        "status": "ok",
        "keywords": kws,
        "system": sys_want,
        "market": mkt_want,
        "alias": None,
        "rewrite": rewrite,
    }


def main():
    ap = argparse.ArgumentParser(
        prog="universe_strip.py",
        description="板块/市场短语剥壳：输出核心检索词 + 体系/市场提示",
    )
    ap.add_argument("phrase", nargs="+", help="待剥壳的板块/市场短语")
    args = ap.parse_args()

    results = [strip_universe(p) for p in args.phrase]
    out = results[0] if len(results) == 1 else results
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
