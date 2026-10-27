<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { queryAssistant } from '@/services/typhoonApi'
import type {
  AssistantContext,
  AssistantHistoricalCase,
  AssistantSource,
  AssistantTimelinePoint
} from '@/types/typhoon'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: AssistantSource[]
  caveats?: string[]
  historicalCases?: AssistantHistoricalCase[]
  timeline?: AssistantTimelinePoint[]
  error?: boolean
}

interface PanelPosition {
  x: number
  y: number
}

interface PanelSize {
  width: number
  height: number
}

const props = defineProps<{
  context: AssistantContext
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'show-historical-case', historicalCase: AssistantHistoricalCase): void
}>()

const QUICK_QUESTIONS = [
  '在线预测模型是什么',
  'ERA5 融合有效吗',
  '与官方预报差多少',
  '误差范围口径',
  '数据范围',
  '找相似历史台风',
  '当前预测时间轴',
  '南京会受影响吗',
  '防台准备建议'
]

const messages = ref<ChatMessage[]>([])
const input = ref('')
const loading = ref(false)
const isTyping = ref(false)
const messageList = ref<HTMLDivElement | null>(null)
const panelElement = ref<HTMLElement | null>(null)
const dragging = ref(false)
const resizing = ref(false)

const POSITION_STORAGE_KEY = 'cnn-cesium-assistant-position'
const DEFAULT_SIZE: PanelSize = { width: 400, height: 640 }

function clampSize(next: PanelSize): PanelSize {
  if (typeof window === 'undefined') return next
  const maxWidth = Math.max(1, window.innerWidth - 24)
  const maxHeight = Math.max(1, window.innerHeight - 24)
  return {
    width: Math.min(Math.max(next.width, Math.min(300, maxWidth)), maxWidth),
    height: Math.min(Math.max(next.height, Math.min(360, maxHeight)), maxHeight)
  }
}

const size = ref<PanelSize>(clampSize(DEFAULT_SIZE))

function getDefaultPosition(): PanelPosition {
  if (typeof window === 'undefined') return { x: 18, y: 116 }
  const panelSize = clampSize(size.value)
  return {
    x: Math.max(12, window.innerWidth - panelSize.width - 18),
    y: Math.max(12, Math.min(116, window.innerHeight - panelSize.height - 12))
  }
}

const position = ref<PanelPosition>(getDefaultPosition())
let dragOrigin: {
  pointerId: number
  pointerX: number
  pointerY: number
  panelX: number
  panelY: number
} | null = null
let resizeOrigin: {
  pointerId: number
  pointerX: number
  pointerY: number
  width: number
  height: number
} | null = null

function clampPosition(next: PanelPosition): PanelPosition {
  if (typeof window === 'undefined') return next
  const maxX = Math.max(12, window.innerWidth - size.value.width - 12)
  const maxY = Math.max(12, window.innerHeight - size.value.height - 12)
  return {
    x: Math.min(Math.max(next.x, 12), maxX),
    y: Math.min(Math.max(next.y, 12), maxY)
  }
}

function restorePosition() {
  try {
    const stored = window.localStorage.getItem(POSITION_STORAGE_KEY)
    if (stored) {
      const parsed = JSON.parse(stored) as Partial<PanelPosition & PanelSize>
      if (Number.isFinite(parsed.width) && Number.isFinite(parsed.height)) {
        size.value = clampSize({ width: parsed.width!, height: parsed.height! })
      }
      position.value = clampPosition({
        x: Number.isFinite(parsed.x) ? parsed.x! : getDefaultPosition().x,
        y: Number.isFinite(parsed.y) ? parsed.y! : getDefaultPosition().y
      })
      return
    }
  } catch {
    // Keep the default position when browser storage is unavailable.
  }
  position.value = clampPosition(position.value)
}

function savePosition() {
  try {
    window.localStorage.setItem(POSITION_STORAGE_KEY, JSON.stringify({ ...position.value, ...size.value }))
  } catch {
    // The panel remains draggable when browser storage is unavailable.
  }
}

function startDrag(event: PointerEvent) {
  if (event.button !== 0 || (event.target as HTMLElement).closest('button')) return
  const header = event.currentTarget as HTMLElement
  dragOrigin = {
    pointerId: event.pointerId,
    pointerX: event.clientX,
    pointerY: event.clientY,
    panelX: position.value.x,
    panelY: position.value.y
  }
  header.setPointerCapture(event.pointerId)
  dragging.value = true
  event.preventDefault()
}

function movePanel(event: PointerEvent) {
  if (!dragOrigin || event.pointerId !== dragOrigin.pointerId) return
  position.value = clampPosition({
    x: dragOrigin.panelX + event.clientX - dragOrigin.pointerX,
    y: dragOrigin.panelY + event.clientY - dragOrigin.pointerY
  })
}

function finishDrag(event: PointerEvent) {
  if (!dragOrigin || event.pointerId !== dragOrigin.pointerId) return
  const header = event.currentTarget as HTMLElement
  if (header.hasPointerCapture(event.pointerId)) header.releasePointerCapture(event.pointerId)
  dragOrigin = null
  dragging.value = false
  savePosition()
}

function startResize(event: PointerEvent) {
  if (event.button !== 0) return
  const handle = event.currentTarget as HTMLElement
  resizeOrigin = {
    pointerId: event.pointerId,
    pointerX: event.clientX,
    pointerY: event.clientY,
    width: size.value.width,
    height: size.value.height
  }
  handle.setPointerCapture(event.pointerId)
  resizing.value = true
  event.preventDefault()
  event.stopPropagation()
}

function resizePanel(event: PointerEvent) {
  if (!resizeOrigin || event.pointerId !== resizeOrigin.pointerId) return
  size.value = clampSize({
    width: resizeOrigin.width + event.clientX - resizeOrigin.pointerX,
    height: resizeOrigin.height + event.clientY - resizeOrigin.pointerY
  })
  position.value = clampPosition(position.value)
}

function finishResize(event: PointerEvent) {
  if (!resizeOrigin || event.pointerId !== resizeOrigin.pointerId) return
  const handle = event.currentTarget as HTMLElement
  if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId)
  resizeOrigin = null
  resizing.value = false
  savePosition()
}

function keepPanelInView() {
  size.value = clampSize(size.value)
  position.value = clampPosition(position.value)
  savePosition()
}

onMounted(() => {
  restorePosition()
  window.addEventListener('resize', keepPanelInView)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', keepPanelInView)
})

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
      caveats: answer.caveats?.length ? answer.caveats : undefined,
      historicalCases: answer.historical_cases?.length ? answer.historical_cases : undefined,
      timeline: answer.forecast_timeline?.length ? answer.forecast_timeline : undefined
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
  <section
    v-if="open"
    ref="panelElement"
    class="assistant-panel"
    :class="{ 'is-resizing': resizing }"
    :style="{ left: `${position.x}px`, top: `${position.y}px`, width: `${size.width}px`, height: `${size.height}px` }"
    aria-label="AI 研判助手"
  >
    <header
      class="assistant-header"
      :class="{ 'is-dragging': dragging }"
      @pointerdown="startDrag"
      @pointermove="movePanel"
      @pointerup="finishDrag"
      @pointercancel="finishDrag"
    >
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
        <div v-if="msg.historicalCases?.length" class="assistant-historical-cases">
          <button
            v-for="historicalCase in msg.historicalCases"
            :key="historicalCase.tfbh"
            type="button"
            @click="emit('show-historical-case', historicalCase)"
          >
            <span>{{ historicalCase.year }} · {{ historicalCase.name }}</span>
            <small>{{ historicalCase.mean_distance_km.toFixed(0) }} km 前缀平均差 · 地图查看</small>
          </button>
        </div>
        <ol v-if="msg.timeline?.length" class="assistant-timeline">
          <li v-for="point in msg.timeline" :key="point.lead_hours">
            <strong>+{{ point.lead_hours }} h</strong>
            <span>{{ point.lat.toFixed(2) }}°N · {{ point.lng.toFixed(2) }}°E</span>
            <small v-if="point.location_radius_90_km != null">校准半径 {{ point.location_radius_90_km.toFixed(0) }} km</small>
          </li>
        </ol>
      </div>
    </div>

    <form class="assistant-input" @submit.prevent="send()">
      <input v-model="input" type="text" maxlength="200" placeholder="输入问题…" @keydown.enter="onEnter" />
      <button type="submit" :disabled="loading || isTyping || !input.trim()">{{ loading ? '…' : '发送' }}</button>
    </form>
    <button
      class="assistant-resize-handle"
      type="button"
      aria-label="调整 AI 研判助手窗口大小"
      title="拖动以调整窗口大小"
      @pointerdown="startResize"
      @pointermove="resizePanel"
      @pointerup="finishResize"
      @pointercancel="finishResize"
    />
  </section>
</template>

<style scoped>
.assistant-panel {
  position: fixed;
  z-index: 9999;
  min-width: min(300px, calc(100vw - 24px));
  min-height: min(360px, calc(100dvh - 24px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px;
  border: 1px solid rgba(43, 185, 224, 0.62);
  border-radius: 3px;
  color: #d8f4ff;
  background: linear-gradient(180deg, rgba(10, 42, 71, 0.97), rgba(5, 27, 48, 0.97));
  backdrop-filter: blur(14px) saturate(130%);
  -webkit-backdrop-filter: blur(14px) saturate(130%);
  box-shadow: 0 6px 22px rgba(0, 0, 0, 0.45), 0 0 18px rgba(31, 151, 198, 0.12);
  font-family: 'Microsoft YaHei', sans-serif;
  box-sizing: border-box;
  pointer-events: auto;
}

.assistant-panel.is-resizing {
  user-select: none;
}

.assistant-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin: -16px -16px 12px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(68, 174, 214, 0.25);
  background: rgba(12, 44, 74, 0.92);
  cursor: grab;
  touch-action: none;
  user-select: none;
}

.assistant-header.is-dragging {
  cursor: grabbing;
}

.assistant-title {
  padding-left: 10px;
  border-left: 3px solid #2bb9e0;
}

.assistant-kicker {
  display: block;
  color: #7fc3de;
  font-size: 11px;
  letter-spacing: 2px;
}

.assistant-title h2 {
  margin: 2px 0 0;
  color: #a8e8ff;
  font-size: 18px;
  font-weight: 600;
}

.assistant-close {
  border: 0;
  background: transparent;
  color: #9cc8de;
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
  border: 1px solid rgba(68, 174, 214, 0.25);
  border-radius: 3px;
  background: rgba(13, 43, 71, 0.25);
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
  color: #7fc3de;
  flex-shrink: 0;
}

.assistant-context-row strong {
  color: #d8f4ff;
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
  border: 1px solid rgba(68, 174, 214, 0.3);
  border-radius: 2px;
  background: rgba(13, 43, 71, 0.5);
  color: #9cc8de;
  font-size: 12px;
  padding: 5px 12px;
  cursor: pointer;
}

.assistant-chips button:hover {
  background: rgba(31, 169, 201, 0.35);
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
  border-radius: 2px;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}

.assistant-message.user {
  align-items: flex-end;
}

.assistant-message.user .assistant-bubble {
  background: rgba(31, 169, 201, 0.3);
  color: #d8f4ff;
  border: 1px solid rgba(43, 185, 224, 0.35);
}

.assistant-message.assistant .assistant-bubble {
  background: rgba(13, 43, 71, 0.55);
  color: #d8f4ff;
  border: 1px solid rgba(68, 174, 214, 0.2);
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
  color: #7fc3de;
  font-size: 11px;
  line-height: 1.5;
  word-break: break-all;
}

.assistant-caveats li {
  color: #f2d24b;
  font-size: 11px;
  line-height: 1.5;
}

.assistant-historical-cases {
  display: grid;
  gap: 6px;
  max-width: 94%;
}

.assistant-historical-cases button {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid rgba(68, 174, 214, 0.32);
  border-radius: 2px;
  color: #d8f4ff;
  background: rgba(13, 43, 71, 0.78);
  text-align: left;
  cursor: pointer;
}

.assistant-historical-cases button:hover,
.assistant-historical-cases button:focus-visible {
  border-color: #2bb9e0;
  background: rgba(31, 169, 201, 0.22);
}

.assistant-historical-cases small {
  flex: 0 0 auto;
  color: #7fc3de;
  font-size: 10px;
}

.assistant-timeline {
  display: grid;
  gap: 0;
  max-width: 94%;
  margin: 2px 0 0;
  padding: 0;
  list-style: none;
  border-left: 1px solid rgba(43, 185, 224, 0.5);
}

.assistant-timeline li {
  position: relative;
  display: grid;
  grid-template-columns: 52px 1fr;
  gap: 2px 10px;
  padding: 6px 8px 6px 12px;
  color: #d8f4ff;
  font-size: 11px;
}

.assistant-timeline li::before {
  position: absolute;
  top: 11px;
  left: -4px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #2bb9e0;
  content: '';
}

.assistant-timeline strong {
  color: #69d2f0;
}

.assistant-timeline small {
  grid-column: 2;
  color: #83a9bd;
  font-size: 10px;
}

.assistant-input {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.assistant-input input {
  flex: 1;
  min-width: 0;
  border: 1px solid rgba(43, 185, 224, 0.35);
  border-radius: 3px;
  background: rgba(13, 43, 71, 0.72);
  color: #d8f4ff;
  font-size: 13px;
  padding: 8px 10px;
  outline: none;
}

.assistant-input input::placeholder {
  color: #6c9fbb;
}

.assistant-input button {
  border: 1px solid rgba(43, 185, 224, 0.62);
  border-radius: 3px;
  background: rgba(31, 169, 201, 0.22);
  color: #d8f4ff;
  font-size: 13px;
  padding: 8px 14px;
  cursor: pointer;
}

.assistant-input button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.assistant-resize-handle {
  position: absolute;
  right: 1px;
  bottom: 1px;
  width: 22px;
  height: 22px;
  border: 0;
  padding: 0;
  background: repeating-linear-gradient(135deg, transparent 0 4px, rgba(127, 195, 222, 0.8) 4px 5px, transparent 5px 8px);
  clip-path: polygon(100% 0, 100% 100%, 0 100%);
  cursor: nwse-resize;
  touch-action: none;
}

.assistant-resize-handle:focus-visible {
  outline: 2px solid #f2d24b;
  outline-offset: -3px;
}

@media (max-width: 520px) {
  .assistant-panel {
    min-width: min(280px, calc(100vw - 24px));
  }
}
</style>
