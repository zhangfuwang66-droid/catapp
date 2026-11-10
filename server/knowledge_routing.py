"""不调用模型的话题路由。短期话题由设备运行时持有，不跨账号缓存。"""

import json
import re
import time
from collections import Counter
from pathlib import Path

GROUPS = (
    ("请假", "休假", "年假", "调休", "事假", "病假", "休假申请"),
    ("OA", "办公自动化", "审批", "申请流程"),
    ("报销", "差旅", "发票", "费用申请"),
    ("入职", "离职", "人事", "交接"),
)
STOP = {"这个", "那个", "什么", "怎么", "一下", "我们", "你们", "可以", "需要", "知道", "文档", "资料", "内容", "文件", "小猫", "小喵"}
EXPLICIT = re.compile(r"知识库|资料库|我(?:的|上传的)(?:资料|文档|文件)|查(?:一下|下)?(?:资料|文档|文件)|(?:根据|查阅|查询|检索|查看).{0,6}(?:资料|文档|文件|手册)")
FOLLOWUP = re.compile(r"^(?:(?:那么|那|这个|那个|它|然后)[，,\s]*)?(?:要(?:等|多|几|不)|多(?:久|长|少)|谁|哪|怎么|需不需要|需要|能不能|是否|还有|有(?:没有|哪些)|会不会|怎么办)|^(?:继续说|然后呢)[？?。！!\s]*$")
SMALLTALK = re.compile(r"^(?:你好|嗨|早上好|晚上好|晚安|谢谢|再见|没事|好[的啊吧呀]?|嗯[嗯哼]?|你是谁|你叫什么|讲个笑话|唱首歌|我喜欢你)[呀啊呢吧嘛喵！!。，,.？?\s]*$")


def terms(values, *, limit=24):
    if not isinstance(values, list):
        return []
    result = []
    for raw in values:
        if not isinstance(raw, str):
            continue
        value = re.sub(r"\s+", " ", raw).strip()[:40]
        if 2 <= len(value) <= 40 and value.casefold() not in STOP and value.casefold() not in [v.casefold() for v in result]:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def local_profile(filename, text):
    title = Path(filename).stem
    keywords = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,30}", title + " " + text[:12000])
    for group in GROUPS:
        keywords.extend(word for word in group if word.casefold() in (title + text).casefold())
    keywords.extend(re.split(r"[\s，,。；;：:、/|_\-]+", title))
    # 重复的短语仅用作离线兜底；语义简介优先由账号模型生成，也可手动修正。
    phrases = Counter(re.findall(r"[\u3400-\u9fff]{2,8}(?=[：:\n])", text[:20000]))
    keywords.extend(value for value, _ in phrases.most_common(8))
    return {"summary": re.sub(r"\s+", " ", text).strip()[:280], "keywords": terms(keywords), "aliases": [], "profile_source": "local"}


def normalize_profile(profile, fallback=None):
    if not isinstance(profile, dict):
        return fallback or {"summary":"", "keywords":[], "aliases":[], "profile_source":"local"}
    return {"summary":str(profile.get("summary") or "").strip()[:500],
            "keywords":terms(profile.get("keywords", [])), "aliases":terms(profile.get("aliases", []), limit=16),
            "profile_source":profile.get("profile_source", "model")}


def metadata_terms(profile):
    values = terms(profile.get("keywords", []) + profile.get("aliases", []) + [Path(profile["filename"]).stem])
    expanded = list(values)
    for group in GROUPS:
        if any(word.casefold() in " ".join(values).casefold() for word in group):
            expanded.extend(group)
    return terms(expanded, limit=64)


def route_documents(query, profiles, *, previous=None, now=None):
    query = query.strip()[:2000]
    now = time.monotonic() if now is None else now
    explicit = bool(EXPLICIT.search(query))
    if not query or (SMALLTALK.fullmatch(query) and not explicit):
        return {"document_ids":[], "reason":"chat", "query":query}
    scored = []
    lower = query.casefold()
    for doc in profiles:
        matched = [term for term in metadata_terms(doc) if (re.search(r"(?<![a-z0-9])" + re.escape(term.casefold()) + r"(?![a-z0-9])", lower)
                   if term.isascii() else term.casefold() in lower)]
        # 旧文档的离线简介也可发现具体话题；至少四个连续汉字，避免「怎么」「需要」这类泛词触发。
        summary = doc.get("summary", "").casefold()
        for run in re.findall(r"[\u3400-\u9fff]+", query):
            matched.extend(run[i:i+4] for i in range(len(run)-3) if run[i:i+4] in summary
                           and not any(word in run[i:i+4] for word in ("什么", "怎么", "我们", "你们", "可以", "需要", "知道")))
        if matched:
            scored.append((sum(min(8, len(term)) for term in matched), doc["id"]))
    if scored:
        return {"document_ids":[doc_id for _, doc_id in sorted(scored, reverse=True)[:4]], "reason":"keyword", "query":query}
    if explicit:
        return {"document_ids":[doc["id"] for doc in profiles], "reason":"explicit", "query":query}
    if previous and now - previous.get("at", 0) <= 120 and previous.get("turns", 0) < 3 and len(query) <= 60 and FOLLOWUP.match(query):
        valid = {doc["id"] for doc in profiles}
        ids = [doc_id for doc_id in previous.get("document_ids", []) if doc_id in valid]
        if ids:
            return {"document_ids":ids, "reason":"followup", "query":previous["topic"][:300] + "\n追问：" + query}
    return {"document_ids":[], "reason":"unrelated", "query":query}
