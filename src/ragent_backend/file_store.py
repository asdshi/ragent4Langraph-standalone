"""
对话文件存储管理

职责：
1. 保存用户上传的原始文件（磁盘存储）
2. 记录文件元数据（SQLite / MySQL）
3. 管理文件生命周期（关联到对话）
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any


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


class ConversationFileStore:
    """
    对话文件存储管理器
    
    存储结构：
    data/
    └── uploads/
        └── {conversation_id}/
            ├── {file_id}_{filename}     # 原始文件
            └── .meta/                   # 元数据（可选）
    """
    
    def __init__(self, upload_dir: str = "./data/uploads", db_path: str = "./data/db/file_store.db") -> None:
        self._upload_dir = Path(upload_dir).resolve()
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        
        # 数据库配置：优先 MySQL，否则 SQLite
        self._mysql_enabled = bool(os.getenv("RAGENT_MYSQL_HOST"))
        self._sqlite_path = Path(db_path).resolve()
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        
        if self._mysql_enabled:
            self._mysql_host = os.getenv("RAGENT_MYSQL_HOST", "127.0.0.1")
            self._mysql_port = int(os.getenv("RAGENT_MYSQL_PORT", "3306"))
            self._mysql_user = os.getenv("RAGENT_MYSQL_USER", "root")
            self._mysql_password = os.getenv("RAGENT_MYSQL_PASSWORD", "")
            self._mysql_database = os.getenv("RAGENT_MYSQL_DATABASE", "ragent")
        
        self._mysql_pool = None
        self._ensure_sqlite_schema()
    
    def _get_db_connection(self):
        """获取 SQLite 连接"""
        conn = sqlite3.connect(str(self._sqlite_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_sqlite_schema(self) -> None:
        """确保 SQLite 表结构存在"""
        with self._get_db_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    mime_type TEXT DEFAULT 'application/octet-stream',
                    doc_id TEXT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    error_message TEXT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_files 
                ON conversation_files (conversation_id, created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_file_id 
                ON conversation_files (file_id)
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uk_conv_file 
                ON conversation_files (conversation_id, file_id)
            """)
    
    async def save_file(
        self,
        conversation_id: str,
        file_content: bytes,
        original_filename: str,
        mime_type: str = "application/octet-stream",
    ) -> ConversationFile:
        """
        保存上传的文件
        
        Args:
            conversation_id: 对话 ID
            file_content: 文件二进制内容
            original_filename: 原始文件名
            mime_type: MIME 类型
            
        Returns:
            ConversationFile 对象
        """
        file_id = str(uuid.uuid4())[:8]
        
        # 创建对话目录
        conv_dir = self._upload_dir / conversation_id
        conv_dir.mkdir(parents=True, exist_ok=True)
        
        # 安全的文件名
        safe_filename = Path(original_filename).name
        if not safe_filename:
            safe_filename = "unnamed_file"
        
        # 存储路径
        storage_filename = f"{file_id}_{safe_filename}"
        file_path = conv_dir / storage_filename
        
        # 写入文件
        with open(file_path, "wb") as f:
            f.write(file_content)
        
        # 创建文件记录
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
        )
        
        # 保存到数据库（SQLite 或 MySQL）
        await self._save_to_db(file_info)
        
        return file_info
    
    async def list_files(self, conversation_id: str) -> List[ConversationFile]:
        """列出对话的所有文件"""
        if self._mysql_enabled:
            return await self._list_files_mysql(conversation_id)
        
        # SQLite 模式
        with self._get_db_connection() as conn:
            cursor = conn.execute(
                """SELECT file_id, conversation_id, filename, original_name, 
                          file_path, file_size, mime_type, doc_id, status, 
                          created_at, error_message
                   FROM conversation_files 
                   WHERE conversation_id = ? 
                   ORDER BY created_at DESC""",
                (conversation_id,)
            )
            rows = cursor.fetchall()
        
        return [self._row_to_file_sqlite(row) for row in rows]
    
    async def _list_files_mysql(self, conversation_id: str) -> List[ConversationFile]:
        """MySQL 模式列出文件"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return []
        await self._ensure_mysql_schema()
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT file_id, conversation_id, filename, original_name, 
                              file_path, file_size, mime_type, doc_id, status, 
                              created_at, error_message
                       FROM conversation_files 
                       WHERE conversation_id = %s 
                       ORDER BY created_at DESC""",
                    (conversation_id,)
                )
                rows = await cursor.fetchall()
        
        return [self._row_to_file(row) for row in rows]
    
    async def get_file(self, conversation_id: str, file_id: str) -> Optional[ConversationFile]:
        """获取单个文件信息"""
        if self._mysql_enabled:
            return await self._get_file_mysql(conversation_id, file_id)
        
        # SQLite 模式
        with self._get_db_connection() as conn:
            cursor = conn.execute(
                """SELECT file_id, conversation_id, filename, original_name, 
                          file_path, file_size, mime_type, doc_id, status, 
                          created_at, error_message
                   FROM conversation_files 
                   WHERE conversation_id = ? AND file_id = ?""",
                (conversation_id, file_id)
            )
            row = cursor.fetchone()
        
        return self._row_to_file_sqlite(row) if row else None
    
    async def _get_file_mysql(self, conversation_id: str, file_id: str) -> Optional[ConversationFile]:
        """MySQL 模式获取文件"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return None
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """SELECT file_id, conversation_id, filename, original_name, 
                              file_path, file_size, mime_type, doc_id, status, 
                              created_at, error_message
                       FROM conversation_files 
                       WHERE conversation_id = %s AND file_id = %s""",
                    (conversation_id, file_id)
                )
                row = await cursor.fetchone()
        
        return self._row_to_file(row) if row else None
    
    async def update_file_status(
        self,
        conversation_id: str,
        file_id: str,
        status: str,
        doc_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """更新文件状态（用于 ingest 完成后更新）"""
        if self._mysql_enabled:
            await self._update_file_status_mysql(conversation_id, file_id, status, doc_id, error_message)
            return
        
        # SQLite 模式
        with self._get_db_connection() as conn:
            conn.execute(
                """UPDATE conversation_files 
                   SET status = ?, doc_id = ?, error_message = ?
                   WHERE conversation_id = ? AND file_id = ?""",
                (status, doc_id, error_message, conversation_id, file_id)
            )
    
    async def _update_file_status_mysql(
        self,
        conversation_id: str,
        file_id: str,
        status: str,
        doc_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """MySQL 模式更新状态"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE conversation_files 
                       SET status = %s, doc_id = %s, error_message = %s
                       WHERE conversation_id = %s AND file_id = %s""",
                    (status, doc_id, error_message, conversation_id, file_id)
                )
    
    async def delete_file(self, conversation_id: str, file_id: str) -> bool:
        """
        删除文件
        
        1. 删除磁盘文件
        2. 删除数据库记录
        3. 返回是否成功
        """
        # 获取文件信息
        file_info = await self.get_file(conversation_id, file_id)
        if not file_info:
            return False
        
        # 删除磁盘文件
        try:
            Path(file_info.file_path).unlink(missing_ok=True)
        except Exception as e:
            print(f"[FileStore] Failed to delete file: {e}")
        
        # 删除数据库记录
        if self._mysql_enabled:
            pool = await self._get_mysql_pool()
            if pool is not None:
                async with pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute(
                            "DELETE FROM conversation_files WHERE conversation_id = %s AND file_id = %s",
                            (conversation_id, file_id)
                        )
        else:
            # SQLite 模式
            with self._get_db_connection() as conn:
                conn.execute(
                    "DELETE FROM conversation_files WHERE conversation_id = ? AND file_id = ?",
                    (conversation_id, file_id)
                )
        
        return True
    
    async def delete_conversation_files(self, conversation_id: str) -> int:
        """
        删除对话的所有文件（用于删除对话时清理）
        
        Returns:
            删除的文件数量
        """
        # 获取所有文件
        files = await self.list_files(conversation_id)
        
        # 删除磁盘目录
        conv_dir = self._upload_dir / conversation_id
        if conv_dir.exists():
            try:
                shutil.rmtree(conv_dir)
            except Exception as e:
                print(f"[FileStore] Failed to delete directory: {e}")
        
        # 删除数据库记录
        if self._mysql_enabled:
            pool = await self._get_mysql_pool()
            if pool is not None:
                async with pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute(
                            "DELETE FROM conversation_files WHERE conversation_id = %s",
                            (conversation_id,)
                        )
        else:
            # SQLite 模式
            with self._get_db_connection() as conn:
                conn.execute(
                    "DELETE FROM conversation_files WHERE conversation_id = ?",
                    (conversation_id,)
                )
        
        return len(files)
    
    async def _save_to_db(self, file_info: ConversationFile) -> None:
        """保存文件信息到数据库"""
        if self._mysql_enabled:
            await self._save_to_mysql(file_info)
            return
        
        # SQLite 模式
        with self._get_db_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO conversation_files 
                   (file_id, conversation_id, filename, original_name, file_path, 
                    file_size, mime_type, doc_id, status, created_at, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    file_info.file_id,
                    file_info.conversation_id,
                    file_info.filename,
                    file_info.original_name,
                    file_info.file_path,
                    file_info.file_size,
                    file_info.mime_type,
                    file_info.doc_id,
                    file_info.status,
                    file_info.created_at.isoformat(),
                    file_info.error_message,
                )
            )
    
    async def _save_to_mysql(self, file_info: ConversationFile) -> None:
        """保存到 MySQL"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return
        await self._ensure_mysql_schema()
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO conversation_files 
                       (file_id, conversation_id, filename, original_name, file_path, 
                        file_size, mime_type, doc_id, status, created_at, error_message)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                       filename = VALUES(filename),
                       original_name = VALUES(original_name),
                       file_path = VALUES(file_path),
                       file_size = VALUES(file_size),
                       mime_type = VALUES(mime_type),
                       doc_id = VALUES(doc_id),
                       status = VALUES(status),
                       error_message = VALUES(error_message)""",
                    (
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
                    )
                )
    
    def _row_to_file(self, row: tuple) -> ConversationFile:
        """将 MySQL 数据库行转换为 ConversationFile"""
        return ConversationFile(
            file_id=row[0],
            conversation_id=row[1],
            filename=row[2],
            original_name=row[3],
            file_path=row[4],
            file_size=row[5],
            mime_type=row[6],
            doc_id=row[7],
            status=row[8],
            created_at=row[9] if isinstance(row[9], datetime) else datetime.fromisoformat(str(row[9])),
            error_message=row[10],
        )
    
    def _row_to_file_sqlite(self, row: sqlite3.Row) -> ConversationFile:
        """将 SQLite 数据库行转换为 ConversationFile"""
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
            created_at=datetime.fromisoformat(row["created_at"]),
            error_message=row["error_message"],
        )
    
    async def _get_mysql_pool(self):
        """获取或创建 MySQL 连接池，如果禁用则返回 None"""
        if not self._mysql_enabled:
            return None
            
        if self._mysql_pool is not None:
            return self._mysql_pool
        
        try:
            import aiomysql
        except ImportError as exc:
            raise RuntimeError("aiomysql is required") from exc
        
        self._mysql_pool = await aiomysql.create_pool(
            host=self._mysql_host,
            port=self._mysql_port,
            user=self._mysql_user,
            password=self._mysql_password,
            db=self._mysql_database,
            charset="utf8mb4",
            autocommit=True,
            minsize=1,
            maxsize=5,
        )
        await self._ensure_mysql_schema()
        return self._mysql_pool
    
    async def _ensure_mysql_schema(self) -> None:
        """确保 MySQL 表结构存在"""
        pool = await self._get_mysql_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # 对话文件表
                await cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_files (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        file_id VARCHAR(64) NOT NULL,
                        conversation_id VARCHAR(128) NOT NULL,
                        filename VARCHAR(512) NOT NULL,
                        original_name VARCHAR(512) NOT NULL,
                        file_path VARCHAR(1024) NOT NULL,
                        file_size BIGINT NOT NULL DEFAULT 0,
                        mime_type VARCHAR(128) DEFAULT 'application/octet-stream',
                        doc_id VARCHAR(128) NULL,
                        status VARCHAR(32) NOT NULL DEFAULT 'pending',
                        created_at DATETIME NOT NULL,
                        error_message TEXT NULL,
                        INDEX idx_conv_files (conversation_id, created_at),
                        INDEX idx_file_id (file_id),
                        UNIQUE KEY uk_conv_file (conversation_id, file_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )


def build_file_store() -> ConversationFileStore:
    """工厂函数"""
    return ConversationFileStore()
