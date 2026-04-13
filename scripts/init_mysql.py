"""
MySQL 数据库初始化脚本
自动创建 ragent 数据库和相关表
"""

import asyncio
import os
import sys

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from dotenv import load_dotenv
load_dotenv()

# 数据库配置
DB_CONFIG = {
    "host": os.getenv("RAGENT_MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("RAGENT_MYSQL_PORT", "3306")),
    "user": os.getenv("RAGENT_MYSQL_USER", "root"),
    "password": os.getenv("RAGENT_MYSQL_PASSWORD", ""),
    "database": os.getenv("RAGENT_MYSQL_DATABASE", "ragent"),
    "charset": os.getenv("RAGENT_MYSQL_CHARSET", "utf8mb4"),
}

# SQL 语句
CREATE_DATABASE_SQL = f"""
CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']} 
    CHARACTER SET utf8mb4 
    COLLATE utf8mb4_unicode_ci;
"""

CREATE_CONVERSATION_FILES_TABLE = """
CREATE TABLE IF NOT EXISTS conversation_files (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    file_id VARCHAR(64) NOT NULL COMMENT '文件唯一ID(8位uuid)',
    conversation_id VARCHAR(128) NOT NULL COMMENT '所属对话ID',
    filename VARCHAR(512) NOT NULL COMMENT '存储文件名',
    original_name VARCHAR(512) NOT NULL COMMENT '原始上传文件名',
    file_path VARCHAR(1024) NOT NULL COMMENT '磁盘存储绝对路径',
    file_size BIGINT NOT NULL DEFAULT 0 COMMENT '文件大小(字节)',
    mime_type VARCHAR(128) DEFAULT 'application/octet-stream' COMMENT 'MIME类型',
    doc_id VARCHAR(128) NULL COMMENT '向量化后的文档ID',
    status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '状态: pending/ingesting/ready/error',
    created_at DATETIME NOT NULL COMMENT '创建时间',
    error_message TEXT NULL COMMENT '错误信息',
    
    INDEX idx_conv_files (conversation_id, created_at),
    INDEX idx_file_id (file_id),
    UNIQUE KEY uk_conv_file (conversation_id, file_id)
    
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话文件元数据表';
"""

CREATE_CONVERSATION_ARCHIVE_TABLE = """
CREATE TABLE IF NOT EXISTS conversation_archive (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    conversation_id VARCHAR(128) NOT NULL COMMENT '对话ID',
    role VARCHAR(32) NOT NULL COMMENT '角色: user/assistant/system',
    content LONGTEXT NOT NULL COMMENT '消息内容',
    message_id VARCHAR(64) NULL COMMENT '消息唯一ID',
    created_at DOUBLE NOT NULL COMMENT '创建时间戳(秒)',
    
    INDEX idx_archive_conversation_time (conversation_id, created_at)
    
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话历史归档表';
"""


async def init_mysql():
    """初始化 MySQL 数据库和表"""
    try:
        import aiomysql
    except ImportError:
        print("[ERROR] Missing aiomysql, please install: pip install aiomysql")
        sys.exit(1)
    
    print("=" * 60)
    print("RAG Agent MySQL 数据库初始化")
    print("=" * 60)
    print(f"\n[连接信息]")
    print(f"   Host: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"   User: {DB_CONFIG['user']}")
    print(f"   Password: {'*' * len(DB_CONFIG['password'])}")
    print(f"   Database: {DB_CONFIG['database']}")
    print(f"   Charset: {DB_CONFIG['charset']}")
    
    try:
        # 第一步：连接 MySQL（不指定数据库）创建数据库
        print("\n[1/3] Creating database...")
        conn = await aiomysql.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            charset=DB_CONFIG['charset'],
        )
        async with conn.cursor() as cur:
            await cur.execute(CREATE_DATABASE_SQL)
        conn.close()
        print("   [OK] Database created")
        
        # 第二步：连接到新创建的数据库，创建表
        print("\n[2/3] Creating tables...")
        conn = await aiomysql.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            db=DB_CONFIG['database'],
            charset=DB_CONFIG['charset'],
        )
        
        async with conn.cursor() as cur:
            # 创建 conversation_files 表
            await cur.execute(CREATE_CONVERSATION_FILES_TABLE)
            print("   [OK] conversation_files table created")
            
            # 创建 conversation_archive 表
            await cur.execute(CREATE_CONVERSATION_ARCHIVE_TABLE)
            print("   [OK] conversation_archive table created")
        
        conn.close()
        
        # 第三步：验证
        print("\n[3/3] Verifying database...")
        conn = await aiomysql.connect(
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            db=DB_CONFIG['database'],
            charset=DB_CONFIG['charset'],
        )
        async with conn.cursor() as cur:
            await cur.execute("SHOW TABLES")
            tables = await cur.fetchall()
            print(f"   Tables in database:")
            for table in tables:
                await cur.execute(f"SELECT COUNT(*) FROM {table[0]}")
                count = await cur.fetchone()
                print(f"      - {table[0]}: {count[0]} records")
        conn.close()
        
        print("\n" + "=" * 60)
        print("[OK] MySQL database initialized successfully!")
        print("=" * 60)
        print("\nYou can now start RAG Agent with MySQL support enabled.")
        
    except Exception as e:
        print(f"\n[ERROR] Initialization failed: {e}")
        print("\nPossible reasons:")
        print("   1. MySQL service not running")
        print("   2. Incorrect username/password")
        print("   3. Network connection issue")
        print("   4. Insufficient privileges")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(init_mysql())
