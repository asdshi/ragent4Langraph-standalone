"""
PostgreSQL 数据库初始化脚本
自动创建 ragent 数据库和相关表（Agent 层统一存储）
"""

import asyncio
import os
import sys

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv()

DB_URL = os.getenv("RAGENT_POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/ragent")

# 从连接字符串提取 dbname，用于先连 postgres 默认库创建目标库
import urllib.parse
parsed = urllib.parse.urlparse(DB_URL)
dbname = parsed.path.lstrip("/") or "ragent"

CREATE_DATABASE_SQL = f"""
CREATE DATABASE {dbname} ENCODING 'UTF8';
"""

CREATE_TABLES_SQL = """
-- 对话历史归档表
CREATE TABLE IF NOT EXISTS conversation_archive (
    id SERIAL PRIMARY KEY,
    conversation_id VARCHAR(128) NOT NULL,
    role VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    message_id VARCHAR(64),
    created_at DOUBLE PRECISION NOT NULL,
    turn_id VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_archive_conversation_time ON conversation_archive(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_archive_turn ON conversation_archive(conversation_id, turn_id);

-- 长期记忆表
CREATE TABLE IF NOT EXISTS long_term_memories (
    id TEXT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(64),
    turn_id VARCHAR(64),
    fact TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    access_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ltm_user ON long_term_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_ltm_conv_turn ON long_term_memories(conversation_id, turn_id);

-- 对话列表表
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id VARCHAR(128) PRIMARY KEY,
    title VARCHAR(512) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    message_count INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    status VARCHAR(32) DEFAULT 'active',
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status);

-- 对话文件元数据表
CREATE TABLE IF NOT EXISTS conversation_files (
    id SERIAL PRIMARY KEY,
    file_id VARCHAR(64) NOT NULL,
    conversation_id VARCHAR(128) NOT NULL,
    filename VARCHAR(512) NOT NULL,
    original_name VARCHAR(512) NOT NULL,
    file_path VARCHAR(1024) NOT NULL,
    file_size BIGINT NOT NULL DEFAULT 0,
    mime_type VARCHAR(128) DEFAULT 'application/octet-stream',
    doc_id VARCHAR(128),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL,
    error_message TEXT,
    file_type VARCHAR(32),
    extract_method VARCHAR(32),
    page_count INTEGER,
    word_count INTEGER,
    UNIQUE (conversation_id, file_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_files ON conversation_files(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_file_id ON conversation_files(file_id);
"""


async def init_postgres():
    try:
        import asyncpg
    except ImportError:
        print("[ERROR] Missing asyncpg, please install: pip install asyncpg")
        sys.exit(1)

    print("=" * 60)
    print("RAG Agent PostgreSQL 数据库初始化")
    print("=" * 60)
    print(f"\n[连接信息]")
    print(f"   URL: {DB_URL.replace(parsed.password or '', '***')}")

    # 1. 连接默认 postgres 库，创建目标数据库（如果不存在）
    base_url = DB_URL.replace(f"/{dbname}", "/postgres")
    try:
        conn = await asyncpg.connect(base_url)
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", dbname
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}" ENCODING \'UTF8\'')
            print(f"   [OK] Database '{dbname}' created")
        else:
            print(f"   [OK] Database '{dbname}' already exists")
        await conn.close()
    except Exception as e:
        print(f"   [WARN] Could not create database via postgres default: {e}")
        print("   Assuming database already exists or user has direct access...")

    # 2. 连接目标数据库，创建表
    print("\n[2/2] Creating tables...")
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute(CREATE_TABLES_SQL)
        print("   [OK] All tables created")

        # 验证
        tables = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' ORDER BY table_name
            """
        )
        print(f"\n   Tables in database:")
        for row in tables:
            count = await conn.fetchval(
                f'SELECT COUNT(*) FROM "{row["table_name"]}"'
            )
            print(f"      - {row['table_name']}: {count} records")

        await conn.close()

        print("\n" + "=" * 60)
        print("[OK] PostgreSQL database initialized successfully!")
        print("=" * 60)

    except Exception as e:
        print(f"\n[ERROR] Initialization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(init_postgres())
