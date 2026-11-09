"""在限时、限内存的独立进程中提取上传文档；不执行文档内容。"""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_XML_BYTES = 8 * 1024 * 1024
PARSE_TIMEOUT_SECONDS = 25
SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm", ".pdf", ".docx"}


class DocumentError(ValueError):
    """仅携带固定错误码，不向日志或页面转发解析器的原始异常。"""


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_text(filename, data):
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentError("unsupported_type")
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError("invalid_size")
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise DocumentError("encrypted_pdf")
        if len(reader.pages) > 100:
            raise DocumentError("too_many_pages")
        parts, count = [], 0
        for page in reader.pages:
            content = page.get_contents()
            if content is not None and len(content.get_data()) > MAX_XML_BYTES:
                raise DocumentError("expanded_too_large")
            part = page.extract_text() or ""
            count += len(part)
            if count > MAX_TEXT_CHARS:
                raise DocumentError("text_too_large")
            parts.append(part)
        text = "\n\n".join(parts)
    elif suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            item = archive.getinfo("word/document.xml")
            if item.file_size > MAX_XML_BYTES:
                raise DocumentError("expanded_too_large")
            with archive.open(item) as stream:
                xml = stream.read(MAX_XML_BYTES + 1)
        if len(xml) > MAX_XML_BYTES or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise DocumentError("unsafe_document")
        root = ET.fromstring(xml)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        text = "\n".join("".join(node.text or "" for node in paragraph.iter(ns + "t"))
                         for paragraph in root.iter(ns + "p"))
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeError:
            try:
                text = data.decode("gb18030")
            except UnicodeError:
                raise DocumentError("invalid_encoding") from None
        if "\x00" in text:
            raise DocumentError("invalid_encoding")
        if suffix in {".html", ".htm"}:
            parser = _HTMLText()
            parser.feed(text)
            text = "".join(parser.parts)
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentError("text_too_large")
    text = text.strip()
    if len(text) < 8:
        raise DocumentError("no_text")
    return text


def _windows_job(process):
    import ctypes as c
    from ctypes import wintypes as w

    class Basic(c.Structure):
        _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                    ("min_working", c.c_size_t), ("max_working", c.c_size_t), ("active", w.DWORD),
                    ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

    class Counters(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class Limits(c.Structure):
        _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", c.c_size_t),
                    ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Limits()
    limits.basic.flags = 0x100 | 0x2000 | 0x2  # memory, kill on close, CPU time
    limits.basic.process_time = 15 * 10_000_000
    limits.process_memory = 512 * 1024**2
    if not job or not kernel.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, int(process._handle)):
        if job:
            kernel.CloseHandle(job)
        raise DocumentError("parser_unavailable")
    return lambda: kernel.CloseHandle(job)


def parse_isolated(filename, data):
    with tempfile.TemporaryDirectory(prefix="cybermiao-document-") as directory:
        source = Path(directory) / ("upload" + Path(filename).suffix.lower())
        output = Path(directory) / "result.json"
        source.write_bytes(data)
        command = ([sys.executable, "--parse-document"] if getattr(sys, "frozen", False)
                   else [sys.executable, str(Path(__file__).resolve())])
        process = subprocess.Popen(command + [str(source), str(output)], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        close_job = None
        try:
            if os.name == "nt":
                close_job = _windows_job(process)
            # 子进程先等待父进程建立 Job 限制，避免开始解析后才限额。
            process.communicate(b"start\n", timeout=PARSE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise DocumentError("parse_timeout") from None
        finally:
            if close_job:
                close_job()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        if not output.is_file() or output.stat().st_size > MAX_TEXT_CHARS * 6 + 1024:
            raise DocumentError("parse_failed")
        payload = json.loads(output.read_text(encoding="utf-8"))
        if process.returncode or "error" in payload:
            raise DocumentError(payload.get("error", "parse_failed"))
        return payload["text"]


def worker(source, output):
    try:
        sys.stdin.readline()
        if os.name != "nt":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
            resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        payload, code = {"text": extract_text(source, Path(source).read_bytes())}, 0
    except DocumentError as exc:
        payload, code = {"error": str(exc)}, 1
    except Exception:
        payload, code = {"error": "parse_failed"}, 1
    Path(output).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return code


if __name__ == "__main__":
    sys.exit(worker(*sys.argv[1:3]))
