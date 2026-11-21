// The agent overlay: presence dot, captions, proof rail, gates, signature dialog, PDF viewer.
import { FormEvent, useEffect, useRef, useState } from 'react'
import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api, decide, get, refresh, set, useStore, utter, Proof } from '../store'
import { hush, startVoice, stopVoice } from '../voice'
import { Kind, Signature } from '../edc/Shared'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const STATE_LABEL: Record<string, string> = { listening: 'Listening', thinking: 'Thinking', acting: 'Acting', waiting: 'Waiting for you' }

export function Dot({ onClick }: { onClick?: () => void }) {
  const d = useStore(s => s.dot)
  const speaking = useStore(s => s.speaking)
  return (
    <button className={`dot ${d.state} ${speaking ? 'speaking' : ''}`} onClick={onClick} aria-label={`Agent: ${STATE_LABEL[d.state]}. ${onClick ? 'Activate to turn voice on or off.' : ''}`}>
      <i className="halo" /><i className="core" />
    </button>
  )
}

export function SayBox() {
  const [text, setText] = useState('')
  const voice = useStore(s => s.voice)
  const submit = (e: FormEvent) => { e.preventDefault(); if (text.trim()) { hush(); utter(text, { typed: true }); setText('') } }
  return (
    <form className="say" onSubmit={submit} data-no-shot>
      <input value={text} onChange={e => setText(e.target.value)} placeholder="Say it, or type here" aria-label="Type to the agent" />
      <button type="button" className={`mic ${voice === 'on' ? 'on' : ''}`} onClick={() => (voice === 'on' ? stopVoice() : startVoice())} aria-pressed={voice === 'on'} aria-label={voice === 'on' ? 'Turn the microphone off' : 'Turn the microphone on'} title={voice === 'error' ? get().voiceError ?? '' : 'Microphone'}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-6a3.5 3.5 0 0 0-7 0v6A3.5 3.5 0 0 0 12 15Zm6-3.5a6 6 0 0 1-12 0H4a8 8 0 0 0 7 7.9V22h2v-2.6a8 8 0 0 0 7-7.9Z" /></svg>
      </button>
    </form>
  )
}

export function Heard() {
  const h = useStore(s => s.heard)
  if (!h) return null
  return <div className={`heard ${h.action === 'ignore' ? 'ign' : ''}`} role="status" data-no-shot>heard: “{h.text}”{h.action === 'ignore' && ' · not for me'}</div>
}

export function AgentBar() {
  const d = useStore(s => s.dot)
  return (
    <div className="agentbar" data-no-shot>
      <Dot />
      <div className="cap" role="status" aria-live="polite"><b>{STATE_LABEL[d.state]}</b><span>{d.caption}</span></div>
      <SayBox />
    </div>
  )
}

export function Cursor() {
  return (
    <div id="agent-cursor" className="agent-cursor" style={{ opacity: 0 }} aria-hidden data-no-shot>
      <svg width="22" height="22" viewBox="0 0 24 24"><path d="M4 2l15 9.5-6.6 1.5 4 7.4-2.8 1.5-4-7.5L4 19z" fill="#f5921b" stroke="#fff" strokeWidth="1.4" strokeLinejoin="round" /></svg>
    </div>
  )
}

// ------------------------------------------------------------------ proof rail
function ProofCard({ p }: { p: Proof }) {
  return (
    <div className={`pf ${p.kind}`}>
      <div className="hd"><Kind k={p.kind} fixture={p.fixture} /><span className="muted">{p.kind === 'ai' ? p.model : p.actor}</span></div>
      <div className="t">{p.title}</div>
      {p.detail && <div className="d">{p.detail}</div>}
      <div className="meta">
        {p.page && <a onClick={() => set({ pdf: { page: p.page!, quote: p.quote } })} title={p.quote ?? ''}>p. {p.page} ↗</a>}
        {p.check && <span className={p.check.ok ? 'ok-t' : 'bad-t'} title={`Checked by ${p.check.by}`}>{p.check.ok ? '✓' : '✗'} {p.check.label}</span>}
        <span className="hash" title={`Ledger entry ${p.seq}: ${p.hash}`}>#{p.seq} {p.hash.slice(0, 8)}</span>
      </div>
    </div>
  )
}

/** Bottom right: which model is doing what, stage by stage. */
export function ModelsPanel() {
  const pipeline = useStore(s => s.pipeline)
  const voice = useStore(s => s.voice)
  const speaking = useStore(s => s.speaking)
  const costs = useStore(s => s.snap?.costs)
  const rows = pipeline.map((r: any) => r.job !== 'voice' ? r : {
    ...r, status: voice === 'on' ? 'running' : voice === 'error' ? 'failed' : 'idle',
    detail: voice === 'on' ? (speaking ? 'Speaking' : 'Listening') : voice === 'error' ? 'Unavailable' : 'Microphone off',
  })
  const running = rows.filter((r: any) => r.status === 'running').length
  return (
    <section className="models" aria-label="Models at work">
      <header><b>Models at work</b><span className="mono">{running ? `${running} running` : 'idle'}{costs?.calls ? ` · ${costs.calls} calls · $${costs.total_usd.toFixed(2)}` : ''}</span></header>
      <ol>
        {rows.map((r: any) => (
          <li key={r.job} className={r.status} title={r.what}>
            <span className="st" aria-label={r.status}>{r.status === 'running' ? <i className="spinner" /> : r.status === 'done' ? '✓' : r.status === 'failed' ? '!' : ''}</span>
            <div>
              <div className="row1"><b>{r.label}</b><code>{r.model}</code></div>
              <div className="row2">{r.detail || r.what}</div>
              {(r.calls > 0 || r.last_seq) && <div className="row3 mono">{r.calls > 0 && <>{r.calls} call{r.calls !== 1 && 's'} · {r.seconds.toFixed(1)} s{r.cost_usd > 0 && <> · ${r.cost_usd.toFixed(3)}</>}</>}{r.last_seq && <>{r.calls > 0 && ' · '}ledger #{r.last_seq}</>}</div>}
            </div>
          </li>))}
      </ol>
    </section>
  )
}

export function ProofRail() {
  const proofs = useStore(s => s.snap?.proofs ?? []) as Proof[]
  const ledger = useStore(s => s.snap?.ledger)
  const [ver, setVer] = useState<any>(null)
  const list = useRef<HTMLDivElement>(null)
  useEffect(() => { list.current?.scrollTo({ top: 0, behavior: 'smooth' }) }, [proofs.length])
  return (
    <aside className="rail" aria-label="Audit trail and models">
      <section className="trail">
        <header><b>Audit trail</b><span className="mono">{ledger ? `${ledger.entries} entries` : ''}</span>
          <button className="btn sm" onClick={async () => setVer(await api('/api/ledger/verify'))}>Verify chain</button></header>
        {ver && <div className={`verline ${ver.ok ? 'ok' : 'bad'}`} role="status">{ver.ok ? `✓ ${ver.checked} entries recomputed, chain intact` : `Chain broken at entry ${ver.broken_at}`}</div>}
        <div className="list" ref={list}>
          {proofs.length === 0 && <div className="empty">Each step the agent takes lands here with who did it, the protocol page, the check it passed and its hash.</div>}
          {proofs.slice().reverse().map(p => <ProofCard key={p.seq} p={p} />)}
        </div>
        {ledger && <div className="headhash mono" title={ledger.head}>head {ledger.head.slice(0, 20)}…</div>}
      </section>
      <ModelsPanel />
    </aside>
  )
}

// ------------------------------------------------------------------ gates
export function GatePanel() {
  const snap = useStore(s => s.snap)
  const q = useStore(s => s.question)
  const st = snap.flow.state
  const mine = snap.flow.owner === snap.user.role
  const a = snap.amendment
  if (q) return (
    <div className="gatepanel" role="group" aria-label="The agent is asking" data-no-shot>
      <h3>{q.text}</h3>
      <div className="bar" style={{ margin: 0 }}>
        <button className="btn ok" onClick={() => decide('approve')}>{q.yes}</button>
        <button className="btn" onClick={() => decide('reject')}>{q.no}</button>
        <button className="btn" onClick={() => decide('explain')}>Why?</button>
        <button className="btn" onClick={() => decide('show_source')}>Show me the source</button>
      </div>
    </div>
  )
  if (!mine) return null
  if (st === 'gate_option') return (
    <div className="gatepanel" role="group" aria-label="Choose a design option" data-no-shot>
      <h3>Choose a design</h3>
      <div className="opts">{a.options.map((o: any) => (
        <button key={o.id} className={`opt ${o.recommended ? 'rec' : ''}`} onClick={() => decide('choose_option', o.id)}>
          <b>Option {o.id}</b> {o.recommended && <span className="pill blue">Recommended</span>}
          <p>{o.tradeoff}</p>
          <div className="pc">{a.audits[o.id] ? `${a.audits[o.id].findings.filter((f: any) => f.routed).length} audit finding(s) need you` : ''}</div>
        </button>))}</div>
    </div>
  )
  if (st === 'gate_approve') return (
    <div className="gatepanel" role="group" aria-label="Approve the build" data-no-shot>
      <h3>Approve this build for signature?</h3>
      <div className="bar" style={{ margin: 0 }}>
        <button className="btn ok" onClick={() => decide('approve')}>Approve</button>
        <button className="btn" onClick={() => decide('reject')}>Reject</button>
        <button className="btn" onClick={() => decide('explain')}>Why?</button>
        {a.options.filter((o: any) => o.id !== a.chosen).map((o: any) => <button key={o.id} className="btn" onClick={() => decide('choose_option', o.id)}>Try option {o.id} instead</button>)}
      </div>
    </div>
  )
  if (['sign', 'rollout', 'site_sign'].includes(st)) return (
    <div className="gatepanel" role="group" aria-label="Signature needed" data-no-shot>
      <h3>Your signature is needed</h3>
      <div className="bar" style={{ margin: 0 }}>
        <button className="btn org" onClick={() => decide('sign')}>Sign…</button>
        {st === 'sign' && <button className="btn" onClick={() => decide('undo')}>Go back</button>}
        <span className="muted">Voice can approve. Signing needs your user ID and password.</span>
      </div>
    </div>
  )
  return null
}

// ------------------------------------------------------------------ Part 11 signature dialog
export function SignDialog() {
  const sign = useStore(s => s.sign)
  const [uid, setUid] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<any>(null)
  const first = useRef<HTMLInputElement>(null)
  useEffect(() => { if (sign) { setUid(''); setPw(''); setErr(null); setDone(null); setTimeout(() => first.current?.focus(), 50) } }, [sign])
  if (!sign) return null
  const close = () => set({ sign: null })
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      const m = await api('/api/sign', { body: { user_id: uid.trim(), password: pw, meaning: sign.meaning } })
      setDone(m)
      await refresh()
    } catch (x: any) { setErr(x.message) } finally { setBusy(false); setPw('') }
  }
  return (
    <div className="ovl" data-no-shot>
      <form className="modal" role="dialog" aria-modal="true" aria-label="Electronic signature" onSubmit={submit}>
        <h2>Electronic signature{!done && <button type="button" className="x" onClick={close}>Cancel ✕</button>}</h2>
        {done ? <>
          <div className="bd"><div className="note green"><b>Signed.</b> The signature is linked to the record and cannot be moved to another record.</div><Signature m={done} /></div>
          <div className="ft"><button type="button" className="btn pri" onClick={close} autoFocus>Close</button></div>
        </> : <>
          <div className="bd">
            <p style={{ marginTop: 0 }}>You are signing <b>{sign.record_id}</b> ({sign.record_type.replace('_', ' ')}). Your signature is the legally binding equivalent of a handwritten signature (21 CFR Part 11).</p>
            <dl className="kv" style={{ marginBottom: 12 }}>
              <dt>Signer</dt><dd>{sign.name}</dd>
              <dt>Meaning</dt><dd><b>{sign.meaning}</b></dd>
              <dt>Record hash</dt><dd className="mono">{sign.record_hash.slice(0, 32)}…</dd>
            </dl>
            {err && <div className="err" role="alert">{err}</div>}
            <div className="fld"><label htmlFor="sg1">User ID</label><input id="sg1" ref={first} value={uid} onChange={e => setUid(e.target.value)} autoComplete="username" required /></div>
            <div className="fld"><label htmlFor="sg2">Password</label><input id="sg2" type="password" value={pw} onChange={e => setPw(e.target.value)} autoComplete="current-password" required /></div>
            <div className="muted">Enter both again, even though you are signed in. A spoken “yes” is not a signature.</div>
          </div>
          <div className="ft"><button className="btn org" disabled={busy || !uid || !pw}>{busy ? 'Signing…' : `Sign: ${sign.meaning}`}</button></div>
        </>}
      </form>
    </div>
  )
}

// ------------------------------------------------------------------ PDF viewer with quote highlight
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '')

export function PdfView({ page, quote, onPage }: { page: number; quote?: string | null; onPage?: (p: number) => void }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [pages, setPages] = useState(0)
  const [found, setFound] = useState<boolean | null>(null)
  const doc = useRef<any>(null)
  useEffect(() => {
    let dead = false
    ;(async () => {
      if (!doc.current) doc.current = await pdfjs.getDocument({ url: '/api/protocol.pdf' }).promise
      const d = doc.current
      if (dead) return
      setPages(d.numPages)
      const pg = await d.getPage(Math.min(Math.max(page, 1), d.numPages))
      const vp = pg.getViewport({ scale: 1.35 })
      const canvas = document.createElement('canvas')
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = vp.width * dpr; canvas.height = vp.height * dpr
      canvas.style.width = `${vp.width}px`; canvas.style.height = `${vp.height}px`
      await pg.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined } as any).promise
      if (dead || !wrap.current) return
      const host = document.createElement('div')
      host.className = 'pg'
      host.style.width = `${vp.width}px`; host.style.height = `${vp.height}px`
      host.appendChild(canvas)
      let hit = false
      if (quote) {
        // map the normalised page text back to text items, then box the items the quote covers
        const tc = await pg.getTextContent()
        const items = (tc.items as any[]).filter(i => typeof i.str === 'string')
        let text = ''
        const span: [number, number][] = []
        for (const it of items) { const a = text.length; text += norm(it.str); span.push([a, text.length]) }
        const q = norm(quote)
        let at = text.indexOf(q), len = q.length
        if (at < 0 && q.length > 40) { at = text.indexOf(q.slice(0, 40)); const end = text.indexOf(q.slice(-40)); len = at >= 0 && end > at ? end + 40 - at : 40 }
        if (at >= 0) {
          hit = true
          let firstBox: HTMLElement | null = null
          items.forEach((it, i) => {
            if (span[i][1] <= at || span[i][0] >= at + len || !it.str.trim()) return
            const t = pdfjs.Util.transform(vp.transform, it.transform)
            const h = Math.hypot(t[2], t[3])
            const box = document.createElement('i')
            box.style.cssText = `left:${t[4]}px;top:${t[5] - h}px;width:${it.width * vp.scale}px;height:${h * 1.15}px`
            host.appendChild(box)
            firstBox ??= box
          })
          setTimeout(() => (firstBox as HTMLElement | null)?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 60)
        }
      }
      setFound(quote ? hit : null)
      wrap.current.replaceChildren(host)
    })().catch(() => setFound(false))
    return () => { dead = true }
  }, [page, quote])
  return (
    <>
      <div className="pdfbar">
        <button className="btn sm" disabled={page <= 1} onClick={() => onPage?.(page - 1)}>‹ Prev</button>
        <span>Page <b>{page}</b> of {pages || '…'}</span>
        <button className="btn sm" disabled={!!pages && page >= pages} onClick={() => onPage?.(page + 1)}>Next ›</button>
        {quote && <span className={found === false ? 'bad-t' : 'muted'}>{found === false ? 'The quote could not be highlighted on this page.' : found ? 'Quote highlighted in yellow.' : ''}</span>}
      </div>
      <div className="pdfv" ref={wrap} />
    </>
  )
}

export function PdfDialog() {
  const pdf = useStore(s => s.pdf)
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && set({ pdf: null })
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])
  if (!pdf) return null
  return (
    <div className="ovl" data-no-shot>
      <div className="modal wide" role="dialog" aria-modal="true" aria-label="Protocol source">
        <h2>Protocol source{pdf.quote && <span className="muted" style={{ fontSize: 12, fontWeight: 400, maxWidth: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>“{pdf.quote}”</span>}<button className="x" onClick={() => set({ pdf: null })} autoFocus>Close ✕</button></h2>
        <PdfView page={pdf.page} quote={pdf.quote} onPage={p => set({ pdf: { page: p, quote: pdf.quote } })} />
      </div>
    </div>
  )
}

export function ProtocolPage() {
  const [p, setP] = useState(1)
  return <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 190px)', border: '1px solid var(--line)' }}><PdfView page={p} onPage={setP} /></div>
}
