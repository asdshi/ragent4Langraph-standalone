"""
对话文件存储管理 (PostgreSQL 版)

职责：
1. 保存用户上传的原始文件（磁盘存储）
2. 记录文件元数据（PostgreSQL）
3. 管理文件生命周期（关联到对话）
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

import asyncpg


@dataclass
class ConversationFile:
    """对话文件信息"""
    file_id: str
    conversation_id: str
    filename: str
    original_name: str
    file_path: str
    file_size: int
    mime_type: str
    doc_id: Optional[str]  # ingest 后的文档 ID
    status: str  # 'pending', 'ingesting', 'ready', 'error'
    created_at: datetime
    error_message: Optional[str] = None
    file_type: Optional[str] = None      # pdf, docx, txt 等
    page_count: Optional[int] = None     # 页数
    extract_method: Optional[str] = None # markitdown, vlm_ocr, text_plain
    word_count: Optional[int] = None     # 提取字数


class ConversationFileStore:
    """
    对话文件存储管理器 (PostgreSQL)

    存储结构：
    data/
    └── uploads/
        └── {conversation_id}/
            ├── {file_id}_{filename}     # 原始文件
            └── .meta/                   # 元数据（可选）
    """

    def __init__(self, upload_dir: str = "./data/uploads") -> None:
        self._upload_dir = Path(upload_dir).resolve()
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        self._pool: Optional[asyncpg.Pool] = None
        self._dsn = os.getenv("RAGENT_POSTGRES_URL", "postgresql://postgres:postgres@localhost:5432/ragent")

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        await self._ensure_schema()
        return self._pool

    async def _ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
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
                    page_count INTEGER,
                    extract_method VARCHAR(32),
                    word_count INTEGER,
                    UNIQUE (conversation_id, file_id)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_files ON conversation_files(conversation_id, created_at)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_id ON conversation_files(file_id)"
            )

    async def save_file(
        self,
        conversation_id: str,
        file_content: bytes,
        original_filename: str,
        mime_type: str = "application/octet-stream",
    ) -> ConversationFile:
        """保存上传的文件"""
        file_id = str(uuid.uuid4())[:8]

        conv_dir = self._upload_dir / conversation_id
        conv_dir.mkdir(parents=True, exist_ok=True)

        safe_filename = Path(original_filename).name
        if not safe_filename:
            safe_filename = "unnamed_file"

        storage_filename = f"{file_id}_{safe_filename}"
        file_path = conv_dir / storage_filename

        with open(file_path, "wb") as f:
            f.write(file_content)

        file_type = Path(original_filename).suffix.lower().lstrip('.') if original_filename else None

        file_info = ConversationFile(
            file_id=file_id,
            conversation_id=conversation_id,
            filename=storage_filename,
            original_name=original_filename,
            file_path=str(file_path),
            file_size=len(file_content),
            mime_type=mime_type,
            doc_id=None,
            status="pending",
            created_at=datetime.now(),
            file_type=file_type,
        )

        await self._save_to_db(file_info)
        return file_info

    async def list_files(self, conversation_id: str) -> List[ConversationFile]:
        """列出对话的所有文件"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT file_id, conversation_id, filename, original_name,
                          file_path, file_size, mime_type, doc_id, status,
                          created_at, error_message, file_type, page_count,
                          extract_method, word_count
                   FROM conversation_files
                   WHERE conversation_id = $1
                   ORDER BY created_at DESC""",
                conversation_id,
            )
        return [self._row_to_file(row) for row in rows]

    async def get_file(self, conversation_id: str, file_id: str) -> Optional[ConversationFile]:
        """获取单个文件信息"""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT file_id, conversation_id, filename, original_name,
                          file_path, file_size, mime_type, doc_id, status,
                          created_at, error_message, file_type, page_count,
                          extract_method, word_count
                   FROM conversation_files
                   WHERE conversation_id = $1 AND file_id = $2""",
                conversation_id, file_id,
            )
        return self._row_to_file(row) if row else None

    async def update_file_status(
        self,
        conversation_id: str,
        file_id: str,
        status: str,
        doc_id: Optional[str] = None,
        error_message: Optional[str] = None,
        file_type: Optional[str] = None,
        page_count: Optional[int] = None,
        extract_method: Optional[str] = None,
        word_count: Optional[int] = None,
    ) -> None:
        """更新文件状态。None 字段不覆盖现有值。"""
        fields = ["status = $1", "doc_id = $2", "error_message = $3"]
        params: List[Any] = [status, doc_id, error_message]
        param_idx = 4

        if file_type is not None:
            fields.append(f"file_type = ${param_idx}")
            params.append(file_type)
            param_idx += 1
        if page_count is not None:
            fields.append(f"page_count = ${param_idx}")
            params.append(page_count)
            param_idx += 1
        if extract_method is not None:
            fields.append(f"extract_method = ${param_idx}")
            params.append(extract_method)
            param_idx += 1
        if word_count is not None:
            fields.append(f"word_count = ${param_idx}")
            params.append(word_count)
            param_idx += 1

        params.extend([conversation_id, file_id])

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""UPDATE conversation_files
                   SET {', '.join(fields)}
                   WHERE conversation_id = ${param_idx} AND file_id = ${param_idx + 1}""",
                *params,
            )

    async def delete_file(self, conversation_id: str, file_id: str) -> bool:
        """删除文件（磁盘 + 数据库）"""
        file_info = await self.get_file(conversation_id, file_id)
        if not file_info:
            return False

        try:
            Path(file_info.file_path).unlink(missing_ok=True)
        except Exception as e:
            print(f"[FileStore] Failed to delete file: {e}")

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM conversation_files WHERE conversation_id = $1 AND file_id = $2",
                conversation_id, file_id,
            )
        return True

    async def delete_conversation_files(self, conversation_id: str) -> int:
        """删除对话的所有文件（磁盘 + 数据库）"""
        files = await self.list_files(conversation_id)

        conv_dir = self._upload_dir / conversation_id
        if conv_dir.exists():
            try:
                shutil.rmtree(conv_dir)
            except Exception as e:
                print(f"[FileStore] Failed to delete directory: {e}")

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM conversation_files WHERE conversation_id = $1",
                conversation_id,
            )
        return len(files)

    async def _save_to_db(self, file_info: ConversationFile) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO conversation_files
                (file_id, conversation_id, filename, original_name, file_path,
                 file_size, mime_type, doc_id, status, created_at, error_message,
                 file_type, page_count, extract_method, word_count)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                ON CONFLICT (conversation_id, file_id) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    original_name = EXCLUDED.original_name,
                    file_path = EXCLUDED.file_path,
                    file_size = EXCLUDED.file_size,
                    mime_type = EXCLUDED.mime_type,
                    doc_id = EXCLUDED.doc_id,
                    status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    file_type = EXCLUDED.file_type,
                    page_count = EXCLUDED.page_count,
                    extract_method = EXCLUDED.extract_method,
                    word_count = EXCLUDED.word_count
                """,
                file_info.file_id,
                file_info.conversation_id,
                file_info.filename,
                file_info.original_name,
                file_info.file_path,
                file_info.file_size,
                file_info.mime_type,
                file_info.doc_id,
                file_info.status,
                file_info.created_at,
                file_info.error_message,
                file_info.file_type,
                file_info.page_count,
                file_info.extract_method,
                file_info.word_count,
            )

    def _row_to_file(self, row: asyncpg.Record) -> ConversationFile:
        """asyncpg Record 转 ConversationFile"""
        return ConversationFile(
            file_id=row["file_id"],
            conversation_id=row["conversation_id"],
            filename=row["filename"],
            original_name=row["original_name"],
            file_path=row["file_path"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            doc_id=row["doc_id"],
            status=row["status"],
            created_at=row["created_at"] if isinstance(row["created_at"], datetime) else datetime.fromisoformat(str(row["created_at"])),
            error_message=row["error_message"],
            file_type=row["file_type"],
            page_count=row["page_count"],
            extract_method=row["extract_method"],
            word_count=row["word_count"],
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def build_file_store() -> ConversationFileStore:
    """工厂函数"""
    return ConversationFileStore()
