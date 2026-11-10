"""资料库的账号权限、流式上传与下载；不接受客户端指定资料归属。"""

import asyncio
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import ClientDisconnect

from access_control import require_origin, require_session, require_session_write
from async_helpers import run_blocking
from document_parser import MAX_UPLOAD_BYTES, SUPPORTED_EXTENSIONS
from knowledge_store import KnowledgeLimit
from web_pages import knowledge_html

router = APIRouter()

ERRORS = {
    "invalid_filename": "文件名无效，请重新命名后上传", "unsupported_type": "不支持这种文件类型",
    "invalid_size": "文件不能为空，且不能超过 20 MiB", "user_quota": "个人资料库容量或文件数量已达上限",
    "server_quota": "服务器资料库容量已达上限，请联系管理员", "disk_space": "服务器剩余空间不足",
    "chunk_quota": "资料片段数量已达上限，请删除部分资料后重建",
    "encrypted_pdf": "请先移除 PDF 密码再上传", "too_many_pages": "PDF 不能超过 100 页",
    "expanded_too_large": "文档解压后的内容过大", "text_too_large": "文档文字不能超过 20 万字",
    "unsafe_document": "无法安全解析此文档", "invalid_encoding": "请将文本另存为 UTF-8 后上传",
    "no_text": "没有提取到足够文字，扫描件请先进行 OCR",
    "parse_timeout": "文档解析超时，请尝试拆分后上传", "parse_failed": "解析失败，请检查文件是否损坏",
    "parser_unavailable": "文档解析器暂不可用", "index_failed": "索引处理失败，请稍后重建",
    "embedding_unavailable": "语义索引暂不可用，当前可使用关键词检索",
}


async def owner(request: Request):
    if request.app.state.access.mode == "accounts":
        return (await require_session(request)).id
    return request.app.state.default_user_id


async def writer(request: Request):
    if request.app.state.access.mode == "accounts":
        return (await require_session_write(request)).id
    require_origin(request)
    return request.app.state.default_user_id


@router.get("/knowledge", response_class=HTMLResponse)
async def page(request: Request):
    try:
        await owner(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/?notice=login", status_code=303)
        raise
    return knowledge_html(request.app.state.access.mode)


@router.get("/api/knowledge")
async def documents(request: Request, user_id=Depends(owner)):
    service = request.app.state.knowledge
    result = await run_blocking(service.store.list_documents, user_id)
    for doc in result["documents"]:
        doc["message"] = ERRORS.get(doc["error_code"], "")
    return {**result, "extensions": sorted(SUPPORTED_EXTENSIONS), "embedding_model": service.embedder.model,
            "mode": request.app.state.access.mode}


@router.post("/api/knowledge/documents")
async def upload(request: Request, user_id=Depends(writer)):
    service = request.app.state.knowledge
    try:
        filename = service.store.clean_filename(unquote(request.headers.get("x-file-name", ""), errors="strict"))
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, ERRORS.get(str(exc), "文件名无效")) from None
    try:
        if "content-length" in request.headers and not 0 < int(request.headers["content-length"]) <= MAX_UPLOAD_BYTES:
            raise ValueError()
    except ValueError:
        raise HTTPException(413, ERRORS["invalid_size"]) from None
    try:
        await asyncio.wait_for(service.uploads.acquire(), timeout=.05)
    except asyncio.TimeoutError:
        raise HTTPException(429, "正在接收其他文件，请稍后重试") from None
    try:
        async def receive():
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, ERRORS["invalid_size"])
                data.extend(chunk)
            return data
        try:
            data = await asyncio.wait_for(receive(), timeout=60)
        except asyncio.TimeoutError:
            raise HTTPException(408, "上传超时，请重试") from None
        except ClientDisconnect:
            raise HTTPException(400, "上传连接中断") from None
        try:
            doc, created = await run_blocking(service.store.add_document, user_id, filename, bytes(data))
        except KnowledgeLimit as exc:
            raise HTTPException(413 if str(exc) == "invalid_size" else 409, ERRORS[str(exc)]) from None
        service.notify()
        import json
        return Response(json.dumps({"document": doc, "created": created}, ensure_ascii=False),
                        status_code=201 if created else 200, media_type="application/json")
    finally:
        service.uploads.release()


@router.get("/api/knowledge/documents/{doc_id}/download")
async def download(doc_id: str, request: Request, user_id=Depends(owner)):
    doc = await run_blocking(request.app.state.knowledge.store.download, user_id, doc_id)
    if doc is None:
        raise HTTPException(404, "资料不存在或无权访问")
    return Response(doc["original"], media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename=download; filename*=UTF-8''" + quote(doc["filename"], safe=""),
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox", "Cache-Control": "no-store"})


@router.delete("/api/knowledge/documents/{doc_id}")
async def delete(doc_id: str, request: Request, user_id=Depends(writer)):
    if not await run_blocking(request.app.state.knowledge.store.delete, user_id, doc_id):
        raise HTTPException(404, "资料不存在或无权访问")
    return {"ok": True}


@router.post("/api/knowledge/documents/{doc_id}/reindex")
async def reindex(doc_id: str, request: Request, user_id=Depends(writer)):
    service = request.app.state.knowledge
    if not await run_blocking(service.store.retry, user_id, doc_id):
        raise HTTPException(404, "资料不存在或无权访问")
    service.notify()
    return {"ok": True}


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(default="", max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=24)
    aliases: list[str] = Field(default_factory=list, max_length=16)


@router.put("/api/knowledge/documents/{doc_id}/profile")
async def profile(doc_id: str, body: ProfileBody, request: Request, user_id=Depends(writer)):
    if any(not 2 <= len(word.strip()) <= 40 for word in body.keywords + body.aliases):
        raise HTTPException(400, "关键词和同义词需为 2–40 个字符")
    if not await run_blocking(request.app.state.knowledge.store.save_profile, user_id, doc_id, body.model_dump()):
        raise HTTPException(404, "资料不存在或无权访问")
    return {"ok":True}


@router.post("/api/knowledge/search")
async def search(body: SearchBody, request: Request, user_id=Depends(writer)):
    if not body.query.strip():
        raise HTTPException(400, "请输入检索问题")
    return {"hits": await request.app.state.knowledge.search(user_id, body.query)}
