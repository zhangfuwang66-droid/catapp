"""按主人隔离的文档、原文件和片段；与聊天使用同一 SQLite 事务及备份。"""

from array import array
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import time
import uuid

from database_schema import KNOWLEDGE_COLUMNS, KNOWLEDGE_PROFILE_COLUMNS, KNOWLEDGE_SCHEMA_VERSION, validate_columns, validate_versions
from knowledge_routing import normalize_profile, local_profile
from document_parser import MAX_UPLOAD_BYTES, SUPPORTED_EXTENSIONS

CHUNK_CHARS = 900
CHUNK_OVERLAP = 120
PUBLIC_FIELDS = "id,filename,size_bytes,status,error_code,chunk_count,embedding_model,created_at,updated_at"
PUBLIC_FIELDS += ",summary,keywords_json,aliases_json,profile_source,profile_version"


def public_document(row):
    result = dict(row)
    result["keywords"] = json.loads(result.pop("keywords_json", "[]"))
    result["aliases"] = json.loads(result.pop("aliases_json", "[]"))
    return result


class KnowledgeLimit(ValueError):
    pass


@dataclass(frozen=True)
class KnowledgeLimits:
    max_documents: int = 40
    user_bytes: int = 100 * 1024**2
    total_bytes: int = 1024 * 1024**2
    user_chunks: int = 3000
    total_chunks: int = 30000
    min_free_bytes: int = 512 * 1024**2


def split_text(text):
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start + CHUNK_CHARS // 2, end),
                           text.rfind("。", start + CHUNK_CHARS // 2, end))
            if boundary >= 0:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def tokenize(text):
    text = text.lower()
    words = re.findall(r"[a-z0-9_]+", text)
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.extend(run[index:index + 2] for index in range(max(1, len(run) - 1)))
    return words


def pack_vector(vector):
    if not vector or len(vector) > 4096 or not all(math.isfinite(value) for value in vector):
        raise ValueError("invalid_embedding")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("invalid_embedding")
    packed = array("f", (value / norm for value in vector))
    import sys
    if sys.byteorder != "little":
        packed.byteswap()
    return packed.tobytes()


def unpack_vector(data):
    values = array("f")
    values.frombytes(data)
    import sys
    if sys.byteorder != "little":
        values.byteswap()
    return values


class KnowledgeStore:
    def __init__(self, path, *, limits=None):
        self.path = Path(path).resolve()
        self.limits = limits or KnowledgeLimits()
        self._routing_cache = {}
        with self.connection(write=True) as db:
            version = validate_versions(db).get("knowledge", 0)
            if not version:
                existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if existing.intersection(KNOWLEDGE_COLUMNS):
                    raise ValueError("知识库存在未登记的表，拒绝覆盖")
                for statement in (
                    """CREATE TABLE knowledge_documents (
                        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, filename TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL CHECK(size_bytes > 0), sha256 TEXT NOT NULL,
                        original BLOB NOT NULL, status TEXT NOT NULL
                        CHECK(status IN ('queued','processing','ready','keyword','failed')),
                        error_code TEXT NOT NULL DEFAULT '', chunk_count INTEGER NOT NULL DEFAULT 0,
                        embedding_model TEXT NOT NULL DEFAULT '', job_token TEXT,
                        created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        UNIQUE(id,user_id), UNIQUE(user_id,sha256))""",
                    "CREATE INDEX idx_knowledge_owner ON knowledge_documents(user_id,created_at DESC)",
                    "CREATE INDEX idx_knowledge_jobs ON knowledge_documents(status,created_at)",
                    """CREATE TABLE knowledge_chunks (
                        document_id TEXT NOT NULL, user_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                        content TEXT NOT NULL, embedding BLOB,
                        PRIMARY KEY(document_id,ordinal),
                        FOREIGN KEY(document_id,user_id) REFERENCES knowledge_documents(id,user_id) ON DELETE CASCADE)""",
                    "CREATE INDEX idx_knowledge_chunks_owner ON knowledge_chunks(user_id)",
                ):
                    db.execute(statement)
                db.execute("INSERT INTO schema_migrations(component,version) VALUES('knowledge',1)")
            if version < 2:
                for column, definition in (("summary", "TEXT NOT NULL DEFAULT ''"), ("keywords_json", "TEXT NOT NULL DEFAULT '[]'"),
                        ("aliases_json", "TEXT NOT NULL DEFAULT '[]'"), ("profile_source", "TEXT NOT NULL DEFAULT 'local'"),
                        ("profile_version", "INTEGER NOT NULL DEFAULT 0")):
                    db.execute(f"ALTER TABLE knowledge_documents ADD COLUMN {column} {definition}")
                db.execute("UPDATE schema_migrations SET version=2 WHERE component='knowledge'")
            validate_columns(db, KNOWLEDGE_COLUMNS)
            validate_columns(db, KNOWLEDGE_PROFILE_COLUMNS)

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def list_documents(self, user_id):
        with self.connection() as db:
            docs = [public_document(row) for row in db.execute(f"SELECT {PUBLIC_FIELDS} FROM knowledge_documents WHERE user_id=? ORDER BY created_at DESC", (user_id,))]
        return {"documents": docs, "usage": {"documents": len(docs), "bytes": sum(doc["size_bytes"] for doc in docs),
                "chunks": sum(doc["chunk_count"] for doc in docs)},
                "limits": {"documents": self.limits.max_documents, "bytes": self.limits.user_bytes,
                           "file_bytes": MAX_UPLOAD_BYTES, "chunks": self.limits.user_chunks}}

    @staticmethod
    def clean_filename(filename):
        # 不将用户文件名用于磁盘路径；同时拒绝控制字符及双向文字控制字符。
        if not isinstance(filename, str) or not 1 <= len(filename) <= 180 or any(ord(char) < 32 or char in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for char in filename):
            raise ValueError("invalid_filename")
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError("unsupported_type")
        return filename

    def add_document(self, user_id, filename, data):
        filename = self.clean_filename(filename)
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise KnowledgeLimit("invalid_size")
        digest = hashlib.sha256(data).hexdigest()
        with self.connection(write=True) as db:
            existing = db.execute(f"SELECT {PUBLIC_FIELDS} FROM knowledge_documents WHERE user_id=? AND sha256=?", (user_id, digest)).fetchone()
            if existing:
                return public_document(existing), False
            count, used = db.execute("SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM knowledge_documents WHERE user_id=?", (user_id,)).fetchone()
            total = db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM knowledge_documents").fetchone()[0]
            if count >= self.limits.max_documents or used + len(data) > self.limits.user_bytes:
                raise KnowledgeLimit("user_quota")
            if total + len(data) > self.limits.total_bytes:
                raise KnowledgeLimit("server_quota")
            if shutil.disk_usage(self.path.parent).free < self.limits.min_free_bytes + len(data) * 3 + 8 * 1024**2:
                raise KnowledgeLimit("disk_space")
            doc_id, now = uuid.uuid4().hex, time.time()
            db.execute("""INSERT INTO knowledge_documents(id,user_id,filename,size_bytes,sha256,original,status,created_at,updated_at)
                          VALUES(?,?,?,?,?,?,'queued',?,?)""", (doc_id, user_id, filename, len(data), digest, data, now, now))
            return public_document(db.execute(f"SELECT {PUBLIC_FIELDS} FROM knowledge_documents WHERE id=? AND user_id=?", (doc_id, user_id)).fetchone()), True

    def download(self, user_id, doc_id):
        with self.connection() as db:
            row = db.execute("SELECT filename,original FROM knowledge_documents WHERE id=? AND user_id=?", (doc_id, user_id)).fetchone()
            return dict(row) if row else None

    def delete(self, user_id, doc_id):
        with self.connection(write=True) as db:
            return bool(db.execute("DELETE FROM knowledge_documents WHERE id=? AND user_id=?", (doc_id, user_id)).rowcount)

    def retry(self, user_id, doc_id):
        with self.connection(write=True) as db:
            row = db.execute("SELECT status FROM knowledge_documents WHERE id=? AND user_id=?", (doc_id, user_id)).fetchone()
            if row is None:
                return False
            if row[0] not in {"queued", "processing"}:
                db.execute("DELETE FROM knowledge_chunks WHERE document_id=? AND user_id=?", (doc_id, user_id))
                db.execute("""UPDATE knowledge_documents SET status='queued',error_code='',chunk_count=0,
                              embedding_model='',job_token=NULL,updated_at=? WHERE id=? AND user_id=?""", (time.time(), doc_id, user_id))
            return True

    def recover_jobs(self):
        with self.connection(write=True) as db:
            db.execute("UPDATE knowledge_documents SET status='queued',job_token=NULL WHERE status='processing'")

    def claim_next(self):
        with self.connection(write=True) as db:
            row = db.execute("SELECT id,user_id,filename,original FROM knowledge_documents WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE knowledge_documents SET status='processing',job_token=?,updated_at=? WHERE id=?", (token, time.time(), row["id"]))
            return {**dict(row), "job_token": token}

    def finish(self, job, chunks, embeddings=None, *, model="", error="", profile=None):
        if embeddings is not None and len(chunks) != len(embeddings):
            raise ValueError("invalid_embedding")
        with self.connection(write=True) as db:
            owner = (job["id"], job["user_id"], job["job_token"])
            if not db.execute("SELECT 1 FROM knowledge_documents WHERE id=? AND user_id=? AND job_token=? AND status='processing'", owner).fetchone():
                return False
            own = db.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE user_id=?", (job["user_id"],)).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
            if own + len(chunks) > self.limits.user_chunks or total + len(chunks) > self.limits.total_chunks:
                chunks, embeddings, error = [], None, "chunk_quota"
            status = "failed" if not chunks else "ready" if embeddings is not None else "keyword"
            db.executemany("INSERT INTO knowledge_chunks(document_id,user_id,ordinal,content,embedding) VALUES(?,?,?,?,?)",
                           ((job["id"], job["user_id"], index + 1, text, pack_vector(embeddings[index]) if embeddings is not None else None)
                            for index, text in enumerate(chunks)))
            db.execute("""UPDATE knowledge_documents SET status=?,error_code=?,chunk_count=?,embedding_model=?,
                          job_token=NULL,updated_at=? WHERE id=? AND user_id=? AND job_token=?""",
                       (status, error, len(chunks), model if embeddings is not None else "", time.time(), *owner))
            if profile and chunks:
                self._save_profile(db, job["user_id"], job["id"], profile, overwrite_manual=False)
            return True

    @staticmethod
    def _save_profile(db, user_id, doc_id, profile, *, overwrite_manual=True):
        profile = normalize_profile(profile)
        suffix = "" if overwrite_manual else " AND profile_source<>'manual'"
        return bool(db.execute("""UPDATE knowledge_documents SET summary=?,keywords_json=?,aliases_json=?,
            profile_source=?,profile_version=profile_version+1,updated_at=? WHERE id=? AND user_id=?""" + suffix,
            (profile["summary"], json.dumps(profile["keywords"], ensure_ascii=False), json.dumps(profile["aliases"], ensure_ascii=False),
             profile["profile_source"], time.time(), doc_id, user_id)).rowcount)

    def save_profile(self, user_id, doc_id, profile):
        with self.connection(write=True) as db:
            return self._save_profile(db, user_id, doc_id, {**profile, "profile_source":"manual"})

    def profiles(self, user_id):
        with self.connection(write=True) as db:
            rows = db.execute(f"SELECT {PUBLIC_FIELDS} FROM knowledge_documents WHERE user_id=? AND status IN ('ready','keyword')", (user_id,)).fetchall()
            for row in rows:
                if not row["profile_version"]:
                    text = "\n".join(r[0] for r in db.execute("SELECT content FROM knowledge_chunks WHERE user_id=? AND document_id=? ORDER BY ordinal LIMIT 18", (user_id,row["id"])))
                    self._save_profile(db, user_id, row["id"], local_profile(row["filename"], text), overwrite_manual=False)
            result = [public_document(row) for row in db.execute(f"SELECT {PUBLIC_FIELDS} FROM knowledge_documents WHERE user_id=? AND status IN ('ready','keyword')", (user_id,))]
            for profile in result:
                if profile['profile_source'] != 'local':
                    continue
                key = (user_id, profile['id'], profile['updated_at'], profile['profile_version'])
                if key not in self._routing_cache:
                    text = '\n'.join(row[0] for row in db.execute('SELECT content FROM knowledge_chunks WHERE user_id=? AND document_id=? ORDER BY ordinal', (user_id, profile['id'])))
                    headings = re.findall(r'(?m)^\s{0,3}#{1,6}\s+(.+)$', text)
                    values = [re.sub(r'^[\d一二三四五六七八九十.、（()）\s]+', '', value).strip('* #')[:40] for value in headings]
                    if len(self._routing_cache) >= 128:
                        self._routing_cache.clear()
                    self._routing_cache[key] = list(dict.fromkeys(value for value in values if 2 <= len(value) <= 40))[:512]
                profile['routing_terms'] = self._routing_cache[key]
            return result

    def has_documents(self, user_id):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM knowledge_documents WHERE user_id=? AND status IN ('ready','keyword') LIMIT 1", (user_id,)).fetchone())

    def has_embeddings(self, user_id, model, document_ids=None):
        if document_ids is not None and not document_ids:
            return False
        clause = "" if document_ids is None else " AND id IN (" + ",".join("?" for _ in document_ids) + ")"
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM knowledge_documents WHERE user_id=? AND status='ready' AND embedding_model=?" + clause + " LIMIT 1",
                                   (user_id, model, *(document_ids or []))).fetchone())

    def search(self, user_id, query, *, vector=None, model="", top_k=4, document_ids=None):
        if document_ids is not None and not document_ids:
            return []
        clause = "" if document_ids is None else " AND d.id IN (" + ",".join("?" for _ in document_ids) + ")"
        # 不设跨账号缓存；SQL 在加载正文和向量之前就限制归属。
        with self.connection() as db:
            rows = db.execute("""SELECT c.document_id,c.ordinal,c.content,c.embedding,d.filename,d.embedding_model
                FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id AND d.user_id=c.user_id
                WHERE c.user_id=? AND d.status IN ('ready','keyword')""" + clause, (user_id, *(document_ids or []))).fetchall()
        if not rows:
            return []
        tokens = set(tokenize(query))
        documents = [Counter(tokenize(row["filename"] + " " + row["content"])) for row in rows]
        frequencies = Counter(token for document in documents for token in tokens if token in document)
        average_length = sum(sum(document.values()) for document in documents) / len(documents) or 1
        query_vector = unpack_vector(pack_vector(vector)) if vector is not None else None
        hits = []
        for row, words in zip(rows, documents):
            length = sum(words.values())
            lexical = sum(math.log(1 + (len(rows) - frequencies[token] + .5) / (frequencies[token] + .5))
                          * (words[token] * 2.2) / (words[token] + 1.2 * (.25 + .75 * length / average_length))
                          for token in tokens if words[token])
            dense = 0.
            if query_vector is not None and row["embedding"] and row["embedding_model"] == model:
                stored = unpack_vector(row["embedding"])
                if len(stored) == len(query_vector):
                    dense = sum(a * b for a, b in zip(stored, query_vector))
            if lexical <= .25 and dense < .4:
                continue
            score = .55 * max(0, dense) + .45 * lexical / (lexical + 2)
            hits.append({"document_id": row["document_id"], "filename": row["filename"], "ordinal": row["ordinal"],
                         "text": row["content"], "score": round(score, 5), "mode": "hybrid" if dense else "keyword"})
        return sorted(hits, key=lambda hit: hit["score"], reverse=True)[:max(1, min(top_k, 6))]
