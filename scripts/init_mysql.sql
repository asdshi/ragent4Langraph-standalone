-- ============================================
-- RAG Agent MySQL 数据库完整初始化脚本
-- 执行: mysql -u root -p < scripts/init_mysql.sql
-- ============================================

-- 创建数据库
CREATE DATABASE IF NOT EXISTS ragent 
    CHARACTER SET utf8mb4 
    COLLATE utf8mb4_unicode_ci;

USE ragent;

-- ============================================
-- 1. 对话列表表 (conversations)
-- 存储对话的基本信息和统计
-- ============================================
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id VARCHAR(128) PRIMARY KEY COMMENT '对话唯一ID',
    title VARCHAR(512) NOT NULL COMMENT '对话标题',
    created_at DATETIME NOT NULL COMMENT '创建时间',
    updated_at DATETIME NOT NULL COMMENT '最后更新时间',
    message_count INT DEFAULT 0 COMMENT '消息数量统计',
    file_count INT DEFAULT 0 COMMENT '文件数量统计',
    status VARCHAR(32) DEFAULT 'active' COMMENT '状态: active/archived/deleted',
    metadata JSON COMMENT '扩展元数据(JSON格式)',
    
    INDEX idx_updated (updated_at DESC),
    INDEX idx_status (status)
    
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话列表';


-- ============================================
-- 2. 对话文件表 (conversation_files)
-- 存储每个对话上传的文件元数据
-- ============================================
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


-- ============================================
-- 3. 对话消息归档表 (conversation_archive)
-- 存储用户可见的完整对话历史(消息列表)
-- ============================================
CREATE TABLE IF NOT EXISTS conversation_archive (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    conversation_id VARCHAR(128) NOT NULL COMMENT '对话ID',
    role VARCHAR(32) NOT NULL COMMENT '角色: user/assistant/system',
    content LONGTEXT NOT NULL COMMENT '消息内容',
    message_id VARCHAR(64) NULL COMMENT '消息唯一ID',
    created_at DOUBLE NOT NULL COMMENT '创建时间戳(Unix秒)',
    
    INDEX idx_archive_conversation_time (conversation_id, created_at)
    
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对话消息历史表';


-- ============================================
-- 查看创建的表
-- ============================================
SHOW TABLES;

-- 查看表结构
DESCRIBE conversations;
DESCRIBE conversation_files;
DESCRIBE conversation_archive;
