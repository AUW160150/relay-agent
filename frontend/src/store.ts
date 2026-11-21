// Client state, REST helpers and the WebSocket to the orchestrator.
import { useSyncExternalStore } from 'react'

export type Proof = {
  seq: number; hash: string; ts: string; title: string; kind: 'ai' | 'rule' | 'person'; actor: string; model: string | null
  page: number | null; quote: string | null; check: { label: string; ok: boolean; by: string } | null; detail: string | null
  state: string; role: string; fixture: boolean
}
export type DotState = 'listening' | 'thinking' | 'acting' | 'waiting'
export type State = {
  token: string | null
  user: any | null
  snap: any | null
  pipeline: any[]
  dot: { state: DotState; caption: string }
  speaking: boolean
  canvas: boolean
  env: boolean
  page: string
  gate: string | null
  question: { text: string; finding: any; yes: string; no: string } | null
  sign: any | null
  pdf: { page: number; quote?: string | null } | null
  heard: { text: string; action: string } | null
  voice: 'off' | 'connecting' | 'on' | 'error'
  voiceError: string | null
  level: number
  toast: string | null
  uiError: string | null
  connected: boolean
  fast: boolean
}

const fast = new URLSearchParams(location.search).has('fast')
let state: State = {
  token: sessionStorage.getItem('relay.token'), user: null, snap: null, pipeline: [], dot: { state: 'waiting', caption: '' }, speaking: false,
  canvas: false, env: false, page: '', gate: null, question: null, sign: null, pdf: null, heard: null, voice: 'off', voiceError: null, level: 0,
  toast: null, uiError: null, connected: false, fast,
}
const subs = new Set<() => void>()
export const get = () => state
export function set(patch: Partial<State>) {
  state = { ...state, ...patch }
  subs.forEach(f => f())
}
export function useStore<T>(sel: (s: State) => T): T {
  return useSyncExternalStore(cb => { subs.add(cb); return () => subs.delete(cb) }, () => sel(state))
}

// ---- REST ----
let pending = 0
export const idle = async () => { while (pending > 0) await new Promise(r => setTimeout(r, 15)) }

export async function api(path: string, opts: { method?: string; body?: any; form?: FormData } = {}) {
  pending++
  try {
    const headers: Record<string, string> = {}
    if (state.token) headers.Authorization = `Bearer ${state.token}`
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
    const r = await fetch(path, { method: opts.method ?? (opts.body !== undefined || opts.form ? 'POST' : 'GET'), headers, body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined) })
    const data = r.headers.get('content-type')?.includes('json') ? await r.json() : null
    if (!r.ok) {
      const msg = typeof data?.detail === 'string' ? data.detail : `Request failed (${r.status})`
      if (r.status === 401 && path !== '/api/login' && path !== '/api/sign') logout()
      throw new Error(msg)
    }
    return data
  } finally { pending-- }
}

/** A change made through the application UI. Errors are kept so the agent can read them. */
export async function uiPost(path: string, body: any): Promise<boolean> {
  try {
    await api(path, { body })
    set({ uiError: null })
    await refresh()
    return true
  } catch (e: any) {
    set({ uiError: e.message })
    return false
  }
}

export async function refresh() {
  if (!state.token) return
  const snap = await api('/api/snapshot')
  set({ snap, pipeline: snap.pipeline ?? [], question: snap.question ?? null })
}

export function toast(msg: string) {
  set({ toast: msg })
  setTimeout(() => state.toast === msg && set({ toast: null }), 2600)
}

// ---- session ----
let ws: WebSocket | null = null
type Handler = (msg: any) => void
const handlers: Record<string, Handler[]> = {}
export function on(type: string, fn: Handler) { (handlers[type] ??= []).push(fn) }
export function send(msg: any) { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)) }

export async function login(user_id: string, password: string) {
  const r = await api('/api/login', { body: { user_id, password } })
  sessionStorage.setItem('relay.token', r.token)
  set({ token: r.token, user: r.user })
  await connect()
}

export function logout() {
  sessionStorage.removeItem('relay.token')
  ws?.close(); ws = null
  set({ token: null, user: null, snap: null, canvas: false, gate: null, question: null, sign: null, pdf: null, connected: false })
}

export async function connect() {
  if (!state.token) return
  try { await refresh() } catch { return }
  const s = get().snap
  set({ user: s.user, page: state.page || s.pages[0], dot: s.dot?.caption ? s.dot : state.dot })
  ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws?token=${state.token}`)
  ws.onopen = () => { set({ connected: true }); send({ type: 'hello' }) }
  ws.onclose = () => set({ connected: false })
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data)
    ;(handlers[m.type] ?? []).forEach(h => h(m))
  }
}

on('dot', m => set({ dot: { state: m.state, caption: m.caption } }))
on('changed', () => { refresh() })
on('proof', () => { refresh() })
on('pipeline', m => set({ pipeline: m.pipeline }))
on('gate', m => set({ gate: m.kind }))
on('question', m => set({ question: m.text ? m : null }))
on('open_sign', m => set({ sign: m }))
on('close_sign', () => set({ sign: null }))
on('open_pdf', m => set({ pdf: { page: m.page, quote: m.quote } }))
on('canvas', m => set({ canvas: !!m.show }))
on('env', m => set({ env: !!m.show }))
on('reset', () => { set({ canvas: false, env: false, gate: null, question: null, sign: null, pdf: null }); connect() })
on('utterance_result', m => {
  set({ heard: { text: m.transcript ?? state.heard?.text ?? '', action: m.action } })
  setTimeout(() => set({ heard: null }), 2500)
})

let uid = 0
/** Text typed into the agent box, or a transcript from the voice session. */
export function utter(transcript: string, extra: { audio?: string; mime?: string; typed?: boolean } = {}) {
  if (!transcript.trim()) return
  set({ heard: { text: transcript, action: 'pending' } })
  send({ type: 'utterance', id: ++uid, transcript, ...extra })
}
export function decide(intent: string, option?: string) { send({ type: 'decision', intent, option }) }
