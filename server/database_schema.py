"""数据库组件版本和必需列；只读运维工具不加载模型或密码哈希依赖。"""

MEMORY_SCHEMA_VERSION = 1
IDENTITY_SCHEMA_VERSION = 2
KNOWLEDGE_SCHEMA_VERSION = 1

KNOWLEDGE_COLUMNS = {
    "knowledge_documents": {"id", "user_id", "filename", "size_bytes", "sha256", "original", "status", "error_code",
                            "chunk_count", "embedding_model", "job_token", "created_at", "updated_at"},
    "knowledge_chunks": {"document_id", "user_id", "ordinal", "content", "embedding"},
}

MEMORY_COLUMNS = {
    "conversations": {"id", "user_id", "device_id", "started_at", "last_activity_at", "summarized_through_message_id"},
    "messages": {"id", "conversation_id", "role", "content", "created_at"},
    "conversation_summaries": {"id", "conversation_id", "through_message_id", "summary", "open_topics_json", "created_at"},
    "memories": {"id", "user_id", "device_id", "content", "source_conversation_id", "created_at", "updated_at", "active"},
}
IDENTITY_COLUMNS = {
    "auth_users": {"id", "username", "password_hash", "created_at", "disabled"},
    "auth_sessions": {"token_hash", "user_id", "created_at", "expires_at", "revoked_at"},
    "auth_rate_limits": {"bucket", "window_start", "hits"},
    "devices": {"id", "display_name", "owner_user_id", "created_at", "bound_at", "revoked_at"},
    "device_credentials": {"token_hash", "device_id", "created_at", "revoked_at"},
    "device_pairings": {"code_hash", "device_id", "created_at", "expires_at", "claimed_by", "claimed_at", "invalidated_at"},
}


def validate_columns(connection, required):
    for name, columns in required.items():
        actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')}
        if not columns.issubset(actual):
            raise ValueError("数据库表结构不完整，请检查备份或使用匹配的程序版本")


def schema_versions(connection):
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    return dict(connection.execute("SELECT component, version FROM schema_migrations")) if exists else {}


def validate_versions(connection):
    versions = schema_versions(connection)
    for component, supported in (("memory", MEMORY_SCHEMA_VERSION), ("identity", IDENTITY_SCHEMA_VERSION),
                                 ("knowledge", KNOWLEDGE_SCHEMA_VERSION)):
        version = versions.get(component, 0)
        if not isinstance(version, int) or not 0 <= version <= supported:
            raise ValueError("数据库组件版本高于当前程序或无效，不能降级写入，请使用匹配版本")
    return versions
