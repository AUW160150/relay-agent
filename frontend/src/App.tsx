import type React from 'react'
import { DragEvent, FormEvent, useEffect, useRef, useState } from 'react'
import { api, connect, decide, get, login, logout, on, send, set, toast, useStore } from './store'
import { halt, perform } from './agentActions'
import { ENV_WIDTH, performEnv } from './envActions'
import { startVoice, stopVoice } from './voice'
import { AiRecord, Audit, Brand, Chrome, Compare } from './edc/Shared'
import { Amendment, Codelists, Events, Forms, Review, Rules, Schedule, Tests } from './edc/Studio'
import { Changes, Deployment, Rollout, Transition, Versions } from './edc/CroSite'
import { AgentBar, Cursor, Dot, GatePanel, Heard, PdfDialog, ProofRail, ProtocolPage, SayBox, SignDialog } from './overlay/Overlay'

// The orchestrator asks this browser to act; the action layer does it and reports back.
on('act', async m => {
  if (m.action.target === 'env') {                      // the embedded environment on the voice page
    if (!get().env) { set({ env: true }); await new Promise(r => setTimeout(r, 300)) }
    send({ type: 'act_result', id: m.id, ...(await performEnv(m.action)) })
    return
  }
  if (!get().canvas) { set({ canvas: true }); await new Promise(r => setTimeout(r, 80)) }
  const res = await perform(m.action, { shot: !get().snap?.fixture })
  send({ type: 'act_result', id: m.id, ...res })
})
on('halt', () => halt())

const PAGES: Record<string, () => React.JSX.Element> = {
  amendment: Amendment, schedule: Schedule, events: Events, forms: Forms, codelists: Codelists, rules: Rules, review: Review, tests: Tests,
  compare: Compare, protocol: ProtocolPage, record: AiRecord, audit: Audit, versions: Versions, deployment: Deployment, rollout: Rollout,
  changes: Changes, transition: Transition,
}

const ROLES = [
  { role: 'sponsor', label: 'Sponsor', sub: 'Data manager' },
  { role: 'cro', label: 'CRO', sub: 'Study manager' },
  { role: 'site', label: 'Site', sub: 'Coordinator' },
] as const

function Landing() {
  const [info, setInfo] = useState<any>(null)
  const [view, setView] = useState<'home' | 'signin'>(location.hash === '#signin' ? 'signin' : 'home')
  const [role, setRole] = useState<'sponsor' | 'cro' | 'site'>('sponsor')
  const [site, setSite] = useState('')
  const [uid, setUid] = useState('')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api('/api/accounts').then(setInfo).catch(e => setErr(`The server is not reachable: ${e.message}`)) }, [])
  const accounts = (info?.accounts ?? []) as any[]
  const sites = accounts.filter(a => a.role === 'site')
  useEffect(() => {
    const a = role === 'site' ? sites.find(s => s.site_id === site) ?? sites[0] : accounts.find(x => x.role === role)
    if (a) { setUid(a.user_id); if (role === 'site' && !site) setSite(a.site_id) }
  }, [role, site, info])
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null)
    try { await login(uid.trim(), pw) } catch (x: any) { setErr(x.message) } finally { setBusy(false) }
  }
  const modeNote = info?.mode === 'fixture' ? 'Fixture mode: hand-written test data, no model calls.' : info && !info.has_key ? 'No GEMINI_API_KEY on the server: model steps will fail.' : null

  const pick = (r: 'sponsor' | 'cro' | 'site') => { setRole(r); setView('signin'); window.scrollTo(0, 0) }

  if (view === 'signin') return (
    <div className="land">
      <div className="head"><Brand dark /></div>
      <form className="signin" onSubmit={submit}>
        <button type="button" className="back" onClick={() => setView('home')}>← All roles</button>
        <div className="eye">Sign in</div>
        <h2>Open your workspace</h2>
        <p>Choose a role. The agent catches you up and tells you what is yours to do.</p>
        <div className="roles" role="radiogroup" aria-label="Role">
          {ROLES.map(r => <button type="button" key={r.role} role="radio" aria-checked={role === r.role} className={role === r.role ? 'on' : ''} onClick={() => setRole(r.role)}><b>{r.label}</b><small>{r.sub}</small></button>)}
        </div>
        {role === 'site' && <label className="sf">Site<select value={site} onChange={e => setSite(e.target.value)}>{sites.map(s => <option key={s.site_id} value={s.site_id}>{s.site_id} · {s.org}</option>)}</select></label>}
        {err && <div className="err" role="alert">{err}</div>}
        <label className="sf">User ID<input value={uid} onChange={e => setUid(e.target.value)} autoComplete="username" required /></label>
        <label className="sf">Password<input type="password" value={pw} onChange={e => setPw(e.target.value)} autoComplete="current-password" required /></label>
        <button className="btn big" disabled={busy || !info}>{busy ? 'Signing in…' : 'Sign in'}</button>
        {info && <div className="w-disc">Demo sign-in accepts any password. Signing a record needs the real one: <code>{info.demo_password}</code>.{modeNote && <> <b style={{ color: 'var(--red)' }}>{modeNote}</b></>}</div>}
      </form>
      <div className="foot2"><span>Independent concept. Not affiliated with or endorsed by Veeva Systems.</span></div>
    </div>
  )

  const CARDS = [
    { role: 'sponsor' as const, tag: 'Sponsor / Data manager', h: 'I approve the casebook change', p: 'The agent reads the amendment, proposes designs, builds the one you choose and proves it. You approve and sign.', stats: [['97', 'Protocol pages read'], ['6', 'Models, one job each']], go: 'Sign in as Sponsor' },
    { role: 'cro' as const, tag: 'CRO / Study manager', h: 'I deploy and roll out', p: 'A signed casebook arrives ready. The agent deploys it and rolls it out to every site with ethics approval.', stats: [[String(info?.study ? 27 : 27), 'Sites in the study'], ['2', 'Signature checks before deploy']], go: 'Sign in as CRO' },
    { role: 'site' as const, tag: 'Site / Coordinator', h: 'I run the visits', p: 'See what changes at your site and how the new schedule maps onto participants already enrolled. Then sign off.', stats: [['7', 'New visits in this amendment'], ['0', 'Models in the transition plan']], go: 'Sign in as Site' },
  ]
  const ART = [
    <svg key="a" className="art" viewBox="0 0 400 300" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><g stroke="#14b8a6" strokeWidth="1.5" fill="none" opacity=".7">{[40, 80, 120, 160, 200, 240].map(y => <path key={y} d={`M70 ${y}h260`} />)}<path d="M70 40v200M330 40v200" /></g>{[[110, 80], [190, 120], [250, 160], [150, 200], [290, 80]].map(([x, y], i) => <circle key={i} cx={x} cy={y} r="7" fill={i % 2 ? '#f59e0b' : '#14b8a6'} opacity=".8" />)}</svg>,
    <svg key="b" className="art" viewBox="0 0 400 300" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><g stroke="#14b8a6" strokeWidth="1.2" opacity=".6">{[[200, 150, 90, 60], [200, 150, 320, 70], [200, 150, 70, 220], [200, 150, 330, 230], [200, 150, 200, 40], [320, 70, 370, 150]].map(([x1, y1, x2, y2], i) => <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} />)}</g>{[[200, 150, 12], [90, 60, 8], [320, 70, 8], [70, 220, 7], [330, 230, 8], [200, 40, 7], [370, 150, 6]].map(([x, y, r], i) => <circle key={i} cx={x} cy={y} r={r} fill={i === 4 || i === 6 ? '#f59e0b' : '#14b8a6'} opacity=".8" />)}</svg>,
    <svg key="c" className="art" viewBox="0 0 400 300" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><path d="M40 150h320" stroke="#14b8a6" strokeWidth="1.5" opacity=".6" />{[60, 105, 150, 195, 240, 285, 330].map((x, i) => <g key={x}><path d={`M${x} 150v${i % 2 ? -46 : 46}`} stroke="#14b8a6" strokeWidth="1.2" opacity=".5" /><circle cx={x} cy={150 + (i % 2 ? -46 : 46)} r="7" fill={i === 2 || i === 5 ? '#f59e0b' : '#14b8a6'} opacity=".8" /></g>)}</svg>,
  ]
  return (
    <div className="land">
      <div className="head"><Brand dark /><p>A protocol amendment, carried by voice from the PDF to a signed, validated EDC build</p></div>
      <div className="cards">
        {CARDS.map((c, i) => (
          <button key={c.role} className="rolecard" onClick={() => pick(c.role)}>
            {ART[i]}
            <span className="tagp">{c.tag}</span>
            <h2>{c.h}</h2>
            <p>{c.p}</p>
            <div className="stats">{c.stats.map(([n, l]) => <div key={l}><b>{n}</b><span>{l}</span></div>)}</div>
            <span className="go">{c.go} →</span>
          </button>
        ))}
      </div>
      <div className="foot2"><span>21 CFR Part 11 signatures</span><span>ALCOA+ audit ledger</span><span>{info ? `${info.study.nct} · ${info.study.num}` : 'ClinicalTrials.gov registry data'}</span><span>Independent concept, not affiliated with Veeva Systems</span>{modeNote && <span style={{ color: '#fca5a5' }}>{modeNote}</span>}</div>
    </div>
  )
}

function Strip() {
  const snap = useStore(s => s.snap)
  const canvas = useStore(s => s.canvas)
  const voice = useStore(s => s.voice)
  const verr = useStore(s => s.voiceError)
  return (
    <div className="strip" data-no-shot>
      {snap.fixture && <span className="fx" title="Hand-written test data. Nothing here is model output.">FIXTURE MODE · not model output</span>}
      {snap.replaying && <span className="fx" style={{ background: 'var(--amber)' }} title="Model responses are played back from a recorded live session.">REPLAY of recorded session “{snap.replaying}”</span>}
      {voice === 'error' && <span title={verr ?? ''}>voice unavailable: {verr?.slice(0, 60)}</span>}
      <span className="sp" />
      <button onClick={() => set({ canvas: !canvas })}>{canvas ? 'Hide workspace' : 'Show workspace'}</button>
      <button onClick={async () => { if (confirm('Reset the whole study to its starting state? This clears the build, the signatures and the audit trail for everyone.')) await api('/api/reset', { method: 'POST' }) }}>Reset demo</button>
      <button onClick={() => { stopVoice(); logout() }}>Sign out</button>
    </div>
  )
}

/** The existing RELAY environment, shown beside the orb while the agent works in it. */
function EnvPanel() {
  const box = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(0.5)
  useEffect(() => {
    const fit = () => box.current && setScale(box.current.clientWidth / ENV_WIDTH)
    fit()
    const ro = new ResizeObserver(fit)
    if (box.current) ro.observe(box.current)
    return () => ro.disconnect()
  }, [])
  return (
    <section className="v-env" aria-label="RELAY environment">
      <header><span className="dots"><i /><i /><i /></span><b>RELAY environment</b><span className="mono">audit-amendment-demo · served from the local clone</span>
        <button className="btn sm" onClick={() => set({ env: false })}>Hide</button></header>
      <div className="vp" ref={box}>
        <iframe id="env-frame" title="RELAY environment" src="/env/index.html" style={{ width: ENV_WIDTH, height: `${100 / scale}%`, transform: `scale(${scale})` }} />
      </div>
    </section>
  )
}

function Presence() {
  const d = useStore(s => s.dot)
  const snap = useStore(s => s.snap)
  const voice = useStore(s => s.voice)
  const question = useStore(s => s.question)
  const env = useStore(s => s.env)
  const [drop, setDrop] = useState(false)
  const canDrop = snap.user.role === 'sponsor' && snap.flow.state === 'intake'
  const onDrop = async (e: DragEvent) => {
    e.preventDefault(); setDrop(false)
    const f = e.dataTransfer.files[0]
    if (!f || !canDrop) return
    const form = new FormData(); form.append('file', f)
    try { await api('/api/amendment/upload', { form }) } catch (x: any) { toast(x.message) }
  }
  const b = snap.briefing
  return (
    <div className={`presence voice ${drop ? 'drop' : ''}`} onDragOver={e => { if (canDrop) { e.preventDefault(); setDrop(true) } }} onDragLeave={() => setDrop(false)} onDrop={onDrop}>
      <div className="v-who"><Brand /><span>{b.name} · {b.title}</span></div>
      <div className={`v-stage ${env ? 'with-env' : ''}`}>
      <div className="v-center">
        <Dot onClick={() => (voice === 'on' ? stopVoice() : startVoice())} />
        <div className="statelabel">{{ listening: 'Listening', thinking: 'Thinking', acting: 'Acting', waiting: 'Waiting for you' }[d.state]}</div>
        {!question && <div className="line" role="status" aria-live="polite">{d.caption}</div>}
        <GatePanel />
        {b.task && !question && snap.flow.allowed.length > 0 && d.state === 'waiting' && !['gate_option', 'gate_approve'].includes(snap.flow.state) &&
          <button className="v-do" onClick={() => decide('approve')}>Do it: {b.task}</button>}
        <SayBox />
        <div className="hint2">{canDrop ? 'Say “do it”, or drop a protocol PDF here.' : voice === 'on' ? 'The microphone is on. Say “stop” at any time.' : 'Select the orb to turn the microphone on, or type.'}</div>
      </div>
      {env && <EnvPanel />}
      </div>
      <Heard />
    </div>
  )
}

export default function App() {
  const token = useStore(s => s.token)
  const snap = useStore(s => s.snap)
  const canvas = useStore(s => s.canvas)
  const page = useStore(s => s.page)
  const toastMsg = useStore(s => s.toast)
  useEffect(() => { if (token && !snap) connect() }, [token])
  if (!token) return <Landing />
  if (!snap) return <div className="presence" style={{ height: '100%' }}><div className="hint2">Connecting…</div></div>
  const Page = PAGES[snap.pages.includes(page) ? page : snap.pages[0]]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Strip />
      <div className="shell" style={{ flex: 1 }}>
        {canvas ? (
          <div style={{ flex: 1, minWidth: 0, position: 'relative', display: 'flex' }}>
            <div className="canvas" id="app-canvas"><Chrome><Page /></Chrome></div>
            <div className="dock"><Heard /><GatePanel /><AgentBar /></div>
          </div>
        ) : <Presence />}
        <ProofRail />
      </div>
      <Cursor />
      <SignDialog />
      <PdfDialog />
      {toastMsg && <div className="toast" role="status">{toastMsg}</div>}
    </div>
  )
}
