"""文件完整性检查模块（增量摄取核心）。

该模块通过 `SHA256(file_content)` 识别“同一内容的文件”，并将处理状态
持久化到 SQLite，用于控制后续摄取时是否跳过。

核心目标：
- 幂等：同一个文件重复执行摄取不会造成重复数据。
- 可恢复：历史记录持久化，进程重启后仍可继续判断。
- 可重试：失败记录会保留，但不会被 `should_skip()` 跳过。
- 并发友好：开启 WAL，兼顾读写并发场景。
"""

import hashlib
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class FileIntegrityChecker(ABC):
    """文件完整性检查抽象接口。

    说明：
    - 该接口定义了“哈希计算 + 跳过判断 + 状态落库”的最小契约。
    - 具体存储介质可替换（SQLite / Redis / Postgres 等），
      只要遵守此接口即可与上层流水线解耦。
    """

    @abstractmethod
    def compute_sha256(self, file_path: str) -> str:
        """计算文件 SHA256 内容哈希。

        Args:
            file_path: Path to the file to hash.

        Returns:
            Hexadecimal SHA256 hash string (64 characters).

        Raises:
            FileNotFoundError: If file does not exist.
            IOError: If path is not a file or cannot be read.
        """
        pass

    @abstractmethod
    def should_skip(self, file_hash: str) -> bool:
        """根据文件哈希判断是否应跳过本次处理。

        Args:
            file_hash: SHA256 hash of the file.

        Returns:
            True if file has been successfully processed before, False otherwise.
        """
        pass

    @abstractmethod
    def mark_success(
        self,
        file_hash: str,
        file_path: str,
        collection: Optional[str] = None,
    ) -> None:
        """将文件标记为“处理成功”。

        Args:
            file_hash: SHA256 hash of the file.
            file_path: Original file path (for tracking).
            collection: Optional collection/namespace identifier.

        Raises:
            RuntimeError: If database operation fails.
        """
        pass

    @abstractmethod
    def mark_failed(
        self,
        file_hash: str,
        file_path: str,
        error_msg: str,
    ) -> None:
        """将文件标记为“处理失败”。

        Failed files are tracked but not skipped on subsequent runs,
        allowing retries.

        Args:
            file_hash: SHA256 hash of the file.
            file_path: Original file path (for tracking).
            error_msg: Error message describing the failure.

        Raises:
            RuntimeError: If database operation fails.
        """
        pass

    @abstractmethod
    def remove_record(self, file_hash: str) -> bool:
        """按文件哈希删除一条摄取记录。

        Args:
            file_hash: SHA256 hash identifying the record.

        Returns:
            True if a record was deleted, False if not found.
        """
        pass

    @abstractmethod
    def list_processed(
        self, collection: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """列出成功处理过的文件记录。

        Args:
            collection: Optional collection filter.  When *None* all
                successful records are returned.

        Returns:
            List of dicts with keys: file_hash, file_path, collection,
            processed_at, updated_at.
        """
        pass


class SQLiteIntegrityChecker(FileIntegrityChecker):
    """基于 SQLite 的完整性检查实现。

    表结构（ingestion_history）字段语义：
    - file_hash: 文件内容哈希，主键（同内容唯一）。
    - file_path: 最近一次处理时的文件路径（便于回溯）。
    - status: 处理状态（success / failed）。
    - collection: 文档集合名（可为空）。
    - error_msg: 失败时错误信息（success 时为 NULL）。
    - processed_at: 首次入库时间（保持不变）。
    - updated_at: 最近一次状态更新时间。

    设计细节：
    - 对“成功/失败”采用 upsert 风格写入，保证重复调用安全。
    - `should_skip()` 只跳过 success，failed 仍允许重试。
    """
    
    def __init__(self, db_path: str):
        """初始化检查器并确保数据库可用。
        
        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._conn = None
        # 构造时即保证表存在，避免首次写入时才暴露配置问题。
        self._ensure_database()
    
    def close(self) -> None:
        """关闭数据库连接（若已打开）。"""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def __del__(self):
        """对象释放时兜底关闭连接。"""
        self.close()
    
    def _ensure_database(self) -> None:
        """确保数据库目录、表和索引存在。"""
        # 若目录不存在则递归创建，保证首次部署可直接运行。
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 用短连接完成初始化，避免构造阶段长期持有连接。
        conn = sqlite3.connect(self.db_path)
        try:
            # 开启 WAL：读写并发更友好，适合摄取/查询并行场景。
            conn.execute("PRAGMA journal_mode=WAL")
            
            # 首次启动创建主表。
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_history (
                    file_hash TEXT PRIMARY KEY,
                    file_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    collection TEXT,
                    error_msg TEXT,
                    processed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # status 上建索引，加速按状态筛选（如 list_processed）。
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status 
                ON ingestion_history(status)
            """)
            
            conn.commit()
        finally:
            conn.close()
    
    def compute_sha256(self, file_path: str) -> str:
        """分块读取文件并计算 SHA256。
        
        Uses 64KB chunks to handle large files without loading entire
        file into memory.
        
        Args:
            file_path: Path to the file to hash.
            
        Returns:
            Hexadecimal SHA256 hash string (64 characters).
            
        Raises:
            FileNotFoundError: If file does not exist.
            IOError: If path is not a file or cannot be read.
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if not path.is_file():
            raise IOError(f"Path is not a file: {file_path}")
        
        # 采用流式分块，避免大文件一次性读入内存。
        sha256_hash = hashlib.sha256()
        
        try:
            with open(file_path, "rb") as f:
                # 64KB 是常见折中值：系统调用次数与内存占用较平衡。
                for chunk in iter(lambda: f.read(65536), b""):
                    sha256_hash.update(chunk)
        except Exception as e:
            raise IOError(f"Failed to read file {file_path}: {e}")
        
        return sha256_hash.hexdigest()
    
    def should_skip(self, file_hash: str) -> bool:
        """判断该哈希对应文件是否可跳过。
        
        Only files with status='success' are skipped. Failed files
        can be retried.
        
        Args:
            file_hash: SHA256 hash of the file.
            
        Returns:
            True if file has status='success', False otherwise.
        """
        # 使用短连接读状态，减少长连接竞争。
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT status FROM ingestion_history WHERE file_hash = ?",
                (file_hash,)
            )
            result = cursor.fetchone()
            
            # 无记录：说明从未处理过，不能跳过。
            if result is None:
                return False
            
            # 仅 success 才跳过；failed 要允许重试。
            return result[0] == "success"
        finally:
            conn.close()
    
    def mark_success(
        self, 
        file_hash: str, 
        file_path: str, 
        collection: Optional[str] = None
    ) -> None:
        """记录文件成功处理状态。
        
        Uses INSERT OR REPLACE for idempotent operation.
        
        Args:
            file_hash: SHA256 hash of the file.
            file_path: Original file path (for tracking).
            collection: Optional collection/namespace identifier.
            
        Raises:
            RuntimeError: If database operation fails.
        """
        # 统一使用 UTC ISO8601，便于跨时区与审计。
        now = datetime.now(timezone.utc).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        try:
            # 为保留首次处理时间 processed_at，先判断是否已有记录。
            cursor = conn.execute(
                "SELECT processed_at FROM ingestion_history WHERE file_hash = ?",
                (file_hash,)
            )
            result = cursor.fetchone()
            
            if result:
                # 已存在：更新状态与更新时间，不改 processed_at。
                conn.execute("""
                    UPDATE ingestion_history 
                    SET file_path = ?,
                        status = 'success',
                        collection = ?,
                        error_msg = NULL,
                        updated_at = ?
                    WHERE file_hash = ?
                """, (file_path, collection, now, file_hash))
            else:
                # 不存在：插入新记录，processed_at 与 updated_at 同时写入 now。
                conn.execute("""
                    INSERT INTO ingestion_history 
                    (file_hash, file_path, status, collection, error_msg, processed_at, updated_at)
                    VALUES (?, ?, 'success', ?, NULL, ?, ?)
                """, (file_hash, file_path, collection, now, now))
            
            conn.commit()
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to mark success for {file_path}: {e}")
        finally:
            conn.close()
    
    def mark_failed(
        self, 
        file_hash: str, 
        file_path: str, 
        error_msg: str
    ) -> None:
        """记录文件处理失败状态。
        
        Failed files are not skipped, allowing retries.
        
        Args:
            file_hash: SHA256 hash of the file.
            file_path: Original file path (for tracking).
            error_msg: Error message describing the failure.
            
        Raises:
            RuntimeError: If database operation fails.
        """
        # 失败记录同样写 UTC 时间，便于后续排障与重试策略。
        now = datetime.now(timezone.utc).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        try:
            # 同 success 分支一致：尽量保留首次处理时间。
            cursor = conn.execute(
                "SELECT processed_at FROM ingestion_history WHERE file_hash = ?",
                (file_hash,)
            )
            result = cursor.fetchone()
            
            if result:
                # 已存在：覆盖状态与错误信息。
                conn.execute("""
                    UPDATE ingestion_history 
                    SET file_path = ?,
                        status = 'failed',
                        error_msg = ?,
                        updated_at = ?
                    WHERE file_hash = ?
                """, (file_path, error_msg, now, file_hash))
            else:
                # 不存在：插入失败记录（collection 置空，后续可再更新）。
                conn.execute("""
                    INSERT INTO ingestion_history 
                    (file_hash, file_path, status, collection, error_msg, processed_at, updated_at)
                    VALUES (?, ?, 'failed', NULL, ?, ?, ?)
                """, (file_hash, file_path, error_msg, now, now))
            
            conn.commit()
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to mark failure for {file_path}: {e}")
        finally:
            conn.close()

    def remove_record(self, file_hash: str) -> bool:
        """按哈希删除记录。

        Args:
            file_hash: SHA256 hash identifying the record.

        Returns:
            True if a record was deleted, False if not found.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "DELETE FROM ingestion_history WHERE file_hash = ?",
                (file_hash,),
            )
            conn.commit()
            # rowcount > 0 表示确实删除到了目标记录。
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to remove record {file_hash}: {e}")
        finally:
            conn.close()

    def list_processed(
        self, collection: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """查询成功处理记录列表。

        Args:
            collection: Optional collection filter.

        Returns:
            List of dicts with keys: file_hash, file_path, collection,
            processed_at, updated_at.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            # 只返回 success，符合“可跳过记录”的业务语义。
            query = (
                "SELECT file_hash, file_path, collection, processed_at, updated_at "
                "FROM ingestion_history WHERE status = 'success'"
            )
            params: list[str] = []
            if collection is not None:
                # 按集合过滤，支持多知识库隔离。
                query += " AND collection = ?"
                params.append(collection)
            # 旧记录在前，便于按时间回放摄取过程。
            query += " ORDER BY processed_at ASC"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
