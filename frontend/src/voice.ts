// Voice: a Gemini Live session in the browser, authorised by an ephemeral token from our server.
// The Live model only listens and speaks. Every user turn is handed to the orchestrator
// (function call `handle_user_turn`), where the Decision 1 gate decides whether it was meant
// for the agent. What the agent says comes back from the orchestrator and is read out verbatim.
import { GoogleGenAI, Modality } from '@google/genai'
import { api, get, on, send, set } from './store'

const INSTRUCTION = `You are the voice of Relay, an assistant for clinical trial amendments. You do not decide or answer anything yourself.
1. Whenever you hear someone speak, call handle_user_turn with their exact words, then say nothing.
   Do this for every utterance, including ones that do not seem meant for you. Never reply to the speaker on your own.
2. When you receive a text message that starts with "SAY:", speak the text after it exactly, word for word, calm and brisk, and add nothing.
Never greet, never apologise, never comment, never ask questions of your own.
Always speak English, whatever language you hear or whatever the text looks like.`

const TOOLS = [{ functionDeclarations: [{
  name: 'handle_user_turn', description: 'Hand the words just heard to the orchestrator.', behavior: 'NON_BLOCKING',
  parameters: { type: 'OBJECT', properties: { transcript: { type: 'STRING', description: 'The exact words heard.' } }, required: ['transcript'] },
}] }]

let session: any = null
let micCtx: AudioContext | null = null
let outCtx: AudioContext | null = null
let stream: MediaStream | null = null
let playHead = 0
let sources: AudioBufferSourceNode[] = []
let ring: Int16Array[] = []          // recent mic audio, for the utterance gate
let ringSamples = 0
const RING_MAX = 16000 * 10
let heard = ''                       // input transcription of the current turn
let heardTimer: any = null
let handledTurn = false
let said = ''                        // what we asked the model to speak (to filter echo of our own voice)
const pendingCalls = new Map<number, { id: string; name: string }>()
let turnId = 0

const b64 = (buf: ArrayBuffer) => { let s = ''; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s) }
const unb64 = (s: string) => Uint8Array.from(atob(s), c => c.charCodeAt(0))

function wavOfRing(): string {
  const n = ring.reduce((a, c) => a + c.length, 0)
  const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf)
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)))
  w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true)
  v.setUint32(24, 16000, true); v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true)
  let o = 44
  for (const c of ring) { for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, c[i], true) }
  return b64(buf)
}

const WORKLET = `class P extends AudioWorkletProcessor{process(i){const c=i[0][0];if(c){const o=new Int16Array(c.length);for(let k=0;k<c.length;k++){const s=Math.max(-1,Math.min(1,c[k]));o[k]=s<0?s*0x8000:s*0x7fff}this.port.postMessage(o,[o.buffer])}return true}}registerProcessor('pcm',P)`

function stopPlayback() {
  sources.forEach(s => { try { s.stop() } catch { /* already stopped */ } })
  sources = []
  playHead = 0
  set({ speaking: false })
}

function play(pcm: Uint8Array) {
  if (!outCtx) return
  const i16 = new Int16Array(pcm.buffer, pcm.byteOffset, pcm.byteLength >> 1)
  const buf = outCtx.createBuffer(1, i16.length, 24000)
  const ch = buf.getChannelData(0)
  for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768
  const src = outCtx.createBufferSource()
  src.buffer = buf
  src.connect(outCtx.destination)
  playHead = Math.max(playHead, outCtx.currentTime + 0.02)
  src.start(playHead)
  playHead += buf.duration
  sources.push(src)
  set({ speaking: true })
  src.onended = () => { sources = sources.filter(s => s !== src); if (!sources.length) set({ speaking: false }) }
}

/** One user turn goes to the orchestrator exactly once, with the audio that carried it. */
function submitTurn(transcript: string, call?: { id: string; name: string }) {
  const t = transcript.trim()
  if (!t || handledTurn) { if (call) respond(call); return }
  handledTurn = true
  const id = ++turnId
  if (call) pendingCalls.set(id, call)
  set({ heard: { text: t, action: 'pending' } })
  send({ type: 'utterance', id: `v${id}`, transcript: t, audio: wavOfRing(), mime: 'audio/wav' })
  ring = []; ringSamples = 0
}

function respond(call: { id: string; name: string }) {
  // SILENT: the model must not start talking because a tool returned
  try { session?.sendToolResponse({ functionResponses: [{ id: call.id, name: call.name, response: { result: 'received' }, scheduling: 'SILENT' }] }) } catch { /* session closed */ }
}

on('utterance_result', m => {
  const id = Number(String(m.id ?? '').replace('v', ''))
  const call = pendingCalls.get(id)
  if (call) { respond(call); pendingCalls.delete(id) }
})

function onMessage(m: any) {
  const sc = m.serverContent
  if (m.toolCall?.functionCalls) {
    for (const fc of m.toolCall.functionCalls) {
      if (fc.name === 'handle_user_turn') submitTurn(String(fc.args?.transcript ?? heard), { id: fc.id, name: fc.name })
      else respond({ id: fc.id, name: fc.name })
    }
  }
  if (!sc) return
  if (sc.interrupted) stopPlayback()                 // barge-in: drop queued speech at once
  if (sc.inputTranscription?.text) {
    if (handledTurn && !heardTimer) { handledTurn = false; heard = '' }
    heard += sc.inputTranscription.text
    set({ dot: { ...get().dot, state: get().dot.state === 'waiting' ? 'listening' : get().dot.state } })
    clearTimeout(heardTimer)
    // fallback: if the model does not call the tool for this turn, hand it over ourselves
    heardTimer = setTimeout(() => { heardTimer = null; const t = heard; heard = ''; if (!handledTurn) submitTurn(t); handledTurn = false; if (get().dot.state === 'listening') set({ dot: { ...get().dot, state: 'waiting' } }) }, 1400)
  }
  for (const p of sc.modelTurn?.parts ?? []) if (p.inlineData?.data) play(unb64(p.inlineData.data))
}

export async function startVoice() {
  if (session || get().voice === 'connecting') return
  set({ voice: 'connecting', voiceError: null })
  try {
    const tok = await api('/api/live-token')
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
    const ai = new GoogleGenAI({ apiKey: tok.token, httpOptions: { apiVersion: tok.api_version } })
    outCtx = new AudioContext({ sampleRate: 24000 })
    session = await ai.live.connect({
      model: tok.model,
      config: {
        responseModalities: [Modality.AUDIO], systemInstruction: INSTRUCTION, tools: TOOLS as any,
        inputAudioTranscription: {}, sessionResumption: {},
        speechConfig: { languageCode: 'en-US' },
      },
      callbacks: {
        onopen: () => set({ voice: 'on' }),
        onmessage: onMessage,
        onerror: (e: any) => set({ voice: 'error', voiceError: String(e?.message ?? 'Voice connection error') }),
        onclose: () => { session = null; if (get().voice === 'on') set({ voice: 'off' }) },
      },
    })
    micCtx = new AudioContext({ sampleRate: 16000 })
    await micCtx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' })))
    const node = new AudioWorkletNode(micCtx, 'pcm')
    const analyser = micCtx.createAnalyser()
    analyser.fftSize = 256
    const srcNode = micCtx.createMediaStreamSource(stream)
    srcNode.connect(node)
    srcNode.connect(analyser)
    const lv = new Uint8Array(analyser.frequencyBinCount)
    const tick = () => {
      if (!session) return
      analyser.getByteTimeDomainData(lv)
      let peak = 0
      for (const x of lv) peak = Math.max(peak, Math.abs(x - 128))
      document.documentElement.style.setProperty('--lvl', (peak / 128).toFixed(2))
      requestAnimationFrame(tick)
    }
    tick()
    node.port.onmessage = ev => {
      const pcm = ev.data as Int16Array
      ring.push(pcm); ringSamples += pcm.length
      while (ringSamples > RING_MAX && ring.length > 1) ringSamples -= ring.shift()!.length
      try { session?.sendRealtimeInput({ audio: { data: b64(pcm.buffer as ArrayBuffer), mimeType: 'audio/pcm;rate=16000' } }) } catch { /* closed */ }
    }
  } catch (e: any) {
    stopVoice()
    set({ voice: 'error', voiceError: String(e?.message ?? e) })
  }
}

export function stopVoice() {
  try { session?.close() } catch { /* ignore */ }
  session = null
  stream?.getTracks().forEach(t => t.stop()); stream = null
  micCtx?.close(); micCtx = null
  outCtx?.close(); outCtx = null
  stopPlayback()
  set({ voice: 'off' })
}

/** Speak a line. Live voice when the session is on; otherwise the browser's own speech
 *  synthesis, which is a fallback only and is labelled as such in the UI. */
export function speak(text: string) {
  said = text
  if (session) {
    try { session.sendClientContent({ turns: [{ role: 'user', parts: [{ text: `SAY: ${text}` }] }], turnComplete: true }); return } catch { /* fall through */ }
  }
  if (get().fast || !('speechSynthesis' in window)) return
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text)
  // The browser's default voice follows the system language, which may not be English.
  u.lang = 'en-US'
  const v = englishVoice()
  if (v) u.voice = v
  u.rate = 1.08
  u.onstart = () => set({ speaking: true })
  u.onend = () => set({ speaking: false })
  window.speechSynthesis.speak(u)
}

function englishVoice(): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices()
  return voices.find(v => v.lang === 'en-US' && v.localService) ?? voices.find(v => v.lang === 'en-US')
    ?? voices.find(v => v.lang.startsWith('en')) ?? null
}
// voices load asynchronously in some browsers; asking once makes them available for the first line
if ('speechSynthesis' in window) { window.speechSynthesis.getVoices(); window.speechSynthesis.onvoiceschanged = () => {} }

export function hush() {
  stopPlayback()
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}

on('say', m => speak(m.text))
on('halt', () => hush())
export const lastSaid = () => said
