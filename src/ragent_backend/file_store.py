"""
对话文件存储管理

职责：
1. 保存用户上传的原始文件（磁盘存储）
2. 记录文件元数据（MySQL）
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
    
    def __init__(self, upload_dir: str = "./data/uploads") -> None:
        self._upload_dir = Path(upload_dir).resolve()
        self._upload_dir.mkdir(parents=True, exist_ok=True)
        
        # MySQL 连接（复用 store.py 的配置）
        self._mysql_pool = None
        self._mysql_host = os.getenv("RAGENT_MYSQL_HOST", "127.0.0.1")
        self._mysql_port = int(os.getenv("RAGENT_MYSQL_PORT", "3306"))
        self._mysql_user = os.getenv("RAGENT_MYSQL_USER", "root")
        self._mysql_password = os.getenv("RAGENT_MYSQL_PASSWORD", "")
        self._mysql_database = os.getenv("RAGENT_MYSQL_DATABASE", "ragent")
    
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
        
        # 保存到数据库
        await self._save_to_db(file_info)
        
        return file_info
    
    async def list_files(self, conversation_id: str) -> List[ConversationFile]:
        """列出对话的所有文件"""
        pool = await self._get_mysql_pool()
        await self._ensure_schema()
        
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
        pool = await self._get_mysql_pool()
        
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
        pool = await self._get_mysql_pool()
        
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
        pool = await self._get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM conversation_files WHERE conversation_id = %s AND file_id = %s",
                    (conversation_id, file_id)
                )
                deleted = cursor.rowcount > 0
        
        return deleted
    
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
        pool = await self._get_mysql_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM conversation_files WHERE conversation_id = %s",
                    (conversation_id,)
                )
                deleted_count = cursor.rowcount
        
        return deleted_count
    
    async def _save_to_db(self, file_info: ConversationFile) -> None:
        """保存文件信息到数据库"""
        pool = await self._get_mysql_pool()
        await self._ensure_schema()
        
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """INSERT INTO conversation_files 
                       (file_id, conversation_id, filename, original_name, file_path, 
                        file_size, mime_type, doc_id, status, created_at, error_message)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
        """将数据库行转换为 ConversationFile"""
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
            created_at=row[9],
            error_message=row[10],
        )
    
    async def _get_mysql_pool(self):
        """获取或创建 MySQL 连接池"""
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
        await self._ensure_schema()
        return self._mysql_pool
    
    async def _ensure_schema(self) -> None:
        """确保表结构存在"""
        pool = await self._get_mysql_pool()
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
