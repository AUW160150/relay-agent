// Application chrome and the pages every role shares (AI Record, Audit Trail, change report).
import { ReactNode, useEffect, useState } from 'react'
import { api, get, logout, set, useStore } from '../store'

export const PAGE_LABEL: Record<string, string> = {
  amendment: 'Amendment', schedule: 'Schedule', events: 'Events', forms: 'Forms', codelists: 'Codelists', rules: 'Rules',
  review: 'Review Plans', tests: 'Test Subjects', compare: 'Change Report', protocol: 'Protocol', record: 'AI Record', audit: 'Audit Trail',
  versions: 'Casebook Versions', deployment: 'Deployment', rollout: 'Site Rollout', changes: 'What Changes', transition: 'Transition Plan',
}
const APP = { sponsor: 'Sponsor · Study design', cro: 'CRO · Deployment', site: 'Site workspace' } as const
const GROUPS: Record<string, [string, string[]][]> = {
  sponsor: [['Amendment', ['amendment', 'compare', 'record']], ['Casebook', ['schedule', 'events', 'forms', 'codelists', 'rules', 'review']], ['Validation', ['tests', 'protocol', 'audit']]],
  cro: [['Deployment', ['versions', 'deployment', 'rollout']], ['Trust', ['record', 'audit']]],
  site: [['Your site', ['changes', 'transition']], ['Trust', ['record', 'audit']]],
}

/** RELAY mark: a teal tile with the baton tick, then the name. */
export function Brand({ dark }: { dark?: boolean }) {
  return (
    <span className={`brand ${dark ? 'on-dark' : ''}`} aria-label="RELAY">
      <span className="tile" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16"><path d="M4 17.5 10.2 6h3.6L20 17.5h-3.3L12 8.9l-4.7 8.6z" fill="#fff" /><path d="M5.2 15.4c4.6-.4 8.9-2 12.6-5.2l1 2.3c-3.6 3-7.9 4.7-13 5.2z" fill="#0b3d39" opacity=".55" /></svg></span>
      <span className="nm">RELAY</span>
    </span>
  )
}

function count(snap: any, p: string): string {
  const cb = snap.casebook
  const n: Record<string, number | undefined> = {
    amendment: snap.amendment.changes.filter((c: any) => c.affects_edc).length || undefined, record: snap.record.length || undefined,
    compare: snap.compare.length || undefined, review: cb.review_plans.length, events: cb.events.length, forms: cb.forms.length,
    codelists: Object.keys(cb.codelists).length, rules: cb.rules.length, tests: snap.amendment.tests?.total,
  }
  return n[p] != null ? ` (${n[p]})` : ''
}

export function Chrome({ children }: { children: ReactNode }) {
  const snap = useStore(s => s.snap)
  const page = useStore(s => s.page)
  const u = snap.user
  const role = u.role as 'sponsor' | 'cro' | 'site'
  const locked = snap.has_v2
  const status = locked ? 'Casebook v2 · signed' : snap.compare.length ? `Casebook v2 draft · ${snap.flow.global === 'build' ? 'build in progress' : 'not signed'}` : 'Casebook v1 · in production'
  return (
    <div className="ws" style={{ minHeight: '100%' }}>
      <header className="bh-top">
        <Brand />
        <span className="bh-crumb">{APP[role]}<i>/</i>{snap.study.num}{role === 'site' && <><i>/</i>{snap.site.id} · {snap.site.name}</>}<i>/</i><b>{PAGE_LABEL[page]}</b></span>
        <span className="bh-right">
          <span className={`bh-pill ${locked ? 'ok' : snap.compare.length ? 'warn' : ''}`}><i />{status}</span>
          <span className="bh-user"><span>{u.name}<small>{u.title}</small></span><span className="avatar" aria-hidden>{u.name.split(' ').map((x: string) => x[0]).join('').slice(0, 2)}</span></span>
        </span>
      </header>
      <div className="layout">
        <aside className="side" aria-label="Pages">
          {GROUPS[role].map(([g, pages]) => (
            <div key={g} className="grp">
              <h4>{g}</h4>
              {pages.map(p => (
                <button key={p} className={`it ${page === p ? 'on' : ''}`} data-agent-id={`nav-${p}`} data-label={PAGE_LABEL[p]} onClick={() => set({ page: p, uiError: null })}>
                  <span>{p === 'protocol' ? 'Protocol Ver. 1.1' : PAGE_LABEL[p]}</span><em>{count(snap, p).replace(/[ ()]/g, '')}</em>
                </button>
              ))}
            </div>
          ))}
        </aside>
        <main className="page">{children}</main>
      </div>
    </div>
  )
}

export function PH({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return <div className="ph"><div><h1>{title}</h1>{sub && <div className="sub">{sub}</div>}</div><div className="acts">{children}</div></div>
}

/** Dialog inside the application. role="dialog" scopes the agent's element list to it. */
export function Modal({ title, onClose, children, footer, wide }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const err = useStore(s => s.uiError)
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="ovl">
      <div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
        <h2>{title}<button className="x" data-agent-id="dialog-cancel" onClick={onClose}>Cancel ✕</button></h2>
        <div className="bd">{err && <div className="err" role="alert">{err}</div>}{children}</div>
        {footer && <div className="ft">{footer}</div>}
      </div>
    </div>
  )
}

export const PageLink = ({ page, quote }: { page: number | null | undefined; quote?: string | null }) =>
  page ? <a onClick={() => set({ pdf: { page, quote } })}>p. {page}</a> : <span className="muted">no source</span>

export const Kind = ({ k, fixture }: { k: string; fixture?: boolean }) =>
  <span className={`kind ${fixture ? 'fx' : k}`} title={fixture ? 'Hand-written test data, not model output' : undefined}>{fixture ? 'Fixture' : k === 'ai' ? 'AI draft' : k === 'rule' ? 'Fixed rule' : 'Person'}</span>

export function Signature({ m }: { m: any }) {
  return (
    <div className="sigblock">
      <b>{m.printed_name}</b>
      {m.title} · {m.user_id}<br />
      Signed {new Date(m.signed_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'long' })}<br />
      Meaning: <b style={{ display: 'inline' }}>{m.meaning}</b><br />
      <span className="mono muted">record {m.record} · sha256 {m.signed_record_hash.slice(0, 16)}… · ledger #{m.seq}</span>
    </div>
  )
}

export function Compare() {
  const snap = useStore(s => s.snap)
  const rows = snap.compare as any[]
  return (
    <>
      <PH title="Change Report" sub={`Casebook version 1 → ${snap.has_v2 ? 'version 2 (signed)' : 'working draft'}`} />
      {rows.length === 0 ? <div className="note blue">No differences. The working draft equals version 1.</div> : (
        <table className="v">
          <thead><tr><th>Object</th><th>Name</th><th>Change</th><th>Before</th><th>After</th></tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}><td>{r.type}</td><td><b>{r.name}</b></td><td><span className={`pill ${r.change === 'Added' ? 'high' : 'blue'}`}>{r.change}</span></td><td className="muted" style={{ whiteSpace: 'pre-wrap' }}>{r.before}</td><td style={{ whiteSpace: 'pre-wrap' }}>{r.after}</td></tr>)}</tbody>
        </table>
      )}
    </>
  )
}

const BASIS: Record<string, [string, string]> = {
  copied: ['high', 'Copied from the protocol'], interpreted: ['blue', 'Interpreted from the protocol'],
  inferred: ['med', 'Inferred, not in the protocol'], unclear: ['dec', 'Protocol is unclear'],
}

export function AiRecord() {
  const snap = useStore(s => s.snap)
  const rec = snap.record as any[]
  const [open, setOpen] = useState<string | null>(null)
  return (
    <>
      <PH title="AI Record" sub="Five answers for every automated change: where it came from, what was done, how it was tested, what is uncertain, who reviewed it." />
      {snap.fixture && <div className="note red"><b>Fixture mode.</b> The content below is hand-written test data, not model output.</div>}
      {rec.length === 0 && <div className="note blue">No build has been chosen yet. The record fills in as the agent works.</div>}
      {rec.map(r => {
        const [cls, label] = BASIS[r.basis] ?? ['gray', r.basis]
        const t = r.tested
        const pass = t.subjects.filter((s: any) => s.pass).length
        return (
          <div className="card" key={r.op_id}>
            <h2>
              <span className="mono muted">{r.op_id}</span>{r.title}
              <span className={`pill ${cls}`} style={{ marginLeft: 'auto' }}>{label}</span>
              <button className="btn sm" onClick={() => setOpen(open === r.op_id ? null : r.op_id)} aria-expanded={open === r.op_id}>{open === r.op_id ? 'Less' : 'Tests'}</button>
            </h2>
            <div className="bd">
              <dl className="five">
                <dt>1. Source</dt>
                <dd>{r.source.page ? <><PageLink page={r.source.page} quote={r.source.quote} /> {r.source.citation && (r.source.citation.ok || r.source.citation.moved) ? <span className="ok-t">✓ quote found on page</span> : <span className="bad-t">quote not verified</span>}<div className="quote">“{r.source.quote}”</div></> : <span className="bad-t">Not in the protocol.</span>}</dd>
                <dt>2. What was done</dt><dd>{r.what}</dd>
                <dt>3. How it was tested</dt>
                <dd>
                  {t.built_ok == null ? <span className="muted">Not built yet. </span> : t.built_ok ? <span className="ok-t">✓ Built casebook shows this change. </span> : <span className="bad-t">Built casebook does not show this change. </span>}
                  {t.subjects.length ? <>{pass} of {t.subjects.length} synthetic subjects that exercise it pass in the rule engine{t.ran_on_built_casebook ? ' on the built casebook' : ' (planned diff; not yet re-run on the build)'}.</> : <span className="muted">No rule to exercise; checked by comparing the build with the plan.</span>}
                  {open === r.op_id && <ul>{t.subjects.map((s: any) => <li key={s.id}><b>{s.id}</b> {s.description}: {s.pass ? <span className="ok-t">pass</span> : <span className="bad-t">fail</span>}</li>)}</ul>}
                </dd>
                <dt>4. What is uncertain</dt>
                <dd>
                  {!r.uncertain.planner && r.uncertain.auditor.length === 0 && <span className="muted">Nothing flagged by the planner or the auditor.</span>}
                  {r.uncertain.planner && <div>Planner: {r.uncertain.planner}</div>}
                  {r.uncertain.auditor.map((f: any) => <div key={f.id}>Independent auditor ({f.kind}{f.p_needs_human != null && `, P(needs a person) ${f.p_needs_human.toFixed(2)}`}): {f.detail}{f.decision && <b> Decision: {f.decision}.</b>}</div>)}
                </dd>
                <dt>5. Who reviewed it</dt>
                <dd>{r.reviewed ? <>{r.reviewed.by}, {new Date(r.reviewed.at).toLocaleString('en-GB')} · “{r.reviewed.meaning}”</> : <span className="muted">Not signed yet.</span>}</dd>
              </dl>
            </div>
          </div>
        )
      })}
    </>
  )
}

export function Audit() {
  const snap = useStore(s => s.snap)
  const [data, setData] = useState<any>(null)
  const [ver, setVer] = useState<any>(null)
  const [filter, setFilter] = useState('all')
  const n = snap.proofs.length
  useEffect(() => { api('/api/ledger').then(setData).catch(() => {}) }, [n, snap.flow.state])
  const verify = async () => { setVer(await api('/api/ledger/verify')); setData(await api('/api/ledger')) }
  const sig = snap.signatures
  const sigs = [sig.sponsor, sig.cro, ...Object.values(sig.site)].filter(Boolean) as any[]
  const rows = (data?.entries ?? []).filter((e: any) => filter === 'all' || e.actor_type === filter).slice().reverse()
  return (
    <>
      <PH title="Audit Trail" sub="Append-only. Each entry's hash covers the entry before it, so any change breaks the chain.">
        <button className="btn pri" data-agent-id="audit-verify" onClick={verify}>Verify chain</button>
      </PH>
      {ver && (ver.ok
        ? <div className="note green" role="status"><b>Chain verified.</b> Recomputed {ver.checked} entries; head <span className="mono">{ver.head.slice(0, 20)}…</span>{ver.signatures.map((s: any) => <div key={s.role}>Signature ({s.role}): {s.ok ? <span className="ok-t">intact and bound to its record</span> : <span className="bad-t">{s.reason}</span>}</div>)}</div>
        : <div className="note red" role="alert"><b>Chain broken at entry {ver.broken_at}:</b> {ver.reason}.</div>)}
      {sigs.length > 0 && <div className="card"><h2>Signatures</h2><div className="bd" style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))' }}>{sigs.map(m => <Signature key={m.seq} m={m} />)}</div></div>}
      <div className="bar">
        <label>Made by <select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">Anyone</option><option value="person">A person</option><option value="ai">The AI</option><option value="rule">A fixed rule</option></select></label>
        <span className="muted">{rows.length} entries</span>
      </div>
      <table className="v">
        <thead><tr><th>#</th><th>Time (UTC)</th><th>By</th><th>Action</th><th>Record</th><th>Detail</th><th>Source</th><th>Hash</th></tr></thead>
        <tbody>{rows.slice(0, 400).map((e: any) => (
          <tr key={e.seq}>
            <td>{e.seq}</td><td className="nw">{e.ts.slice(11, 23)}</td>
            <td><Kind k={e.actor_type} fixture={!!e.model?.startsWith('fixture')} /><div>{e.actor_id}</div>{e.model && <div className="muted mono">{e.model}</div>}</td>
            <td>{e.action}</td><td>{e.record}</td>
            <td style={{ maxWidth: 340, wordBreak: 'break-word' }}>{e.reason}{e.after != null && <div className="muted mono">{JSON.stringify(e.after).slice(0, 160)}</div>}</td>
            <td>{e.source?.page ? <PageLink page={e.source.page} quote={e.source.quote} /> : ''}</td>
            <td className="mono muted" title={e.hash}>{e.hash.slice(0, 10)}…</td>
          </tr>
        ))}</tbody>
      </table>
    </>
  )
}

export const num = (s: string) => (s.trim() === '' ? null : Number(s))
export const fmtWin = (e: any) => (e.window_before == null && e.window_after == null ? '—' : e.window_before === e.window_after ? `±${e.window_after}` : `-${e.window_before ?? 0}/+${e.window_after ?? 0}`)
export { get, logout }
