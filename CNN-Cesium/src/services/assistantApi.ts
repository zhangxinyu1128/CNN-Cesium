export type AssistantContext = Record<string, unknown>

export interface AssistantCitation {
  title: string
  source: string
  excerpt: string
}

export interface AssistantResponse {
  answer: string
  mode: string
  citations: AssistantCitation[]
  suggested_questions: string[]
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || detail?.code || `HTTP ${response.status}`
    throw new Error(`请求失败: ${message}`)
  }
  return response.json() as Promise<T>
}

export function askAssistant(question: string, context: AssistantContext) {
  return request<AssistantResponse>('/api/assistant/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, context })
  })
}
