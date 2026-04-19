<template>
  <div class="trace-panel">
    <div class="trace-header">
      <div class="trace-title">
        <el-icon size="18"><DataLine /></el-icon>
        <span>LANGGRAPH 实时追踪</span>
      </div>
      <el-tag v-if="runningCount > 0" type="primary" size="small" effect="dark" class="running-tag">
        RUNNING {{ runningCount }}
      </el-tag>
      <el-tag v-else-if="hasTraces" type="success" size="small" effect="dark" class="running-tag">
        COMPLETE
      </el-tag>
    </div>

    <div class="trace-timeline">
      <div
        v-for="(item, index) in displaySteps"
        :key="item.id"
        :class="['trace-step', item.status]"
      >
        <div class="step-indicator">
          <div class="step-pulse" v-if="item.status === 'running'"></div>
          <div class="step-dot">
            <el-icon v-if="item.status === 'success'" size="14" color="#10b981"><Check /></el-icon>
            <el-icon v-else-if="item.status === 'error'" size="14" color="#ef4444"><Close /></el-icon>
            <span v-else-if="item.status === 'running'" class="dot-spinner"></span>
            <span v-else class="dot-idle"></span>
          </div>
          <div v-if="index < displaySteps.length - 1" class="step-line" :class="item.status"></div>
        </div>
        <div class="step-content">
          <div class="step-name">{{ item.label }}</div>
          <div class="step-meta">
            <span v-if="item.status === 'running'" class="meta-running">执行中</span>
            <span v-else-if="item.status === 'success'" class="meta-success">已完成</span>
            <span v-else-if="item.status === 'error'" class="meta-error">失败</span>
            <span v-else class="meta-pending">等待中</span>
            <span v-if="item.duration" class="meta-duration">{{ item.duration }}ms</span>
          </div>
          <div v-if="item.payload && Object.keys(item.payload).length > 0" class="step-payload">
            <div
              v-for="(val, key) in previewPayload(item.payload)"
              :key="key"
              class="payload-item"
            >
              <span class="payload-key">{{ key }}:</span>
              <span class="payload-val">{{ formatValue(val) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="inspector-section">
      <div class="inspector-header" @click="inspectorExpanded = !inspectorExpanded">
        <el-icon size="14"><Monitor /></el-icon>
        <span>STATE INSPECTOR</span>
        <el-icon size="14" class="expand-icon" :class="{ expanded: inspectorExpanded }">
          <ArrowDown />
        </el-icon>
      </div>
      <transition name="inspector-slide">
        <div v-show="inspectorExpanded" class="inspector-body">
          <pre class="inspector-code"><code v-html="highlightedState"></code></pre>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  traces: { type: Array, default: () => [] }
})

const inspectorExpanded = ref(true)

const stepLabels = {
  session: '会话初始化',
  intent: '意图解析',
  clarify: '澄清提示',
  retrieve: '知识库检索',
  tool_subgraph: '工具调用',
  generate: '最终生成',
  memory_manage: '记忆管理',
  archive: '归档存储'
}

const displaySteps = computed(() => {
  const nodes = ['session', 'intent', 'clarify', 'retrieve', 'tool_subgraph', 'generate', 'memory_manage', 'archive']
  const result = []
  
  for (const node of nodes) {
    const nodeTraces = props.traces.filter(t => t.node === node)
    if (nodeTraces.length === 0) {
      result.push({
        id: node + '_pending',
        node,
        label: stepLabels[node] || node,
        status: 'pending',
        payload: {},
        duration: null
      })
      continue
    }
    
    const nodeEnd = nodeTraces.find(t => t.step === 'node_end')
    const error = nodeTraces.find(t => t.status === 'error')
    const running = nodeTraces.find(t => t.status === 'running')
    const success = nodeTraces.filter(t => t.status === 'success')
    
    let status = 'pending'
    let payload = {}
    let duration = null
    
    if (error) {
      status = 'error'
      payload = error.payload || {}
    } else if (nodeEnd && nodeEnd.status === 'success') {
      status = 'success'
      payload = success.length > 0 ? success[success.length - 1].payload || {} : (nodeEnd.payload || {})
      const starts = nodeTraces.filter(t => t.status === 'running').map(t => t.ts)
      const ends = success.map(t => t.ts)
      if (starts.length && ends.length) {
        duration = Math.round((Math.max(...ends) - Math.min(...starts)) * 1000)
      }
    } else if (running) {
      status = 'running'
      payload = running.payload || {}
    }
    
    result.push({
      id: node + '_' + status,
      node,
      label: stepLabels[node] || node,
      status,
      payload,
      duration
    })
  }
  
  return result
})

const runningCount = computed(() => displaySteps.value.filter(s => s.status === 'running').length)
const hasTraces = computed(() => props.traces.length > 0)

const latestState = computed(() => {
  if (props.traces.length === 0) return {}
  const last = props.traces[props.traces.length - 1]
  return {
    node: last.node,
    step: last.step,
    status: last.status,
    ts: last.ts,
    payload: last.payload
  }
})

const highlightedState = computed(() => {
  const json = JSON.stringify(latestState.value, null, 2)
  return syntaxHighlight(json)
})

function previewPayload(payload) {
  const limited = {}
  const keys = Object.keys(payload).slice(0, 4)
  for (const k of keys) {
    limited[k] = payload[k]
  }
  return limited
}

function formatValue(val) {
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'number') return String(val)
  if (Array.isArray(val)) return `[${val.length} items]`
  if (typeof val === 'object' && val !== null) return JSON.stringify(val).slice(0, 40)
  return String(val).slice(0, 60)
}

function syntaxHighlight(json) {
  if (!json) return ''
  return json
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/(".*?")/g, '<span class="json-key">$1</span>')
    .replace(/\b(true|false|null)\b/g, '<span class="json-bool">$1</span>')
    .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="json-number">$1</span>')
}
</script>

<style scoped>
.trace-panel {
  width: 100%;
  height: 100%;
  background: linear-gradient(180deg, #0b0f19 0%, #111827 100%);
  color: #e2e8f0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-left: 1px solid rgba(255, 255, 255, 0.06);
}

.trace-header {
  padding: 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.trace-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #f8fafc;
}

.running-tag {
  font-family: 'Courier New', monospace;
  font-weight: 600;
  letter-spacing: 0.5px;
}

.trace-timeline {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.trace-step {
  display: flex;
  gap: 14px;
  position: relative;
  padding-bottom: 20px;
}

.step-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
}

.step-dot {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: rgba(30, 41, 59, 0.9);
  border: 2px solid rgba(148, 163, 184, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2;
  transition: all 0.3s ease;
}

.trace-step.running .step-dot {
  border-color: #6366f1;
  background: rgba(99, 102, 241, 0.15);
  box-shadow: 0 0 12px rgba(99, 102, 241, 0.4);
}

.trace-step.success .step-dot {
  border-color: #10b981;
  background: rgba(16, 185, 129, 0.15);
  box-shadow: 0 0 10px rgba(16, 185, 129, 0.3);
}

.trace-step.error .step-dot {
  border-color: #ef4444;
  background: rgba(239, 68, 68, 0.15);
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.3);
}

.step-pulse {
  position: absolute;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: rgba(99, 102, 241, 0.4);
  animation: pulse-ring 1.5s infinite;
  z-index: 1;
}

@keyframes pulse-ring {
  0% { transform: scale(1); opacity: 0.6; }
  70% { transform: scale(2); opacity: 0; }
  100% { transform: scale(2); opacity: 0; }
}

.dot-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid rgba(99, 102, 241, 0.3);
  border-top-color: #818cf8;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.dot-idle {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(148, 163, 184, 0.4);
}

.step-line {
  width: 2px;
  flex: 1;
  min-height: 30px;
  background: rgba(148, 163, 184, 0.15);
  margin-top: 6px;
  transition: background 0.4s ease;
}

.step-line.success {
  background: linear-gradient(180deg, #10b981 0%, rgba(148, 163, 184, 0.15) 100%);
}

.step-content {
  flex: 1;
  padding-top: 2px;
}

.step-name {
  font-size: 14px;
  font-weight: 600;
  color: #f1f5f9;
  margin-bottom: 4px;
}

.step-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  margin-bottom: 8px;
}

.meta-running {
  color: #818cf8;
  font-weight: 500;
}

.meta-success {
  color: #34d399;
}

.meta-error {
  color: #f87171;
}

.meta-pending {
  color: #64748b;
}

.meta-duration {
  color: #94a3b8;
  font-family: 'Courier New', monospace;
  font-size: 11px;
  margin-left: auto;
}

.step-payload {
  background: rgba(15, 23, 42, 0.6);
  border: 1px solid rgba(255, 255, 255, 0.04);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.5;
}

.payload-item {
  display: flex;
  gap: 6px;
  margin-bottom: 2px;
}

.payload-key {
  color: #94a3b8;
  font-family: 'Courier New', monospace;
  flex-shrink: 0;
}

.payload-val {
  color: #cbd5e1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-section {
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  background: rgba(2, 6, 23, 0.5);
}

.inspector-header {
  padding: 14px 20px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.8px;
  color: #94a3b8;
  cursor: pointer;
  user-select: none;
  transition: color 0.2s;
}

.inspector-header:hover {
  color: #e2e8f0;
}

.expand-icon {
  margin-left: auto;
  transition: transform 0.3s ease;
}

.expand-icon.expanded {
  transform: rotate(180deg);
}

.inspector-body {
  padding: 0 20px 16px;
  max-height: 260px;
  overflow: auto;
}

.inspector-slide-enter-active,
.inspector-slide-leave-active {
  transition: all 0.25s ease;
}

.inspector-slide-enter-from,
.inspector-slide-leave-to {
  opacity: 0;
  transform: translateY(-6px);
  max-height: 0;
}

.inspector-code {
  margin: 0;
  padding: 14px;
  background: #020617;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.05);
  font-size: 12px;
  line-height: 1.6;
  font-family: 'Fira Code', 'Courier New', monospace;
  color: #cbd5e1;
  overflow-x: auto;
}

:deep(.json-key) {
  color: #93c5fd;
}

:deep(.json-bool) {
  color: #f472b6;
}

:deep(.json-number) {
  color: #fcd34d;
}
</style>
