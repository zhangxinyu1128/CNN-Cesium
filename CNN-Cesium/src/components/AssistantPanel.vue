<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { queryAssistant } from '@/services/typhoonApi'
import type { AssistantContext, AssistantSource } from '@/types/typhoon'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: AssistantSource[]
  caveats?: string[]
  error?: boolean
}

const props = defineProps<{
  context: AssistantContext
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const QUICK_QUESTIONS = [
  '在线预测模型是什么',
  'ERA5 融合有效吗',
  '与官方预报差多少',
  '误差范围口径',
  '数据范围'
]

const messages = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const isTyping = ref(false)
const messageList = ref<HTMLDivElement | null>(null)

async function send(text?: string) {
  if (isTyping.value || loading.value) return
  const content = (text ?? input.value).trim()
  if (!content) return
  messages.value.push({ role: 'user', content })
  input.value = ''
  loading.value = true
  isTyping.value = true
  try {
    const answer = await queryAssistant(content, props.context)
    messages.value.push({
      role: 'assistant',
      content: answer.answer,
      sources: answer.sources,
      caveats: answer.caveats?.length ? answer.caveats : undefined
    })
  } catch (error) {
    messages.value.push({
      role: 'assistant',
      content: error instanceof Error ? error.message : '请求失败',
      error: true
    })
  } finally {
    loading.value = false
    isTyping.value = false
    await nextTick()
    if (messageList.value) {
      messageList.value.scrollTop = messageList.value.scrollHeight
    }
  }
}

function onEnter(event: KeyboardEvent) {
  event.preventDefault()
  if (isTyping.value || loading.value) return
  send()
}
</script>

<template>
  <section v-if="open" class="assistant-panel" aria-label="AI 研判助手">
    <header class="assistant-header">
      <div class="assistant-title">
        <span class="assistant-kicker">TYphoon INTELLIGENCE</span>
        <h2>AI 研判助手</h2>
      </div>
      <button class="assistant-close" type="button" title="关闭" @click="emit('close')">×</button>
    </header>

    <div class="assistant-context">
      <div class="assistant-context-row">
        <span>台风</span>
        <strong>{{ context.typhoon_name || context.typhoon_id || '--' }}</strong>
      </div>
      <div class="assistant-context-row">
        <span>时效</span>
        <strong>{{ context.lead_hours != null ? `${context.lead_hours} h` : '--' }}</strong>
      </div>
      <div class="assistant-context-row">
        <span>预测位置</span>
        <strong>{{ context.forecast_position ? `${context.forecast_position.lat?.toFixed(1)}°N · ${context.forecast_position.lng?.toFixed(1)}°E` : '--' }}</strong>
      </div>
      <div class="assistant-context-row">
        <span>误差半径</span>
        <strong>{{ context.location_radius_90_km != null ? `${context.location_radius_90_km} km` : '--' }}</strong>
      </div>
      <div class="assistant-context-row">
        <span>ERA5 风场</span>
        <strong>{{ context.era5 ? `${context.era5.speed_ms ?? '--'} m/s · ${context.era5.direction_deg ?? '--'}°` : '--' }}</strong>
      </div>
    </div>

    <div class="assistant-chips">
      <button v-for="question in QUICK_QUESTIONS" :key="question" type="button" @click="send(question)">{{ question }}</button>
    </div>

    <div ref="messageList" class="assistant-messages">
      <div v-for="(msg, index) in messages" :key="index" class="assistant-message" :class="[msg.role, { error: msg.error }]">
        <div class="assistant-bubble">{{ msg.content }}</div>
        <ul v-if="msg.sources?.length" class="assistant-sources">
          <li v-for="(source, sourceIndex) in msg.sources" :key="sourceIndex">{{ source.path }}</li>
        </ul>
        <ul v-if="msg.caveats?.length" class="assistant-caveats">
          <li v-for="(caveat, caveatIndex) in msg.caveats" :key="caveatIndex">{{ caveat }}</li>
        </ul>
      </div>
    </div>

    <form class="assistant-input" @submit.prevent="send()">
      <input v-model="input" type="text" placeholder="输入问题…" @keydown.enter="onEnter" />
      <button type="submit" :disabled="loading || isTyping || !input.trim()">{{ loading ? '…' : '发送' }}</button>
    </form>
  </section>
</template>

<style scoped>
.assistant-panel {
  position: fixed;
  top: 68px;
  right: 18px;
  bottom: 80px;
  z-index: 9999;
  width: min(400px, calc(100vw - 36px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px;
  border: 1px solid rgba(92, 211, 210, 0.78);
  border-radius: 3px;
  color: #e4ffff;
  background: rgba(3, 24, 38, 0.92);
  backdrop-filter: blur(14px) saturate(130%);
  -webkit-backdrop-filter: blur(14px) saturate(130%);
  box-shadow: 0 12px 35px #000a;
  font-family: 'Microsoft YaHei', sans-serif;
  box-sizing: border-box;
  pointer-events: auto;
}

.assistant-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.assistant-title {
  padding-left: 10px;
  border-left: 3px solid #67e8d0;
}

.assistant-kicker {
  display: block;
  color: #67e8d0;
  font-size: 11px;
  letter-spacing: 2px;
}

.assistant-title h2 {
  margin: 2px 0 0;
  color: #f2ffff;
  font-size: 18px;
  font-weight: 600;
}

.assistant-close {
  border: 0;
  background: transparent;
  color: #9fd9d8;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}

.assistant-close:hover {
  color: #ffffff;
}

.assistant-context {
  padding: 10px 12px;
  border: 1px solid rgba(92, 211, 210, 0.32);
  border-radius: 3px;
  background: rgba(8, 40, 58, 0.55);
  margin-bottom: 12px;
}

.assistant-context-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  line-height: 1.7;
}

.assistant-context-row span {
  color: #8ecfd2;
  flex-shrink: 0;
}

.assistant-context-row strong {
  color: #e4ffff;
  font-weight: 600;
  text-align: right;
  word-break: break-all;
}

.assistant-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.assistant-chips button {
  border: 1px solid rgba(92, 211, 210, 0.5);
  border-radius: 999px;
  background: rgba(8, 40, 58, 0.55);
  color: #a9e8e7;
  font-size: 12px;
  padding: 5px 12px;
  cursor: pointer;
}

.assistant-chips button:hover {
  background: rgba(103, 232, 208, 0.16);
  color: #ffffff;
}

.assistant-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 2px;
}

.assistant-message {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.assistant-bubble {
  max-width: 88%;
  padding: 8px 11px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}

.assistant-message.user {
  align-items: flex-end;
}

.assistant-message.user .assistant-bubble {
  background: rgba(103, 232, 208, 0.22);
  color: #d7fffa;
  border: 1px solid rgba(103, 232, 208, 0.35);
}

.assistant-message.assistant .assistant-bubble {
  background: rgba(8, 40, 58, 0.75);
  color: #e4ffff;
  border: 1px solid rgba(92, 211, 210, 0.28);
}

.assistant-message.assistant.error .assistant-bubble {
  border-color: rgba(239, 68, 68, 0.6);
  color: #ffd7d7;
  background: rgba(88, 20, 20, 0.55);
}

.assistant-sources,
.assistant-caveats {
  margin: 0;
  padding-left: 16px;
  max-width: 88%;
}

.assistant-message.user .assistant-sources {
  margin-left: auto;
}

.assistant-sources li {
  color: #8ecfd2;
  font-size: 11px;
  line-height: 1.5;
  word-break: break-all;
}

.assistant-caveats li {
  color: #f7c873;
  font-size: 11px;
  line-height: 1.5;
}

.assistant-input {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.assistant-input input {
  flex: 1;
  min-width: 0;
  border: 1px solid rgba(92, 211, 210, 0.4);
  border-radius: 3px;
  background: rgba(8, 40, 58, 0.75);
  color: #e4ffff;
  font-size: 13px;
  padding: 8px 10px;
  outline: none;
}

.assistant-input input::placeholder {
  color: #6f9fa4;
}

.assistant-input button {
  border: 1px solid rgba(92, 211, 210, 0.78);
  border-radius: 3px;
  background: rgba(103, 232, 208, 0.16);
  color: #d7fffa;
  font-size: 13px;
  padding: 8px 14px;
  cursor: pointer;
}

.assistant-input button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
</style>
