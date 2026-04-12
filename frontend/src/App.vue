<template>
  <div class="app-container">
    <el-container class="main-container">
      <!-- 左侧边栏 -->
      <el-aside width="320px" class="sidebar">
        <div class="sidebar-header">
          <div class="logo">
            <el-icon size="28" color="#667eea"><ChatDotRound /></el-icon>
            <span class="logo-text">RAG Agent</span>
          </div>
          <p class="logo-subtitle">智能对话与知识库</p>
        </div>
        
        <div class="sidebar-actions">
          <el-button 
            type="primary" 
            size="large" 
            class="new-chat-btn"
            @click="startNewChat"
          >
            <el-icon><Plus /></el-icon>
            新建对话
          </el-button>
        </div>
        
        <!-- 文件管理区域 -->
        <div class="file-section">
          <div class="section-title">
            <el-icon><Document /></el-icon>
            <span>文件管理</span>
          </div>
          
          <!-- 上传区域 -->
          <el-upload
            class="upload-area"
            drag
            action=""
            :auto-upload="false"
            :on-change="handleFileChange"
            :show-file-list="false"
            accept=".pdf,.docx,.txt,.md,.csv,.xlsx"
          >
            <el-icon class="upload-icon"><Upload /></el-icon>
            <div class="upload-text">
              <div>点击或拖拽上传文件</div>
              <div class="upload-hint">支持 PDF, Word, TXT, CSV</div>
            </div>
          </el-upload>
          
          <!-- 文件列表 -->
          <div class="file-list">
            <div v-if="files.length === 0" class="empty-files">
              <el-icon size="32" color="#dcdfe6"><FolderOpened /></el-icon>
              <p>暂无文件</p>
            </div>
            
            <div
              v-for="file in files"
              :key="file.file_id"
              class="file-item"
            >
              <div class="file-icon" :class="getFileIconClass(file.filename)">
                <el-icon size="20">
                  <component :is="getFileIcon(file.filename)" />
                </el-icon>
              </div>
              
              <div class="file-info">
                <div class="file-name" :title="file.filename">{{ file.filename }}</div>
                <div class="file-size">{{ formatFileSize(file.size) }}</div>
              </div>
              
              <el-tag 
                :type="getStatusType(file.status)" 
                size="small"
                class="file-status"
              >
                {{ getStatusText(file.status) }}
              </el-tag>
              
              <el-button
                type="danger"
                link
                size="small"
                @click="deleteFile(file.file_id)"
              >
                <el-icon><Delete /></el-icon>
              </el-button>
            </div>
          </div>
        </div>
        
        <!-- 系统信息 -->
        <div class="system-info">
          <el-divider />
          <div class="info-item">
            <span>API 地址:</span>
            <el-link type="primary" @click="showSettings">{{ apiBase }}</el-link>
          </div>
        </div>
      </el-aside>
      
      <!-- 主聊天区域 -->
      <el-main class="chat-area">
        <!-- 头部 -->
        <div class="chat-header">
          <div class="header-left">
            <span class="chat-title">💬 当前对话</span>
            <el-tag v-if="conversationId" size="small" type="info" class="conv-tag">
              {{ conversationId.substring(0, 16) }}...
            </el-tag>
            <el-tag v-else size="small" type="warning">新对话</el-tag>
          </div>
          
          <div class="header-right">
            <el-tooltip content="设置" placement="bottom">
              <el-button circle @click="showSettings">
                <el-icon><Setting /></el-icon>
              </el-button>
            </el-tooltip>
          </div>
        </div>
        
        <!-- 消息列表 -->
        <div ref="messagesContainer" class="messages-container">
          <div v-if="messages.length === 0" class="welcome-message">
            <el-icon size="64" color="#667eea"><ChatDotRound /></el-icon>
            <h2>你好！我是 RAG Agent</h2>
            <p>你可以上传文件，然后向我提问关于文件内容的问题</p>
            <div class="quick-actions">
              <el-button 
                v-for="action in quickActions" 
                :key="action"
                type="default"
                class="quick-action-btn"
                @click="sendQuickAction(action)"
              >
                {{ action }}
              </el-button>
            </div>
          </div>
          
          <div
            v-for="(msg, index) in messages"
            :key="index"
            :class="['message', msg.role]"
          >
            <div class="message-avatar">
              <el-avatar 
                :size="36" 
                :icon="msg.role === 'user' ? UserFilled : ChatDotRound"
                :class="msg.role"
              />
            </div>
            
            <div class="message-content-wrapper">
              <div class="message-content" v-html="renderMarkdown(msg.content)"></div>
              <div class="message-time">{{ formatTime(msg.time) }}</div>
              
              <!-- 检索来源（仅 AI 消息显示） -->
              <div v-if="msg.sources && msg.sources.length > 0" class="message-sources">
                <el-collapse>
                  <el-collapse-item title="检索来源">
                    <div
                      v-for="(source, idx) in msg.sources"
                      :key="idx"
                      class="source-item"
                    >
                      <el-tag size="small">{{ source.doc_id }}</el-tag>
                      <span class="source-score">得分: {{ (source.score * 100).toFixed(1) }}%</span>
                      <p class="source-content">{{ source.content.substring(0, 100) }}...</p>
                    </div>
                  </el-collapse-item>
                </el-collapse>
              </div>
            </div>
          </div>
          
          <!-- 思考中指示器 -->
          <div v-if="isTyping" class="message assistant typing">
            <div class="message-avatar">
              <el-avatar :size="36" :icon="ChatDotRound" class="assistant" />
            </div>
            <div class="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        </div>
        
        <!-- 输入区域 -->
        <div class="input-area">
          <div class="input-wrapper">
            <el-input
              v-model="inputMessage"
              type="textarea"
              :rows="1"
              placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
              class="message-input"
              resize="none"
              @keydown="handleKeydown"
            />
            <el-button
              type="primary"
              circle
              class="send-btn"
              :disabled="!inputMessage.trim() || isTyping"
              @click="sendMessage"
            >
              <el-icon><Promotion /></el-icon>
            </el-button>
          </div>
          <div class="input-hint">
            <el-icon size="12"><InfoFilled /></el-icon>
            <span>AI 生成内容仅供参考</span>
          </div>
        </div>
      </el-main>
    </el-container>
    
    <!-- 设置对话框 -->
    <el-dialog
      v-model="settingsVisible"
      title="设置"
      width="500px"
      destroy-on-close
    >
      <el-form :model="settings" label-width="120px">
        <el-form-item label="API 基础地址">
          <el-input v-model="settings.apiBase" placeholder="http://localhost:8000/api/v1" />
        </el-form-item>
        
        <el-form-item label="检索数量 (Top K)">
          <el-slider v-model="settings.topK" :min="1" :max="20" show-stops />
        </el-form-item>
        
        <el-form-item label="流式输出">
          <el-switch v-model="settings.streaming" />
        </el-form-item>
        
        <el-form-item label="模型">
          <el-select v-model="settings.model" style="width: 100%">
            <el-option label="GPT-4o" value="gpt-4o" />
            <el-option label="GPT-4o Mini" value="gpt-4o-mini" />
          </el-select>
        </el-form-item>
      </el-form>
      
      <template #footer>
        <el-button @click="settingsVisible = false">取消</el-button>
        <el-button type="primary" @click="saveSettings">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'

// ==================== 状态管理 ====================
const messages = ref([])
const inputMessage = ref('')
const isTyping = ref(false)
const conversationId = ref(null)
const files = ref([])
const messagesContainer = ref(null)
const settingsVisible = ref(false)

// 设置
const settings = reactive({
  apiBase: 'http://localhost:8000/api/v1',
  topK: 5,
  streaming: true,
  model: 'gpt-4o'
})

const apiBase = ref(settings.apiBase)

// 快捷操作
const quickActions = [
  '总结一下这份文档',
  '文档的主要观点是什么？',
  '找出关键数据',
  '翻译这段内容'
]

// ==================== 生命周期 ====================
onMounted(() => {
  loadSettings()
  // 加载历史对话（如果有）
  const savedConvId = localStorage.getItem('currentConversationId')
  if (savedConvId) {
    conversationId.value = savedConvId
    loadHistory(savedConvId)
    loadFiles(savedConvId)
  }
})

// ==================== 方法 ====================

// 加载设置
function loadSettings() {
  const saved = localStorage.getItem('ragAgentSettings')
  if (saved) {
    Object.assign(settings, JSON.parse(saved))
    apiBase.value = settings.apiBase
  }
}

// 保存设置
function saveSettings() {
  localStorage.setItem('ragAgentSettings', JSON.stringify(settings))
  apiBase.value = settings.apiBase
  settingsVisible.value = false
  ElMessage.success('设置已保存')
}

// 显示设置
function showSettings() {
  settingsVisible.value = true
}

// 新建对话
function startNewChat() {
  ElMessageBox.confirm('确定要开始新对话吗？当前对话将被保存。', '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(() => {
    conversationId.value = null
    messages.value = []
    files.value = []
    localStorage.removeItem('currentConversationId')
    ElMessage.success('已开启新对话')
  }).catch(() => {})
}

// 发送消息
async function sendMessage() {
  const query = inputMessage.value.trim()
  if (!query || isTyping.value) return

  // 添加用户消息
  messages.value.push({
    role: 'user',
    content: query,
    time: new Date()
  })
  
  inputMessage.value = ''
  scrollToBottom()

  if (settings.streaming) {
    await sendStreamMessage(query)
  } else {
    await sendNormalMessage(query)
  }
}

// 快捷操作
function sendQuickAction(action) {
  inputMessage.value = action
  sendMessage()
}

// 普通消息发送
async function sendNormalMessage(query) {
  isTyping.value = true
  
  try {
    const response = await axios.post(`${settings.apiBase}/chat`, {
      query,
      conversation_id: conversationId.value,
      top_k: settings.topK,
      model: settings.model
    })

    const data = response.data
    
    if (!conversationId.value && data.conversation_id) {
      conversationId.value = data.conversation_id
      localStorage.setItem('currentConversationId', data.conversation_id)
      loadFiles(data.conversation_id)
    }

    messages.value.push({
      role: 'assistant',
      content: data.answer,
      time: new Date(),
      sources: data.retrieval_sources
    })
  } catch (error) {
    ElMessage.error('发送失败: ' + (error.response?.data?.error?.message || error.message))
    console.error('Error:', error)
  } finally {
    isTyping.value = false
    scrollToBottom()
  }
}

// 流式消息发送
async function sendStreamMessage(query) {
  isTyping.value = true
  
  const assistantMessage = {
    role: 'assistant',
    content: '',
    time: new Date(),
    sources: []
  }
  messages.value.push(assistantMessage)
  
  try {
    const response = await fetch(`${settings.apiBase}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        conversation_id: conversationId.value,
        top_k: settings.topK,
        model: settings.model
      })
    })

    const reader = response.body.getReader()
    const decoder = new TextDecoder()

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const chunk = decoder.decode(value)
      const lines = chunk.split('\n')

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6))
            
            if (data.conversation_id && !conversationId.value) {
              conversationId.value = data.conversation_id
              localStorage.setItem('currentConversationId', data.conversation_id)
              loadFiles(data.conversation_id)
            }
            
            if (data.token) {
              assistantMessage.content += data.token
              // 触发更新
              messages.value = [...messages.value]
              scrollToBottom()
            }
            
            if (data.error) {
              throw new Error(data.error)
            }
          } catch (e) {
            // 忽略解析错误
          }
        }
      }
    }
  } catch (error) {
    assistantMessage.content += '\n\n[错误: ' + error.message + ']'
    messages.value = [...messages.value]
    ElMessage.error('流式输出失败: ' + error.message)
  } finally {
    isTyping.value = false
    scrollToBottom()
  }
}

// 加载历史
async function loadHistory(convId) {
  try {
    const response = await axios.get(`${settings.apiBase}/history/${convId}`)
    if (response.data.messages) {
      messages.value = response.data.messages.map(m => ({
        role: m.role,
        content: m.content,
        time: new Date(m.timestamp)
      }))
    }
  } catch (error) {
    console.error('加载历史失败:', error)
  }
}

// 文件处理
function handleFileChange(file) {
  uploadFile(file.raw)
}

async function uploadFile(file) {
  if (!conversationId.value) {
    // 创建临时对话 ID
    conversationId.value = 'temp_' + Date.now()
  }

  const formData = new FormData()
  formData.append('file', file)

  try {
    const response = await axios.post(
      `${settings.apiBase}/conversations/${conversationId.value}/files`,
      formData,
      {
        headers: { 'Content-Type': 'multipart/form-data' }
      }
    )

    const data = response.data
    
    if (data.conversation_id && conversationId.value.startsWith('temp_')) {
      conversationId.value = data.conversation_id
      localStorage.setItem('currentConversationId', data.conversation_id)
    }

    ElMessage.success('上传成功，正在处理...')
    files.value.push(data)
    
    // 轮询文件状态
    pollFileStatus(data.file_id)
  } catch (error) {
    ElMessage.error('上传失败: ' + (error.response?.data?.error?.message || error.message))
  }
}

async function loadFiles(convId) {
  if (!convId || convId.startsWith('temp_')) return
  
  try {
    const response = await axios.get(
      `${settings.apiBase}/conversations/${convId}/files`
    )
    files.value = response.data.files || []
  } catch (error) {
    console.error('加载文件失败:', error)
  }
}

async function deleteFile(fileId) {
  try {
    await ElMessageBox.confirm('确定要删除这个文件吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    await axios.delete(
      `${settings.apiBase}/conversations/${conversationId.value}/files/${fileId}`
    )
    
    files.value = files.value.filter(f => f.file_id !== fileId)
    ElMessage.success('文件已删除')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

async function pollFileStatus(fileId) {
  const maxAttempts = 30
  let attempts = 0

  const interval = setInterval(async () => {
    attempts++
    if (attempts > maxAttempts) {
      clearInterval(interval)
      return
    }

    try {
      const response = await axios.get(
        `${settings.apiBase}/conversations/${conversationId.value}/files`
      )
      
      const file = response.data.files.find(f => f.file_id === fileId)
      if (file) {
        const index = files.value.findIndex(f => f.file_id === fileId)
        if (index !== -1) {
          files.value[index] = file
        }
        
        if (file.status === 'ready' || file.status === 'error') {
          clearInterval(interval)
          if (file.status === 'ready') {
            ElMessage.success('文件处理完成，可以提问了！')
          }
        }
      }
    } catch (error) {
      console.error('轮询失败:', error)
    }
  }, 2000)
}

// ==================== 工具函数 ====================

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

function formatTime(date) {
  return new Date(date).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  })
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

function getFileIcon(filename) {
  const ext = filename.split('.').pop().toLowerCase()
  const icons = {
    pdf: 'Document',
    doc: 'Document',
    docx: 'Document',
    txt: 'DocumentCopy',
    md: 'DocumentCopy',
    csv: 'DataLine',
    xlsx: 'DataLine'
  }
  return icons[ext] || 'Document'
}

function getFileIconClass(filename) {
  const ext = filename.split('.').pop().toLowerCase()
  return 'file-icon-' + ext
}

function getStatusType(status) {
  const types = {
    pending: 'warning',
    ingesting: 'primary',
    ready: 'success',
    error: 'danger'
  }
  return types[status] || 'info'
}

function getStatusText(status) {
  const texts = {
    pending: '等待中',
    ingesting: '处理中',
    ready: '就绪',
    error: '错误'
  }
  return texts[status] || status
}

function renderMarkdown(text) {
  // 简单的 Markdown 渲染
  return text
    .replace(/\n/g, '<br>')
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
}

// 监听消息变化，自动滚动
watch(messages, () => {
  scrollToBottom()
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.app-container {
  height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}

.main-container {
  height: 100%;
  background: white;
  border-radius: 16px;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}

/* 侧边栏样式 */
.sidebar {
  background: #f8fafc;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  padding: 20px;
}

.sidebar-header {
  margin-bottom: 20px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-text {
  font-size: 24px;
  font-weight: 700;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.logo-subtitle {
  font-size: 13px;
  color: #94a3b8;
  margin-top: 4px;
  margin-left: 40px;
}

.sidebar-actions {
  margin-bottom: 24px;
}

.new-chat-btn {
  width: 100%;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
}

.new-chat-btn:hover {
  opacity: 0.9;
}

/* 文件区域 */
.file-section {
  flex: 1;
  overflow-y: auto;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 16px;
}

.upload-area {
  margin-bottom: 16px;
}

.upload-area :deep(.el-upload-dragger) {
  width: 100%;
  height: 140px;
  border: 2px dashed #cbd5e1;
  border-radius: 12px;
  background: #f8fafc;
}

.upload-area :deep(.el-upload-dragger:hover) {
  border-color: #667eea;
  background: #f1f5f9;
}

.upload-icon {
  font-size: 32px;
  color: #94a3b8;
  margin-bottom: 8px;
}

.upload-text {
  text-align: center;
}

.upload-text div:first-child {
  font-size: 14px;
  color: #475569;
  margin-bottom: 4px;
}

.upload-hint {
  font-size: 12px;
  color: #94a3b8;
}

/* 文件列表 */
.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.empty-files {
  text-align: center;
  padding: 40px 20px;
  color: #94a3b8;
}

.empty-files p {
  margin-top: 12px;
  font-size: 13px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  background: white;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  transition: all 0.2s;
}

.file-item:hover {
  border-color: #cbd5e1;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
}

.file-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
}

.file-icon-pdf { background: #ef4444; }
.file-icon-doc, .file-icon-docx { background: #3b82f6; }
.file-icon-txt, .file-icon-md { background: #10b981; }
.file-icon-csv, .file-icon-xlsx { background: #f59e0b; }
.file-icon-default { background: #64748b; }

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: 13px;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-size {
  font-size: 11px;
  color: #94a3b8;
}

.file-status {
  flex-shrink: 0;
}

/* 系统信息 */
.system-info {
  margin-top: auto;
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: #64748b;
  padding: 0 4px;
}

/* 聊天区域 */
.chat-area {
  display: flex;
  flex-direction: column;
  padding: 0;
}

.chat-header {
  padding: 16px 24px;
  border-bottom: 1px solid #e2e8f0;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.chat-title {
  font-size: 16px;
  font-weight: 600;
  color: #1e293b;
}

.conv-tag {
  font-family: monospace;
}

/* 消息容器 */
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.welcome-message {
  text-align: center;
  padding: 60px 20px;
  color: #64748b;
}

.welcome-message h2 {
  margin: 20px 0 12px;
  color: #1e293b;
}

.welcome-message p {
  margin-bottom: 24px;
}

.quick-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
}

.quick-action-btn {
  border-radius: 20px;
}

/* 消息样式 */
.message {
  display: flex;
  gap: 12px;
  max-width: 85%;
}

.message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-avatar .el-avatar {
  background: #f1f5f9;
}

.message-avatar .el-avatar.user {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.message-content-wrapper {
  max-width: calc(100% - 48px);
}

.message-content {
  padding: 14px 18px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.7;
  word-wrap: break-word;
}

.message.user .message-content {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-bottom-right-radius: 4px;
}

.message.assistant .message-content {
  background: #f1f5f9;
  color: #1e293b;
  border-bottom-left-radius: 4px;
}

.message-time {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 6px;
  text-align: right;
}

.message.user .message-time {
  text-align: left;
}

/* 检索来源 */
.message-sources {
  margin-top: 12px;
}

.source-item {
  padding: 10px;
  background: white;
  border-radius: 8px;
  margin-bottom: 8px;
}

.source-score {
  font-size: 12px;
  color: #94a3b8;
  margin-left: 8px;
}

.source-content {
  font-size: 12px;
  color: #64748b;
  margin-top: 6px;
  line-height: 1.5;
}

/* 思考中指示器 */
.typing-indicator {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 16px 20px;
  background: #f1f5f9;
  border-radius: 16px;
  border-bottom-left-radius: 4px;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  background: #94a3b8;
  border-radius: 50%;
  animation: typing 1.4s infinite ease-in-out both;
}

.typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
.typing-indicator span:nth-child(2) { animation-delay: -0.16s; }

@keyframes typing {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

/* 输入区域 */
.input-area {
  padding: 20px 24px;
  border-top: 1px solid #e2e8f0;
}

.input-wrapper {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  background: #f8fafc;
  padding: 12px 16px;
  border-radius: 16px;
  border: 1px solid #e2e8f0;
  transition: all 0.2s;
}

.input-wrapper:focus-within {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.message-input :deep(.el-textarea__inner) {
  border: none;
  background: transparent;
  resize: none;
  padding: 0;
  font-size: 14px;
  line-height: 1.6;
  box-shadow: none;
}

.message-input :deep(.el-textarea__inner:focus) {
  box-shadow: none;
}

.send-btn {
  flex-shrink: 0;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
}

.send-btn:hover:not(:disabled) {
  opacity: 0.9;
}

.input-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 12px;
  color: #94a3b8;
}

/* 代码样式 */
.message-content pre {
  background: #1e293b;
  color: #e2e8f0;
  padding: 16px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 8px 0;
}

.message-content code {
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 13px;
}

.message.user .message-content code {
  background: rgba(255, 255, 255, 0.2);
}

/* 滚动条样式 */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}

::-webkit-scrollbar-track {
  background: transparent;
}

::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}

::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* 响应式 */
@media (max-width: 768px) {
  .app-container {
    padding: 0;
  }
  
  .main-container {
    border-radius: 0;
  }
  
  .sidebar {
    display: none;
  }
  
  .message {
    max-width: 95%;
  }
}
</style>
