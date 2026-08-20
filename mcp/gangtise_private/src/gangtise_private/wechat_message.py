import os
import sys
from datetime import datetime
from io import TextIOWrapper
from typing import Any, Dict, List, Optional, Tuple

import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.append(script_dir)

from .utils import (authorized_request, FILE_DEFAULT_LIMIT, WECHAT_GROUP_CHATROOM_URL, WECHAT_GROUP_MSG_LIST_URL, check_version, format_response, get_authorization_headers, get_authorization_token, get_headers_extra, is_code_arg)

_CATEGORY_VALID = {"text", "image", "documents", "url"}
DEFAULT_ROOM_LIMIT = 50
DEFAULT_MSG_LIMIT = FILE_DEFAULT_LIMIT.get("wechat_message", 500)
IDS_FILE_HINT = "可从上述群列表复制群 ID，再通过 --groups 参数拉取群消息"


def _normalize_datetime_field(raw: Optional[str], *, end_of_day: bool) -> Optional[str]:
    """yyyy-MM-dd HH:mm:ss 原样；仅 yyyy-MM-dd 则补齐时分秒。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return s
        return s + (" 23:59:59" if end_of_day else " 00:00:00")
    return s


def _normalize_categories(items: Optional[List[str]]) -> Optional[List[str]]:
    if not items:
        return None
    out: List[str] = []
    for x in items:
        v = str(x).strip().lower()
        if v in _CATEGORY_VALID and v not in out:
            out.append(v)
    return out or None


def _normalize_tags(items: Optional[List[str]]) -> Optional[List[str]]:
    if not items:
        return None
    zh_map = {
        "路演": "roadShow",
        "调研": "research",
        "策略会": "strategyMeeting",
        "会议纪要": "meetingSummary",
        "行业点评": "industryComment",
        "公司点评": "companyComment",
        "业绩点评": "earningsReview",
    }
    lower_map = {
        "roadshow": "roadShow",
        "research": "research",
        "strategymeeting": "strategyMeeting",
        "meetingsummary": "meetingSummary",
        "industrycomment": "industryComment",
        "companycomment": "companyComment",
        "earningsreview": "earningsReview",
    }
    valid_exact = set(lower_map.values())
    out: List[str] = []
    for x in items:
        s = str(x).strip()
        if not s:
            continue
        if s in zh_map:
            v = zh_map[s]
        elif s in valid_exact:
            v = s
        else:
            v = lower_map.get(s.lower())
        if v and v not in out:
            out.append(v)
    return out or None


def _parse_str_list(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    items = [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]
    return items or None


def _check_api_ok(body: Dict[str, Any]) -> bool:
    code = body.get("code")
    return (code in (200, "000000") or code == "0") and body.get("status") is True


def _format_msg_rows(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        tags = row.get("tagList") or []
        tag_parts: List[str] = []
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, dict):
                    code = t.get("tagCode") or ""
                    name = t.get("tagName") or ""
                    if name and code:
                        tag_parts.append(f"{name}({code})")
                    elif name:
                        tag_parts.append(name)
                    elif code:
                        tag_parts.append(code)
        tag_str = "、".join(tag_parts)
        url = row.get("contentUrl") or row.get("url") or ""
        content = row.get("content") or ""
        quote = row.get("quoteMsg") if isinstance(row.get("quoteMsg"), dict) else {}
        quote_id = str(quote.get("quoteMsgId") or "") if quote else ""
        quote_content = str(quote.get("quoteContent") or "") if quote else ""
        quote_url = str(quote.get("quoteUrl") or "") if quote else ""

        item = {
            "消息全文": content,
            "消息时间": row.get("msgTime") or "",
            "链接": url,
            "群名称": row.get("wechatGroupName") or "",
            "群ID": row.get("wechatGroupId") or "",
            "发言人": row.get("speakerName") or "",
            "消息类型": row.get("category") or "",
            "标签": tag_str,
            "引用消息ID": quote_id,
            "引用消息内容": quote_content,
            "引用消息链接": quote_url,
            "类型": "群消息",
            "类型ID": str(row.get("msgId") or ""),
        }
        out.append(item)
    return out


def _format_chatroom_rows(rows: List[dict]) -> List[dict]:
    out: List[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("chatroomName") or row.get("chatRoomName") or ""
        cid = row.get("chatroomId") or row.get("chatRoomId") or ""
        item = {
            "标题": name or cid,
            "文件时间": "",
            "群名称": name,
            "群ID": cid,
            "类型": "微信群查询",
            "类型ID": str(cid),
        }
        out.append(item)
    return out


def _post_json(url: str, headers: dict, payload: dict) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        r = authorized_request("POST", url, headers=headers, json=payload, timeout=300)
        if r.status_code != 200:
            return None, r.text
        return r.json(), None
    except Exception as e:
        return None, str(e)


def _filter_chatrooms_by_keyword(rows: List[dict], keyword: str) -> List[dict]:
    kw = (keyword or "").strip()
    if not kw:
        return rows
    out = []
    for row in rows:
        name = str(row.get("群名称") or row.get("标题") or "")
        gid = str(row.get("群ID") or "")
        if kw in name or kw in gid:
            out.append(row)
    return out


def _fetch_chatrooms(
    headers: dict,
    room_name: Optional[str] = None,
    page_from: int = 0,
    page_size: int = 20,
) -> Tuple[List[dict], Optional[str]]:
    sz = min(50, max(1, int(page_size)))
    offset = max(0, int(page_from))
    payload: Dict[str, Any] = {"from": offset, "size": sz}
    if room_name and str(room_name).strip():
        payload["roomName"] = str(room_name).strip()

    body, err = _post_json(WECHAT_GROUP_CHATROOM_URL, headers, payload)
    if err:
        return [], err
    if not body or not _check_api_ok(body):
        return [], (body or {}).get("msg", "微信群查询失败")

    data_block = body.get("data") or {}
    rooms = data_block.get("list") or data_block.get("chatRoomList") or []
    if not isinstance(rooms, list):
        rooms = []
    return _format_chatroom_rows(rooms), None


def _extract_group_ids(rows: List[dict]) -> List[str]:
    ids: List[str] = []
    for row in rows:
        gid = str(row.get("群ID") or "").strip()
        if gid and gid not in ids:
            ids.append(gid)
    return ids


def _fetch_messages(
    headers: dict,
    *,
    page_from: int = 0,
    page_size: int = 20,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    keyword: Optional[str] = None,
    wechat_group_id_list: Optional[List[str]] = None,
    industry_id_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    tag_list: Optional[List[str]] = None,
    max_total: int = DEFAULT_MSG_LIMIT,
) -> Tuple[List[dict], Optional[str]]:
    cap_total = max(1, int(max_total))
    sz = min(50, max(1, int(page_size)))
    offset = max(0, int(page_from))
    aggregated: List[dict] = []
    part_err: Optional[str] = None

    st = _normalize_datetime_field(start_time, end_of_day=False)
    et = _normalize_datetime_field(end_time, end_of_day=True)
    cats = _normalize_categories(category_list)
    tags = _normalize_tags(tag_list)

    while len(aggregated) < cap_total:
        chunk = min(sz, cap_total - len(aggregated))
        payload: Dict[str, Any] = {"from": offset, "size": chunk}
        if st:
            payload["startTime"] = st
        if et:
            payload["endTime"] = et
        if keyword:
            payload["keyword"] = str(keyword).strip()
        if wechat_group_id_list:
            payload["wechatGroupIdList"] = wechat_group_id_list
        if industry_id_list:
            payload["industryIdList"] = industry_id_list
        if cats:
            payload["categoryList"] = cats
        if tags:
            payload["tagList"] = tags

        body, err = _post_json(WECHAT_GROUP_MSG_LIST_URL, headers, payload)
        if err:
            if not aggregated:
                return [], err
            part_err = err
            break
        if not body or not _check_api_ok(body):
            msg = (body or {}).get("msg", "请求失败")
            if not aggregated:
                return [], msg
            part_err = msg
            break

        data_block = body.get("data") or {}
        raw_list = data_block.get("list") or []
        if not isinstance(raw_list, list):
            raw_list = []

        aggregated.extend(_format_msg_rows(raw_list))

        if len(raw_list) < chunk:
            break
        offset += len(raw_list)

    return aggregated, part_err


def _auth_headers(authorization: Optional[str] = None) -> Tuple[Optional[dict], Optional[str]]:
    auth = authorization or get_authorization_token()
    if not auth:
        return None, "未配置 gangtise 授权，无法调用 open 接口"
    headers = {**get_headers_extra(), "Authorization": auth}
    return headers, None


def _exact_match_chatrooms(keyword: str, rows: List[dict]) -> List[dict]:
    kw = keyword.strip()
    return [
        r
        for r in rows
        if str(r.get("群名称") or r.get("标题") or "").strip() == kw
        or str(r.get("群ID") or "").strip() == kw
    ]


def _resolve_groups_from_arg(
    raw: str,
    limit: int,
    *,
    page_from: int = 0,
    page_size: int = 20,
) -> Tuple[Optional[List[str]], Optional[str]]:
    if is_code_arg(raw):
        return _parse_str_list(raw) or [], None

    headers, err = _auth_headers()
    if err:
        return None, err

    resolved: List[str] = []
    search_outputs: List[str] = []
    for token in _parse_str_list(raw) or []:
        rows, fetch_err = _fetch_chatrooms(headers, token, page_from, page_size)
        if fetch_err:
            return None, fetch_err
        rows = _filter_chatrooms_by_keyword(rows, token)
        if limit > 0:
            rows = rows[: int(limit)]
        if not rows:
            return None, f"未找到与「{token}」相关的微信群"
        exact = _exact_match_chatrooms(token, rows)
        if len(exact) == 1:
            gid = str(exact[0].get("群ID") or "").strip()
            if gid:
                resolved.append(gid)
            continue
        if len(exact) > 1:
            prefix = f"「{token}」存在 {len(exact)} 条完全匹配，请指定更精确的群名或 ID：\n\n"
        else:
            prefix = f"未找到与「{token}」完全匹配的微信群，以下为候选结果：\n\n"
        search_outputs.append(
            prefix
            + wechat_message_search(
                room_name=token,
                room_filter=token,
                limit=limit,
                page_from=page_from,
                page_size=page_size,
                append_file_hint=False,
            )
        )

    if search_outputs:
        return None, "\n\n".join(search_outputs)
    return resolved, None


def wechat_message_search(
    room_name: str = "",
    room_filter: str = "",
    limit: int = DEFAULT_ROOM_LIMIT,
    page_from: int = 0,
    page_size: int = 20,
    output: Optional[str] = None,
    authorization: Optional[str] = None,
    append_file_hint: bool = False,
    **kwargs,
):
    headers, err = _auth_headers(authorization)
    if err:
        return format_response({"state": "error", "message": err, "data": []}, "wechat_message", output=output)

    api_room = (room_name or "").strip()
    rows, err = _fetch_chatrooms(headers, api_room or None, page_from, page_size)
    if err:
        return format_response({"state": "error", "message": err, "data": []}, "wechat_message", output=output)

    rows = _filter_chatrooms_by_keyword(rows, room_filter or api_room)
    if limit > 0:
        rows = rows[: int(limit)]

    if not rows:
        hint = f"与「{room_filter or api_room}」匹配的" if (room_filter or api_room) else ""
        return format_response(
            {
                "state": "error",
                "message": f"未找到{hint}微信群",
                "data": [],
            },
            "wechat_message",
            output=output,
        )

    parts = [
        {
            "data": rows,
            "module": "wechat_chatroom",
            "type": "files",
        }
    ]
    msg = f"已找到 {len(rows)} 个微信群"
    if api_room:
        msg += f"（群名称：{api_room}）"
    if room_filter and room_filter != api_room:
        msg += f"（过滤：{room_filter}）"
    if append_file_hint:
        msg += f"；{IDS_FILE_HINT}"
    return format_response(
        {"state": "success", "message": msg, "data": parts},
        "wechat_message",
        output=output,
    )


def wechat_message_get(
    page_from: int = 0,
    page_size: int = 20,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    keyword: Optional[str] = None,
    wechat_group_id_list: Optional[List[str]] = None,
    industry_id_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    tag_list: Optional[List[str]] = None,
    max_total: Optional[int] = None,
    output: Optional[str] = None,
    authorization: Optional[str] = None,
    **kwargs,
):
    headers, err = _auth_headers(authorization)
    if err:
        return format_response({"state": "error", "message": err, "data": []}, "wechat_message", output=output)

    cap = int(max_total) if max_total is not None else DEFAULT_MSG_LIMIT
    aggregated, part_err = _fetch_messages(
        headers,
        page_from=page_from,
        page_size=page_size,
        start_time=start_time,
        end_time=end_time,
        keyword=keyword,
        wechat_group_id_list=wechat_group_id_list,
        industry_id_list=industry_id_list,
        category_list=category_list,
        tag_list=tag_list,
        max_total=cap,
    )

    extra = f"\n未完整拉取：{part_err}" if part_err else ""
    if not aggregated:
        return format_response(
            {
                "state": "error",
                "message": "未找到群消息，请检查权限（需已绑定并激活群消息助理并入群）与筛选条件",
                "data": [],
            },
            "wechat_message",
            output=output,
        )

    parts = [
        {
            "data": aggregated,
            "module": "wechat_message",
            "type": "files",
        }
    ]
    gid_hint = f"{len(wechat_group_id_list)} 个群" if wechat_group_id_list else "全部可访问群"
    msg = f"已获取{gid_hint}内 {len(aggregated)} 条群消息"
    if keyword:
        msg += f"（关键词：{keyword.strip()}）"
    return format_response(
        {"state": "success", "message": msg, "data": parts},
        "wechat_message",
        output=output,
        additional_message=extra.strip(),
    )


def wechat_message_finder(
    room_name: str = "",
    keyword: Optional[str] = None,
    wechat_group_id_list: Optional[List[str]] = None,
    page_from: int = 0,
    page_size: int = 20,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    industry_id_list: Optional[List[str]] = None,
    category_list: Optional[List[str]] = None,
    tag_list: Optional[List[str]] = None,
    limit: Optional[int] = None,
    output: Optional[str] = None,
    authorization: Optional[str] = None,
):
    """检索微信群列表，或在提供 wechat_group_id_list 时拉取群消息。"""
    room_limit = int(limit) if limit is not None else DEFAULT_ROOM_LIMIT
    msg_limit = int(limit) if limit is not None else DEFAULT_MSG_LIMIT
    common = dict(
        page_from=page_from,
        page_size=page_size,
        start_time=start_time,
        end_time=end_time,
        industry_id_list=industry_id_list,
        category_list=category_list,
        tag_list=tag_list,
        output=output,
        authorization=authorization,
    )

    group_ids: Optional[List[str]] = None
    if wechat_group_id_list:
        if isinstance(wechat_group_id_list, str):
            raw_groups = str(wechat_group_id_list).strip()
            if raw_groups:
                resolved, err = _resolve_groups_from_arg(
                    raw_groups,
                    room_limit,
                    page_from=page_from,
                    page_size=page_size,
                )
                if err:
                    return err
                group_ids = resolved
        else:
            tokens = [str(x).strip() for x in wechat_group_id_list if str(x).strip()]
            if tokens:
                resolved, err = _resolve_groups_from_arg(
                    ",".join(tokens),
                    room_limit,
                    page_from=page_from,
                    page_size=page_size,
                )
                if err:
                    return err
                group_ids = resolved

    if group_ids:
        return wechat_message_get(
            keyword=keyword,
            wechat_group_id_list=group_ids,
            max_total=msg_limit,
            **common,
        )

    api_room = (room_name or "").strip()
    room_filter = keyword if api_room and keyword else ""
    return wechat_message_search(
        room_name=api_room or (keyword or ""),
        room_filter=room_filter,
        limit=room_limit,
        append_file_hint=True,
        **common,
    )


def main():
    import argparse

    try:
        if not check_version():
            print("[WARNING] 存在 Gangtise private 版本更新，请与用户确认是否更新\n")
    except Exception:
        print("[WARNING] 检查 Gangtise private 版本失败\n")

    parser = argparse.ArgumentParser(
        description="微信群消息：检索群 ID 或按群 ID/条件拉取消息（open-vault wechatgroupmsg）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["search", "get"],
        default="search",
        help="search=查群 ID；get=拉群消息；提供 --groups 或 -m get 时进入 get",
    )
    parser.add_argument(
        "-n",
        "--room-name",
        default="",
        help="群名称，逗号分隔（search 模式传给 chatroom 接口）",
    )
    parser.add_argument(
        "-k",
        "--keyword",
        default="",
        help="search：对群列表名称/ID 过滤；get：消息内容关键词",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=None,
        help=f"search：最多返回群数量（默认 {DEFAULT_ROOM_LIMIT}）；get：最多消息条数（默认 {DEFAULT_MSG_LIMIT}）",
    )
    parser.add_argument("--page-from", type=int, default=0, help="起始偏移 from")
    parser.add_argument("--page-size", type=int, default=20, help="单页 size，最大 50")
    parser.add_argument("-st", "--start-time", default=None, help="消息开始时间（get 模式）")
    parser.add_argument("-et", "--end-time", default=None, help="消息结束时间（get 模式）")
    parser.add_argument(
        "--groups",
        default=None,
        help="群 ID 或群名，逗号分隔；纯字母数字视为 ID 直接 get，否则 search，唯一完全匹配时自动 get",
    )
    parser.add_argument("--industries", default=None, help="行业 ID，逗号分隔（get 模式）")
    parser.add_argument(
        "--categories",
        default=None,
        help="消息类型：text,image,documents,url（get 模式）",
    )
    parser.add_argument("--tags", default=None, help="标签，逗号分隔或中文别名（get 模式）")
    parser.add_argument("-o", "--output", default=None, help="保存路径（需 GTS_SAVE_FILE=True）")

    args = parser.parse_args()
    room_name = (args.room_name or "").strip()
    keyword = (args.keyword or "").strip() or None

    groups_arg: Optional[List[str]] = None
    if args.groups and str(args.groups).strip():
        groups_arg = _parse_str_list(args.groups)

    print(
        wechat_message_finder(
            room_name=room_name,
            keyword=keyword,
            wechat_group_id_list=groups_arg,
            page_from=args.page_from,
            page_size=args.page_size,
            start_time=args.start_time,
            end_time=args.end_time,
            industry_id_list=_parse_str_list(args.industries),
            category_list=_parse_str_list(args.categories),
            tag_list=_parse_str_list(args.tags),
            limit=args.limit,
            output=args.output,
        )
    )


if __name__ == "__main__":
    encoding = "utf-8"
    sys.stdout = TextIOWrapper(sys.stdout.buffer, encoding=encoding, errors="ignore")
    main()
