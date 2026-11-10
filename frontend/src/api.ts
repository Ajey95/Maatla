export const API = import.meta.env.VITE_API_URL || (import.meta.env.PROD ? location.origin : 'http://127.0.0.1:8008')

export interface Client { id: string; name: string; created_at: string }
export interface Fact { id: string; field: string; value: string; source_kind: string; source_id: string | null; observed_at: string; valid_to: string | null }
export interface PendingFact { id: string; field: string; value: string; source_utterance_id: string }
export interface Utterance { id: string; room_id: string; role: 'agent' | 'buyer'; text: string; meaning: string | null; language: string | null; origin: string; created_at: string }
export interface Evidence { kind: string; title?: string; url?: string | null; text?: string; updated_at?: string; utterance_id?: string }
export interface Suggestion { id: string; headline: string; why: string; reply_hindi: string; provider: string; utterance_id: string; evidence: Evidence[]; created_at: string }
export interface Knowledge { id: string; title: string; body: string; url: string | null; source_kind: string; updated_at: string; expires_at: string | null }
export interface Brief { current: Fact[]; history: Fact[]; commitments: { id: string; text: string; due_at: string | null; owner: string }[]; recent_utterances: Utterance[] }
export interface RoomState { room: { id: string; client_id: string; created_at: string; ended_at: string | null }; client: Client; utterances: Utterance[]; brief?: Brief; pending_facts?: PendingFact[]; suggestions?: Suggestion[]; knowledge?: Knowledge[] }
export interface RoomCreated { id: string; client_id: string; agent_token: string; buyer_token: string }
export interface CallSummary { room_id: string; started_at: string; ended_at: string | null; buyer_requirements: { field: string; value: string; source_id: string | null }[]; concerns: { value: string; source_id: string | null }[]; site_visit: string | null; commitments: { id: string; text: string; owner: string; due_at: string | null }[]; pending_review: PendingFact[]; conversation_excerpt: { role: string; text: string; utterance_id: string }[]; note: string }

export async function request<T>(path: string, method = 'GET', body?: unknown, token?: string): Promise<T> {
  const agentKey = sessionStorage.getItem('maatla-agent-key') || ''
  const response = await fetch(API + path, {
    method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(token ? { 'X-Room-Token': token } : {}), ...(agentKey ? { 'X-Agent-Key': agentKey } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text.length < 220 ? text : response.statusText)
  }
  return response.json() as Promise<T>
}
