<template>
  <div class="app-container">
    <el-container class="main-container">
      <!-- 左侧边栏 -->
      <el-aside width="320px" class="sidebar">
        <div class="sidebar-header">
          <div class="logo">
            <el-icon size="28" color="var(--accent)"><MagicStick /></el-icon>
            <span class="logo-text">RAG Agent</span>
          </div>
          <p class="logo-subtitle">Intelligent Copilot</p>
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
        
        <!-- 对话列表区域 -->
        <div class="conversation-section" :class="{ 'is-collapsed': isHistoryCollapsed }">
          <div class="section-title" @click="isHistoryCollapsed = !isHistoryCollapsed" style="cursor: pointer; user-select: none; display: flex; justify-content: space-between;">
            <div style="display: flex; align-items: center; gap: 10px;">
              <el-icon><CopyDocument /></el-icon>
              <span>历史对话</span>
              <el-icon class="collapse-icon" :style="{ transform: isHistoryCollapsed ? 'rotate(-90deg)' : 'rotate(0deg)' }"><ArrowDown /></el-icon>
            </div>
            <el-button 
              v-if="conversations.length > 0"
              link 
              size="small" 
              @click.stop="refreshConversations"
              class="refresh-btn"
            >
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>
          
          <el-collapse-transition>
            <div v-show="!isHistoryCollapsed" class="conversation-list">
              <div v-if="conversations.length === 0" class="empty-conversations">
                <el-icon size="24" color="var(--text-tertiary)"><ChatLineRound /></el-icon>
                <p>暂无历史记录</p>
              </div>
              
              <div
                v-for="conv in conversations"
                :key="conv.conversation_id"
                :class="['conversation-item', { active: conversationId === conv.conversation_id }]"
                @click="switchConversation(conv.conversation_id)"
              >
                <div class="conversation-icon">
                  <el-icon><ChatDotSquare /></el-icon>
                </div>
                <div class="conversation-info">
                  <div class="conversation-title" :title="conv.title">{{ conv.title }}</div>
                  <div class="conversation-meta">
                    <span>{{ formatTime(conv.updated_at) }}</span>
                    <span v-if="conv.message_count > 0" class="message-badge">
                      {{ conv.message_count }} 条消息
                    </span>
                  </div>
                </div>
                <el-button
                  type="danger"
                  link
                  size="small"
                  class="delete-conv-btn"
                  @click.stop="deleteConversation(conv.conversation_id)"
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </div>
            </div>
          </el-collapse-transition>
        </div>
        
        <!-- 文件管理区域 -->
        <div v-if="conversationId" class="file-section">
          <div class="section-title">
            <el-icon><Files /></el-icon>
            <span>知识库文件</span>
          </div>
          
          <!-- 上传区域 -->
          <input
            ref="fileInput"
            type="file"
            style="display: none"
            accept=".pdf,.docx,.doc,.txt,.md,.csv,.xlsx,.xls,.pptx,.html,.htm,.json,.yaml,.yml"
            @change="handleFileSelect"
          />
          <div class="upload-area" @click="fileInput?.click()">
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="upload-text">
              <div>拖拽或点击上传文件</div>
              <div class="upload-hint">支持 PDF, Word, Excel, PPT, Markdown, HTML等</div>
            </div>
          </div>
          
          <!-- 文件列表 -->
          <div class="file-list">
            <div v-if="files.length === 0" class="empty-files">
              <el-icon size="32" color="var(--text-tertiary)"><Box /></el-icon>
              <p>知识库为空</p>
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
              
              <div class="file-badges">
                <el-tag 
                  v-if="file.extract_method === 'vlm_ocr'"
                  type="success"
                  size="small"
                  class="file-badge"
                >
                  OCR
                </el-tag>
                <el-tag 
                  :type="getStatusType(file.status)" 
                  size="small"
                  class="file-badge"
                >
                  {{ getStatusText(file.status) }}
                </el-tag>
              </div>
              
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
            <el-tooltip content="LangGraph 追踪" placement="bottom">
              <el-button
                circle
                :type="showTracePanel ? 'primary' : 'default'"
                @click="showTracePanel = !showTracePanel"
              >
                <el-icon><DataLine /></el-icon>
              </el-button>
            </el-tooltip>
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
            <el-icon size="64" color="var(--border-hover)"><MagicStick /></el-icon>
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
                :class="msg.role"
              >
                <el-icon v-if="msg.role === 'user'"><UserFilled /></el-icon>
                <el-icon v-else><ChatDotRound /></el-icon>
              </el-avatar>
            </div>
            
            <div class="message-content-wrapper">
              <div class="message-content" v-html="renderMarkdown(msg.content)"></div>
              <div class="message-time">
                {{ formatTime(msg.time) }}
                <el-button
                  v-if="msg.role === 'assistant' && msg.message_id"
                  link
                  size="small"
                  type="danger"
                  class="rollback-btn"
                  @click="rollbackToMessage(msg.message_id)"
                >
                  回溯到此处
                </el-button>
              </div>
              
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
              <el-avatar :size="36" class="assistant">
              <el-icon><ChatDotRound /></el-icon>
            </el-avatar>
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
              v-if="!isTyping"
              type="primary"
              circle
              class="send-btn"
              :disabled="!inputMessage.trim()"
              @click="sendMessage"
            >
              <el-icon><Promotion /></el-icon>
            </el-button>
            <el-button
              v-else
              type="danger"
              circle
              class="send-btn"
              @click="stopGeneration"
            >
              <el-icon><CircleClose /></el-icon>
            </el-button>
          </div>
          <div class="input-hint">
            <el-icon size="12"><InfoFilled /></el-icon>
            <span>AI 生成内容仅供参考</span>
          </div>
        </div>
      </el-main>
      
      <el-aside v-if="showTracePanel" width="360px" class="trace-sidebar">
        <TracePanel :traces="traceEvents" />
      </el-aside>
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
import { ref, reactive, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import axios from 'axios'
import TracePanel from './components/TracePanel.vue'

// ==================== 状态管理 ====================
const messages = ref([])
const inputMessage = ref('')
const isTyping = ref(false)
const conversationId = ref(null)
const files = ref([])
const messagesContainer = ref(null)
const fileInput = ref(null)
const settingsVisible = ref(false)
const conversations = ref([])
const isHistoryCollapsed = ref(true) // 默认收起历史对话

// 设置
const settings = reactive({
  apiBase: '/api/v1',  // 使用相对路径，让 Vite 代理生效
  topK: 5,
  streaming: true,  // 默认开启真流式
  model: 'qwen3.5-omni-flash'
})

const abortController = ref(null)
const traceEvents = ref([])
const showTracePanel = ref(true)
const traceWs = ref(null)

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
  loadConversations()
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

// 加载对话列表
async function loadConversations() {
  try {
    const response = await axios.get(`${settings.apiBase}/conversations`)
    conversations.value = response.data.conversations || []
  } catch (error) {
    console.error('加载对话列表失败:', error)
  }
}

// 刷新对话列表
async function refreshConversations() {
  await loadConversations()
  ElMessage.success('已刷新')
}

// 切换对话
async function switchConversation(convId) {
  if (convId === conversationId.value) return
  
  disconnectTraceWs()
  conversationId.value = convId
  messages.value = []
  files.value = []
  traceEvents.value = []
  localStorage.setItem('currentConversationId', convId)
  
  // 加载历史消息
  await loadHistory(convId)
  // 加载文件
  await loadFiles(convId)
  
  ElMessage.success('已切换对话')
}

// 删除对话
async function deleteConversation(convId) {
  try {
    await ElMessageBox.confirm('确定要删除这个对话吗？相关的文件也会被删除。', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    await axios.delete(`${settings.apiBase}/conversations/${convId}`)
    
    // 从列表中移除
    conversations.value = conversations.value.filter(c => c.conversation_id !== convId)
    
    // 如果删除的是当前对话，清空界面
    if (conversationId.value === convId) {
      conversationId.value = null
      messages.value = []
      files.value = []
      localStorage.removeItem('currentConversationId')
    }
    
    ElMessage.success('对话已删除')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败: ' + (error.response?.data?.detail || error.message))
    }
  }
}

// 新建对话
async function startNewChat() {
  try {
    // 先创建新对话
    const response = await axios.post(`${settings.apiBase}/conversations`, {
      title: null  // 让后端自动生成标题
    })
    
    const newConv = response.data
    conversations.value.unshift(newConv)
    
    // 切换到新对话
    conversationId.value = newConv.conversation_id
    messages.value = []
    files.value = []
    localStorage.setItem('currentConversationId', newConv.conversation_id)
    
    ElMessage.success('已创建新对话')
  } catch (error) {
    ElMessage.error('创建对话失败: ' + (error.response?.data?.detail || error.message))
  }
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
      user_id: '1',
      conversation_id: conversationId.value,
      top_k: settings.topK,
      model: settings.model
    })

    const data = response.data
    
    if (!conversationId.value && data.conversation_id) {
      conversationId.value = data.conversation_id
      localStorage.setItem('currentConversationId', data.conversation_id)
      loadFiles(data.conversation_id)
      loadConversations()  // 刷新对话列表
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

// 停止生成
function stopGeneration() {
  if (abortController.value) {
    abortController.value.abort()
    abortController.value = null
    isTyping.value = false
  }
}

// WebSocket 追踪连接
function connectTraceWs(convId) {
  if (!convId || convId.startsWith('temp_')) return
  disconnectTraceWs()
  traceEvents.value = []
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/ws/trace/${convId}`
  const ws = new WebSocket(wsUrl)
  traceWs.value = ws

  ws.onopen = () => {
    console.log('[TraceWS] connected', convId)
  }
  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (data.type === 'trace') {
        traceEvents.value.push(data)
      }
    } catch (e) {
      // ignore
    }
  }
  ws.onerror = (e) => {
    console.error('[TraceWS] error', e)
  }
  ws.onclose = () => {
    traceWs.value = null
  }
}

function disconnectTraceWs() {
  if (traceWs.value) {
    traceWs.value.close()
    traceWs.value = null
  }
}

// 流式消息发送
async function sendStreamMessage(query) {
  isTyping.value = true
  traceEvents.value = []
  if (conversationId.value) {
    connectTraceWs(conversationId.value)
  }
  
  const assistantMessage = {
    role: 'assistant',
    content: '',
    time: new Date(),
    sources: []
  }
  messages.value.push(assistantMessage)
  
  abortController.value = new AbortController()
  
  try {
    const response = await fetch(`${settings.apiBase}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: abortController.value.signal,
      body: JSON.stringify({
        query,
        user_id: '1',
        conversation_id: conversationId.value,
        top_k: settings.topK,
        model: settings.model
      })
    })

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let newConvId = null

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
              newConvId = data.conversation_id
              connectTraceWs(newConvId)
            }
            
            if (data.content) {
              assistantMessage.content += data.content
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
    
    // 如果有新对话ID，更新并刷新列表
    if (newConvId) {
      conversationId.value = newConvId
      localStorage.setItem('currentConversationId', newConvId)
      loadFiles(newConvId)
      loadConversations()  // 刷新对话列表
    }
    
    // 流式结束后从 MySQL 重新加载历史，以获取 message_id（用于回溯）
    if (conversationId.value) {
      await loadHistory(conversationId.value)
    }
  } catch (error) {
    if (error.name === 'AbortError') {
      assistantMessage.content += '\n\n[已停止生成]'
    } else {
      assistantMessage.content += '\n\n[错误: ' + error.message + ']'
      ElMessage.error('流式输出失败: ' + error.message)
    }
    messages.value = [...messages.value]
  } finally {
    abortController.value = null
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
        time: new Date(m.timestamp),
        message_id: m.message_id
      }))
    }
  } catch (error) {
    console.error('加载历史失败:', error)
  }
}

// 回溯到指定消息
async function rollbackToMessage(messageId) {
  if (!conversationId.value || !messageId) return
  try {
    await ElMessageBox.confirm('回溯后，该消息及之后的所有记录将被永久删除。是否继续？', '确认回溯', {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    const response = await axios.post(
      `${settings.apiBase}/conversations/${conversationId.value}/rollback`,
      { target_message_id: messageId }
    )
    
    if (response.data.success) {
      ElMessage.success('已回溯到选定位置')
      traceEvents.value = []
      await loadHistory(conversationId.value)
    }
  } catch (error) {
    if (error !== 'cancel') {
      console.error('回溯失败:', error)
      ElMessage.error('回溯失败: ' + (error.response?.data?.detail || error.message))
    }
  }
}

// 文件处理
function handleFileSelect(event) {
  const file = event.target.files?.[0]
  console.log('[Upload] handleFileSelect called', file)
  if (file) {
    uploadFile(file)
  }
  // 重置 input 允许重复选择同一文件
  event.target.value = ''
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
  // 优化：延长轮询间隔，减少请求次数
  const maxAttempts = 20       // 最多查询20次（之前30次）
  const pollInterval = 3000    // 每3秒查询一次（之前2秒）
  
  let attempts = 0
  console.log(`[FilePoll] 开始轮询文件 ${fileId} 状态`)

  const interval = setInterval(async () => {
    attempts++
    if (attempts > maxAttempts) {
      clearInterval(interval)
      console.log(`[FilePoll] 轮询超时，文件ID: ${fileId}`)
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
          console.log(`[FilePoll] 文件处理完成，状态: ${file.status}`)
          if (file.status === 'ready') {
            ElMessage.success('文件处理完成，可以提问了！')
          } else {
            ElMessage.error('文件处理失败: ' + (file.error_message || '未知错误'))
          }
        }
      }
    } catch (error) {
      console.error('[FilePoll] 轮询失败:', error)
    }
  }, pollInterval)
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
    xlsx: 'DataLine',
    xls: 'DataLine',
    pptx: 'DataBoard',
    html: 'Link',
    htm: 'Link',
    json: 'DocumentCopy',
    yaml: 'DocumentCopy',
    yml: 'DocumentCopy'
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

onUnmounted(() => {
  disconnectTraceWs()
})
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Plus+Jakarta+Sans:wght@300;400;500;600&family=JetBrains+Mono:wght@400&display=swap');

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

:root {
  --bg-base: #f7f7f8;
  --bg-surface: #ffffff;
  --bg-subtle: #f3f4f6;
  --border-color: rgba(0, 0, 0, 0.06);
  --border-hover: rgba(0, 0, 0, 0.15);
  
  --text-primary: #111827;
  --text-secondary: #6b7280;
  --text-tertiary: #9ca3af;
  
  --accent: #0f172a;
  --accent-light: #f8fafc;
  
  --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.02);
  --shadow-md: 0 12px 32px rgba(0, 0, 0, 0.04);
  
  --radius-sm: 8px;
  --radius-md: 16px;
  --radius-lg: 24px;
}

body {
  font-family: 'Plus Jakarta Sans', sans-serif;
  background-color: var(--bg-base);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.app-container {
  height: 100vh;
  width: 100vw;
  padding: 24px 32px;
  background-image: 
    linear-gradient(to right, rgba(0,0,0,0.02) 1px, transparent 1px),
    linear-gradient(to bottom, rgba(0,0,0,0.02) 1px, transparent 1px);
  background-size: 60px 60px;
  display: flex;
  justify-content: center;
}

.main-container {
  width: 100%;
  max-width: 1600px;
  height: 100%;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  overflow: hidden;
}

/* 侧边栏样式 */
.sidebar {
  background: var(--bg-surface);
  border-right: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  padding: 32px 28px;
  position: relative;
}

.sidebar::after {
  content: '';
  position: absolute;
  top: 0; right: 0; bottom: 0;
  width: 1px;
  background: linear-gradient(to bottom, transparent, var(--border-color) 10%, var(--border-color) 90%, transparent);
}

.logo {
  display: flex;
  align-items: center;
  gap: 16px;
}

.logo .el-icon {
  color: var(--accent);
}

.logo-text {
  font-family: 'Playfair Display', serif;
  font-size: 26px;
  font-weight: 600;
  letter-spacing: -0.5px;
  color: var(--text-primary);
}

.logo-subtitle {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 2px;
  text-transform: uppercase;
  color: var(--text-tertiary);
  margin-top: 6px;
  margin-left: 44px;
}

.sidebar-actions {
  margin: 40px 0 32px;
}

.new-chat-btn {
  width: 100%;
  background: var(--bg-surface) !important;
  color: var(--text-primary) !important;
  border: 1px solid var(--border-color) !important;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-weight: 500;
  font-size: 13px;
  padding: 22px 0;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm) !important;
  transition: all 0.3s ease !important;
}

.new-chat-btn:hover {
  border-color: var(--accent) !important;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05) !important;
}
.new-chat-btn .el-icon { margin-right: 8px; }

/* 文件区域和标题 */
.file-section, .conversation-section {
  display: flex;
  flex-direction: column;
}

.file-section {
  flex: 1;
  overflow-y: auto;
}

.conversation-section {
  margin-bottom: 30px;
  display: flex;
  flex-direction: column;
}

.conversation-section:not(.is-collapsed) {
  max-height: 40vh;
}

.collapse-icon {
  margin-left: 'auto';
  transition: transform 0.3s ease;
  font-size: 14px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-tertiary);
  margin-bottom: 20px;
}

.section-title .el-icon {
  color: var(--text-secondary);
}

.upload-area {
  margin-bottom: 24px;
  width: 100%;
  height: 120px;
  border: 1px dashed var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-subtle);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.3s ease;
}

.upload-area:hover {
  border-color: var(--accent);
  background: var(--accent-light);
}

.upload-icon {
  font-size: 28px;
  color: var(--text-tertiary);
  margin-bottom: 12px;
  transition: color 0.3s;
}

.upload-area:hover .upload-icon {
  color: var(--accent);
}

.upload-text { text-align: center; }

.upload-text div:first-child {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 4px;
}

.upload-hint {
  font-size: 11px;
  color: var(--text-tertiary);
}

/* 列表展示 */
.file-list, .conversation-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding-right: 8px;
}

.empty-files, .empty-conversations {
  text-align: center;
  padding: 30px 20px;
  color: var(--text-tertiary);
  font-size: 12px;
}

.file-item, .conversation-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: transparent;
  cursor: pointer;
  transition: all 0.2s ease;
}

.file-item:hover, .conversation-item:hover {
  background: var(--bg-subtle);
}

.conversation-item.active {
  background: var(--accent-light);
}

.file-icon, .conversation-icon {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  flex-shrink: 0;
  font-size: 14px;
}

.conversation-item.active .conversation-icon {
  background: var(--accent);
  color: white;
  border-color: var(--accent);
}

.file-info, .conversation-info {
  flex: 1;
  min-width: 0;
}

.file-name, .conversation-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-size, .conversation-meta {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 4px;
  display: flex;
  gap: 8px;
}

.message-badge { color: var(--accent); font-weight: 500; }

.file-badges {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
}

.file-badges .el-tag {
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  border-radius: 4px;
}

.delete-conv-btn { opacity: 0; transition: opacity 0.2s; color: var(--text-tertiary) !important; }
.conversation-item:hover .delete-conv-btn { opacity: 1; }
.delete-conv-btn:hover { color: #ef4444 !important; }

.refresh-btn { margin-left: auto; color: var(--text-tertiary) !important; }
.refresh-btn:hover { color: var(--text-primary) !important; }

.system-info {
  margin-top: auto;
  padding-top: 24px;
}
.system-info .el-divider { margin: 12px 0; border-color: var(--border-color); }

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  color: var(--text-tertiary);
}

/* 聊天区域 */
.chat-area {
  display: flex;
  flex-direction: column;
  padding: 0;
  background: var(--bg-surface);
  position: relative;
}

.chat-header {
  padding: 24px 40px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-surface);
  z-index: 10;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.chat-title {
  font-family: 'Playfair Display', serif;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.conv-tag {
  background: var(--bg-subtle) !important;
  border: 1px solid var(--border-color) !important;
  color: var(--text-secondary) !important;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.5px;
  border-radius: 6px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-right .el-button {
  background: var(--bg-surface) !important;
  border: 1px solid var(--border-color) !important;
  color: var(--text-secondary) !important;
  transition: all 0.2s;
}
.header-right .el-button:hover {
  background: var(--bg-subtle) !important;
  color: var(--text-primary) !important;
}
.header-right .el-button--primary {
  border-color: var(--accent) !important;
  color: var(--accent) !important;
  background: var(--accent-light) !important;
}

/* 消息容器 */
.messages-container {
  flex: 1;
  overflow-y: auto;
  padding: 40px;
  display: flex;
  flex-direction: column;
  gap: 32px;
  scroll-behavior: smooth;
}

.welcome-message {
  text-align: center;
  padding: 100px 20px;
  color: var(--text-tertiary);
  max-width: 600px;
  margin: 0 auto;
}
.welcome-message .el-icon {
  margin-bottom: 24px;
  color: var(--border-hover);
}

.welcome-message h2 {
  font-family: 'Playfair Display', serif;
  font-size: 32px;
  font-weight: 400;
  color: var(--text-primary);
  margin-bottom: 16px;
}

.welcome-message p { margin-bottom: 40px; font-size: 15px; line-height: 1.6; }

.quick-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
}

.quick-action-btn {
  background: var(--bg-surface) !important;
  border: 1px solid var(--border-color) !important;
  color: var(--text-secondary) !important;
  border-radius: 100px !important;
  padding: 10px 20px !important;
  font-size: 13px !important;
  transition: all 0.2s ease !important;
}

.quick-action-btn:hover {
  border-color: var(--text-primary) !important;
  color: var(--text-primary) !important;
}

/* 消息样式 */
.message {
  display: flex;
  gap: 16px;
  max-width: 85%;
  animation: fade-in 0.4s ease-out both;
}

@keyframes fade-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

.message.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.message-avatar .el-avatar {
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
}

.message.user .message-avatar .el-avatar {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
}

.message-content-wrapper {
  max-width: calc(100% - 56px);
}

.message-content {
  padding: 16px 20px;
  border-radius: var(--radius-md);
  font-size: 14.5px;
  line-height: 1.7;
  color: var(--text-primary);
  word-wrap: break-word;
}

.message.user .message-content {
  background: var(--bg-subtle);
  border-top-right-radius: 4px;
}

.message.assistant .message-content {
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  box-shadow: 0 4px 20px rgba(0,0,0,0.015);
  border-top-left-radius: 4px;
}

.message-time {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: 'JetBrains Mono', monospace;
  margin-top: 8px;
  text-align: right;
}

.message.user .message-time { text-align: left; }

.rollback-btn {
  margin-left: 8px;
  font-size: 11px;
}

/* 检索来源 */
.message-sources { margin-top: 16px; }
.message-sources .el-collapse { border: none; }
.message-sources .el-collapse-item__header {
  background: transparent;
  color: var(--text-tertiary);
  border-bottom: 1px dashed var(--border-color);
  font-size: 12px;
}
.message-sources .el-collapse-item__wrap {
  background: transparent;
  border: none;
}

.source-item {
  padding: 16px;
  background: var(--bg-subtle);
  border-radius: var(--radius-md);
  margin: 12px 0;
}

.source-score {
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  color: var(--text-secondary);
  margin-left: 12px;
}

.source-content {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 8px;
  line-height: 1.6;
}

/* 思考中指示器 */
.typing-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 20px 24px;
  background: var(--bg-surface);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  border-top-left-radius: 4px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.015);
}

.typing-indicator span {
  width: 5px;
  height: 5px;
  background: var(--text-tertiary);
  border-radius: 50%;
  animation: gentle-pulse 1.4s infinite ease-in-out both;
}

.typing-indicator span:nth-child(1) { animation-delay: -0.32s; }
.typing-indicator span:nth-child(2) { animation-delay: -0.16s; }

@keyframes gentle-pulse {
  0%, 80%, 100% { transform: scale(0.8); opacity: 0.4; }
  40% { transform: scale(1.2); opacity: 1; }
}

/* 输入区域 */
.input-area {
  padding: 32px 40px;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-color);
}

.input-wrapper {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  background: var(--bg-surface);
  padding: 16px 20px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-hover);
  transition: all 0.3s;
  box-shadow: 0 2px 10px rgba(0,0,0,0.01);
}

.input-wrapper:focus-within {
  border-color: var(--accent);
  box-shadow: 0 4px 20px rgba(0,0,0,0.04);
}

.message-input :deep(.el-textarea__inner) {
  border: none !important;
  background: transparent !important;
  color: var(--text-primary) !important;
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 15px;
  box-shadow: none !important;
  resize: none;
  padding: 0;
  line-height: 1.6;
}

.message-input :deep(.el-textarea__inner::placeholder) {
  color: var(--text-tertiary);
}

.send-btn {
  flex-shrink: 0;
  background: var(--accent) !important;
  border: 1px solid var(--accent) !important;
  color: #fff !important;
  transition: all 0.2s ease !important;
  width: 44px;
  height: 44px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08) !important;
}

.send-btn:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(0,0,0,0.12) !important;
}

.input-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 16px;
  font-size: 11px;
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: var(--text-tertiary);
}

/* 代码样式 */
.message-content pre {
  background: #0d1117;
  border: 1px solid #1f2937;
  color: #e5e7eb;
  padding: 20px;
  border-radius: var(--radius-sm);
  overflow-x: auto;
  margin: 16px 0;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  line-height: 1.5;
}

.message-content code {
  color: #e11d48;
  background: #fee2e2;
  padding: 2px 6px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
}

.message.user .message-content code {
  background: #e5e7eb;
  color: #1f2937;
}

/* 滚动条 */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: transparent; margin: 4px; }
::-webkit-scrollbar-thumb { background: var(--border-hover); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-tertiary); }

/* 侧边追踪面板 */
.trace-sidebar {
  background: var(--bg-surface);
  border-left: 1px solid var(--border-color);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
</style>
