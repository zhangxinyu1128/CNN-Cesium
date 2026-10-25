<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { askAssistant, type AssistantCitation, type AssistantContext } from '@/services/assistantApi'

interface AssistantMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  mode?: string
  citations?: AssistantCitation[]
}

const props = defineProps<{
  context: AssistantContext
}>()

const open = ref(false)
const question = ref('')
const pending = ref(false)
const error = ref('')
const scrollElement = ref<HTMLElement | null>(null)
const sequence = ref(0)
const suggestedQuestions = ref([
  '当前在线模型使用了 ERA5 吗？',
  '24 小时预测误差应该怎么解释？',
  '这个项目的 AI+ 创新点是什么？',
  '当前台风的预测结果如何解释？'
])
const messages = ref<AssistantMessage[]>([
  {
    id: 0,
    role: 'assistant',
    text: '你好，我可以根据项目证据和当前页面状态，解释模型、ERA5、预测误差与 AI+ 设计。',
    mode: 'local_grounded'
  }
])

const selected = computed(() => {
  const value = props.context.selected_typhoon
  if (!value || typeof value !== 'object') return '未选择台风'
  const item = value as Record<string, unknown>
  return String(item.name || item.ename || item.tfbh || '未选择台风')
})

const forecastLabel = computed(() => {
  const value = props.context.forecast
  if (!value || typeof value !== 'object') return '尚未运行预测'
  const lead = (value as Record<string, unknown>).lead_hours
  return typeof lead === 'number' ? `${lead} 小时预测` : '尚未运行预测'
})

const modeLabel = (mode?: string) => {
  if (mode === 'deepseek_grounded') return 'DeepSeek · 证据约束'
  return '本地知识库 · 证据约束'
}

async function scrollToLatest() {
  await nextTick()
  if (scrollElement.value) scrollElement.value.scrollTop = scrollElement.value.scrollHeight
}

async function submit(value = question.value) {
  const text = value.trim()
  if (!text || pending.value) return
  question.value = ''
  error.value = ''
  messages.value.push({ id: ++sequence.value, role: 'user', text })
  pending.value = true
  await scrollToLatest()
  try {
    const result = await askAssistant(text, props.context)
    messages.value.push({
      id: ++sequence.value,
      role: 'assistant',
      text: result.answer,
      mode: result.mode,
      citations: result.citations
    })
    if (result.suggested_questions?.length) suggestedQuestions.value = result.suggested_questions
  } catch (requestError) {
    error.value = requestError instanceof Error ? requestError.message : '助手请求失败'
  } finally {
    pending.value = false
    await scrollToLatest()
  }
}

function submitForm() {
  void submit()
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    submitForm()
  }
}

watch(open, (value) => {
  if (value) void scrollToLatest()
})
</script>

<template>
  <div class="typhoon-assistant" :class="{ 'is-open': open }">
    <section v-if="open" class="assistant-panel" aria-label="台风研判智能助手">
      <header class="assistant-header">
        <div>
          <span class="assistant-kicker">AI + EVIDENCE</span>
          <h2>台风研判助手</h2>
          <p>{{ selected }} · {{ forecastLabel }}</p>
        </div>
        <button class="assistant-close" type="button" title="关闭助手" aria-label="关闭助手" @click="open = false">×</button>
      </header>

      <div ref="scrollElement" class="assistant-messages" aria-live="polite">
        <article v-for="message in messages" :key="message.id" class="assistant-message" :class="message.role">
          <span class="message-role">{{ message.role === 'user' ? '你' : 'AI' }}</span>
          <div class="message-body">
            <p>{{ message.text }}</p>
            <small v-if="message.role === 'assistant' && message.mode" class="message-mode">{{ modeLabel(message.mode) }}</small>
            <details v-if="message.citations?.length" class="message-citations">
              <summary>查看 {{ message.citations.length }} 条依据</summary>
              <div v-for="citation in message.citations" :key="citation.title" class="citation">
                <strong>{{ citation.title }}</strong>
                <span>{{ citation.excerpt }}</span>
                <small>{{ citation.source }}</small>
              </div>
            </details>
          </div>
        </article>
        <div v-if="pending" class="assistant-pending"><span></span><span></span><span></span><em>正在检索项目证据</em></div>
      </div>

      <div class="assistant-suggestions">
        <button v-for="item in suggestedQuestions.slice(0, 4)" :key="item" type="button" @click="submit(item)">
          {{ item }}
        </button>
      </div>

      <form class="assistant-form" @submit.prevent="submitForm">
        <textarea
          v-model="question"
          rows="2"
          maxlength="800"
          placeholder="问模型、误差、ERA5 或当前预测…"
          aria-label="输入问题"
          @keydown="handleKeydown"
        ></textarea>
        <button type="submit" :disabled="pending || !question.trim()" title="发送问题" aria-label="发送问题">↑</button>
      </form>
      <p v-if="error" class="assistant-error">{{ error }}</p>
      <footer class="assistant-footer">回答只依据项目数据，不替代官方预警</footer>
    </section>

    <button class="assistant-launcher" type="button" :aria-expanded="open" aria-label="打开台风研判智能助手" title="打开台风研判智能助手" @click="open = !open">
      <span class="launcher-mark">AI</span>
      <span class="launcher-label">研判助手</span>
    </button>
  </div>
</template>

<style scoped>
.typhoon-assistant {
  position: fixed;
  right: 22px;
  bottom: 140px;
  z-index: 80;
  font-family: 'Microsoft YaHei', Inter, sans-serif;
}

.assistant-launcher {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 42px;
  border: 1px solid rgba(103, 232, 208, 0.72);
  border-radius: 3px;
  padding: 0 13px 0 9px;
  color: #06212a;
  background: #67e8d0;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.34);
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
}

.typhoon-assistant.is-open .assistant-launcher {
  display: none;
}

.assistant-launcher:hover,
.assistant-launcher:focus-visible {
  background: #a6f7e7;
  outline: 2px solid rgba(247, 200, 115, 0.8);
  outline-offset: 2px;
}

.launcher-mark {
  display: grid;
  width: 25px;
  height: 25px;
  place-items: center;
  border: 1px solid rgba(6, 33, 42, 0.48);
  font-size: 10px;
  letter-spacing: 0;
}

.assistant-panel {
  position: fixed;
  top: 108px;
  right: 0;
  bottom: 138px;
  display: grid;
  grid-template-rows: auto minmax(180px, 1fr) auto auto auto;
  width: max(min(25vw, 430px), calc(20vw + 4px));
  overflow: hidden;
  border: 1px solid rgba(103, 232, 208, 0.55);
  border-radius: 4px;
  color: #eaf6f5;
  background: rgba(5, 22, 31, 0.98);
  box-shadow: 0 20px 55px rgba(0, 0, 0, 0.48);
}

.assistant-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 15px 16px 12px;
  border-bottom: 1px solid rgba(143, 207, 205, 0.16);
  background: rgba(8, 36, 48, 0.92);
}

.assistant-kicker {
  color: #67e8d0;
  font-size: 14px;
  letter-spacing: 0.14em;
}

.assistant-header h2 {
  margin: 4px 0 2px;
  color: #f0fffc;
  font-size: 26px;
  font-weight: 700;
}

.assistant-header p {
  max-width: 280px;
  margin: 0;
  overflow: hidden;
  color: #8eabb0;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assistant-close {
  width: 28px;
  height: 28px;
  border: 1px solid rgba(143, 207, 205, 0.35);
  border-radius: 2px;
  color: #cfe8e6;
  background: transparent;
  cursor: pointer;
  font-size: 26px;
  line-height: 1;
}

.assistant-close:hover,
.assistant-close:focus-visible {
  color: #06212a;
  background: #67e8d0;
  outline: none;
}

.assistant-messages {
  min-height: 0;
  overflow-y: auto;
  padding: 13px 12px 8px;
  scrollbar-color: #356a76 transparent;
}

.assistant-message {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 13px;
}

.assistant-message.user {
  flex-direction: row-reverse;
}

.message-role {
  display: grid;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid rgba(103, 232, 208, 0.42);
  border-radius: 2px;
  color: #67e8d0;
  background: rgba(103, 232, 208, 0.08);
  font-size: 14px;
  font-weight: 700;
}

.user .message-role {
  border-color: rgba(247, 200, 115, 0.44);
  color: #f7c873;
  background: rgba(247, 200, 115, 0.08);
}

.message-body {
  max-width: calc(100% - 38px);
  border: 1px solid rgba(143, 207, 205, 0.16);
  border-radius: 3px;
  padding: 9px 10px;
  background: rgba(14, 39, 51, 0.82);
}

.user .message-body {
  border-color: rgba(247, 200, 115, 0.23);
  background: rgba(60, 48, 27, 0.52);
}

.message-body p {
  margin: 0;
  color: #d9ecea;
  font-size: 19px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.message-mode {
  display: block;
  margin-top: 6px;
  color: #67e8d0;
  font-size: 13px;
}

.message-citations {
  margin-top: 8px;
  border-top: 1px solid rgba(143, 207, 205, 0.14);
  padding-top: 6px;
}

.message-citations summary {
  color: #f7c873;
  cursor: pointer;
  font-size: 15px;
}

.citation {
  display: grid;
  gap: 3px;
  margin-top: 7px;
  padding-left: 7px;
  border-left: 2px solid rgba(247, 200, 115, 0.55);
}

.citation strong {
  color: #eaf6f5;
  font-size: 14px;
}

.citation span,
.citation small {
  color: #91afb3;
  font-size: 13px;
  line-height: 1.45;
}

.citation small {
  color: #67e8d0;
  overflow-wrap: anywhere;
}

.assistant-pending {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 0 10px 36px;
  color: #8eabb0;
  font-size: 14px;
}

.assistant-pending span {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #67e8d0;
  animation: assistant-pulse 1.1s infinite ease-in-out;
}

.assistant-pending span:nth-child(2) { animation-delay: 0.14s; }
.assistant-pending span:nth-child(3) { animation-delay: 0.28s; }

.assistant-pending em {
  margin-left: 4px;
  font-style: normal;
}

@keyframes assistant-pulse {
  0%, 80%, 100% { opacity: 0.35; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-2px); }
}

.assistant-suggestions {
  display: flex;
  gap: 5px;
  overflow-x: auto;
  padding: 7px 12px;
  border-top: 1px solid rgba(143, 207, 205, 0.12);
  scrollbar-width: thin;
}

.assistant-suggestions button {
  flex: 0 0 auto;
  min-height: 26px;
  border: 1px solid rgba(103, 232, 208, 0.28);
  border-radius: 2px;
  padding: 3px 7px;
  color: #9ed0ca;
  background: rgba(103, 232, 208, 0.05);
  cursor: pointer;
  font-size: 15px;
  white-space: nowrap;
}

.assistant-suggestions button:hover,
.assistant-suggestions button:focus-visible {
  color: #06212a;
  background: #67e8d0;
  outline: none;
}

.assistant-form {
  display: flex;
  gap: 7px;
  padding: 9px 12px 8px;
  border-top: 1px solid rgba(143, 207, 205, 0.16);
}

.assistant-form textarea {
  min-width: 0;
  flex: 1;
  resize: none;
  border: 1px solid #356a76;
  border-radius: 2px;
  padding: 8px;
  color: #eaf6f5;
  background: #0a2a38;
  font-size: 18px;
  line-height: 1.45;
  outline: none;
}

.assistant-form textarea:focus {
  border-color: #67e8d0;
}

.assistant-form textarea::placeholder {
  color: #759398;
}

.assistant-form button {
  align-self: stretch;
  width: 38px;
  border: 1px solid rgba(103, 232, 208, 0.55);
  border-radius: 2px;
  color: #06212a;
  background: #67e8d0;
  cursor: pointer;
  font-size: 28px;
  line-height: 1;
}

.assistant-form button:disabled {
  color: #789096;
  background: #2b4249;
  cursor: not-allowed;
}

.assistant-error {
  margin: 0 12px 7px;
  color: #ffb1a2;
  font-size: 14px;
  line-height: 1.4;
}

.assistant-footer {
  padding: 0 12px 9px;
  color: #749399;
  font-size: 13px;
  line-height: 1.4;
}

@media (max-width: 760px) {
  .typhoon-assistant {
    right: 8px;
    bottom: 180px;
  }

  .assistant-panel {
    top: 108px;
    right: 8px;
    bottom: 184px;
    width: calc(100vw - 16px);
  }

  .assistant-launcher {
    min-height: 38px;
    padding-right: 9px;
  }

  .launcher-label {
    display: none;
  }
}

@media (max-height: 560px) and (min-width: 761px) {
  .assistant-panel {
    top: 56px;
    bottom: 68px;
  }
}
</style>
