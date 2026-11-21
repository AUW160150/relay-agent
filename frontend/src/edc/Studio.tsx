// Sponsor workspace: the study design application. Every interactive element carries a
// stable data-agent-id so the navigator can name it; a person can use the same controls.
import { useState } from 'react'
import { decide, set, uiPost, useStore } from '../store'
import { Modal, PH, PageLink, fmtWin, num } from './Shared'

const op = (body: any) => uiPost('/api/draft/op', body)
const useCb = () => { const s = useStore(x => x.snap); return { snap: s, cb: s.draft as any, v1: s.v1 as any, canEdit: s.flow.global === 'build' && s.user.role === 'sponsor' } }
const Locked = ({ canEdit, signed }: { canEdit: boolean; signed: boolean }) =>
  canEdit ? null : <div className="note blue">{signed ? 'Casebook version 2 is signed and locked.' : 'The casebook is read-only until a build is in progress.'}</div>

// ------------------------------------------------------------------ Amendment
export function Amendment() {
  const snap = useStore(s => s.snap)
  const a = snap.amendment
  const st = snap.flow.global
  const mine = snap.flow.owner === 'sponsor'
  return (
    <>
      <PH title={a.title} sub={<>{snap.study.num} · {snap.study.brief} · {a.pdf_pages} pages · received {a.received}</>}>
        <span className="pill gray">state: {st}</span>
      </PH>
      {a.summary && <div className="note blue"><b>Summary of what changed.</b> {a.summary}</div>}
      {a.changes.length === 0 && <div className="note">The amendment has not been read yet. Ask the agent to open it.</div>}
      {a.changes.length > 0 && (
        <div className="card"><h2>Protocol changes <span className="muted" style={{ fontWeight: 400 }}>found by the protocol-diff model, each quote checked against the PDF</span></h2>
          <table className="v"><thead><tr><th>ID</th><th>Type</th><th>Change</th><th>Source</th><th>Quote check</th><th>Affects build</th></tr></thead>
            <tbody>{a.changes.map((c: any) => (
              <tr key={c.id} className={c.affects_edc ? '' : 'dim'}>
                <td>{c.id}</td><td>{c.type}</td><td>{c.summary}<div className="muted">{c.section}</div></td>
                <td className="nw"><PageLink page={c.page} quote={c.quote} /></td>
                <td>{c.citation?.ok ? <span className="ok-t">✓ on page</span> : c.citation?.moved ? <span className="ok-t">✓ found, page corrected</span> : <span className="bad-t">not found</span>}</td>
                <td>{c.affects_edc ? <span className="pill med">Yes</span> : <span className="pill gray">No</span>}</td>
              </tr>))}</tbody></table></div>
      )}
      {a.options.length > 0 && (
        <div className="card"><h2>Design options</h2><div className="bd">
          <div className="opts">{a.options.map((o: any) => {
            const b = a.builds[o.id], au = a.audits[o.id]
            const routed = au?.findings.filter((f: any) => f.routed).length ?? 0
            return (
              <div key={o.id} className={`opt ${o.recommended ? 'rec' : ''}`}>
                <b>Option {o.id}: {o.name}</b> {o.recommended && <span className="pill blue">Recommended</span>} {a.chosen === o.id && <span className="pill solid">Chosen</span>}
                <p>{o.summary}</p>
                <div className="pc">+ {o.pros.join(' · ')}</div><div className="pc">− {o.cons.join(' · ')}</div>
                {b && <div className="pc">{b.ops.length} operations · {b.tests_planned ? `${b.tests_planned.passed}/${b.tests_planned.total} subjects pass` : 'not validated'}{b.repaired && ' · repaired once'}</div>}
                {au && <div className="pc">Audit: {au.findings.length} finding{au.findings.length !== 1 && 's'}{routed > 0 && <b style={{ color: 'var(--amber)' }}> · {routed} need a person</b>}</div>}
                {mine && snap.flow.allowed.includes('choose_option') && b && <button className="btn sm pri" style={{ marginTop: 6 }} data-agent-id={`option-choose-${o.id}`} onClick={() => decide('choose_option', o.id)}>Build option {o.id}</button>}
              </div>)
          })}</div>
          {a.chosen && a.audits[a.chosen]?.findings.length > 0 && (
            <table className="v" style={{ marginTop: 12 }}><thead><tr><th>Audit finding (option {a.chosen})</th><th>Kind</th><th>About</th><th>P(needs a person)</th><th>Decision</th></tr></thead>
              <tbody>{a.audits[a.chosen].findings.map((f: any) => (
                <tr key={f.id}><td>{f.detail}</td><td>{f.kind}</td><td>{f.op_id ?? f.change_id ?? '—'} {f.page && <PageLink page={f.page} />}</td>
                  <td>{f.p_needs_human?.toFixed(2) ?? '—'}</td><td>{a.decisions[a.chosen]?.[f.id] ?? (f.routed ? 'waiting' : 'not routed')}</td></tr>))}</tbody></table>
          )}
        </div></div>
      )}
      {a.build_check && (
        <div className={`note ${a.build_check.ok && a.tests?.passed === a.tests?.total ? 'green' : 'red'}`}>
          <b>Verification.</b> Build {a.build_check.ok ? 'matches' : 'does NOT match'} the planned diff (<span className="mono">{a.build_check.built_hash.slice(0, 12)}…</span>). {a.tests && <>{a.tests.passed} of {a.tests.total} synthetic subjects pass. <a onClick={() => set({ page: 'tests' })}>See the tests</a></>}
          {a.build_check.differences?.length > 0 && <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>{a.build_check.differences.map((d: string) => <li key={d} className="mono">{d}</li>)}</ul>}
        </div>
      )}
      {mine && st === 'gate_approve' && <div className="bar"><button className="btn ok" data-agent-id="approve-build" onClick={() => decide('approve')}>Approve build for signature</button><button className="btn" data-agent-id="reject-build" onClick={() => decide('reject')}>Reject and choose again</button></div>}
      {mine && st === 'sign' && <div className="bar"><button className="btn org" data-agent-id="open-signature" onClick={() => decide('sign')}>Sign…</button></div>}
    </>
  )
}

// ------------------------------------------------------------------ Schedule
export function Schedule() {
  const { snap, cb, v1, canEdit } = useCb()
  const old = new Set(v1.events.map((e: any) => e.id))
  const groups = cb.event_groups.map((g: any) => ({ ...g, n: cb.events.filter((e: any) => e.group === g.id).length })).filter((g: any) => g.n)
  const ordered = groups.flatMap((g: any) => cb.events.filter((e: any) => e.group === g.id))
  return (
    <>
      <PH title="Schedule" sub="Forms collected at each event. Tick a cell to assign a form to an event." />
      <Locked canEdit={canEdit} signed={snap.has_v2} />
      <div className="schedwrap"><table className="sched">
        <thead>
          <tr><th className="l">Event group</th>{groups.map((g: any) => <th key={g.id} className="l" colSpan={g.n} style={{ textAlign: 'center' }}>{g.label}</th>)}</tr>
          <tr><th className="l">Form</th>{ordered.map((e: any) => <th key={e.id} className={`rot ${old.has(e.id) ? '' : 'new'}`}><div><span>{e.label}{e.dynamic && ' ⚡'}</span></div></th>)}</tr>
        </thead>
        <tbody>
          <tr className="meta"><td style={{ textAlign: 'left' }}>Study day</td>{ordered.map((e: any) => <td key={e.id} className={old.has(e.id) ? '' : 'new'}>{e.day ?? ''}</td>)}</tr>
          <tr className="meta"><td style={{ textAlign: 'left' }}>Window (days)</td>{ordered.map((e: any) => <td key={e.id} className={old.has(e.id) ? '' : 'new'}>{fmtWin(e)}</td>)}</tr>
          {cb.forms.map((f: any) => (
            <tr key={f.id}><td className="name nw"><b>{f.id}</b> <span className="muted">{f.label}</span></td>
              {ordered.map((e: any) => {
                const on = (cb.schedule[f.id] ?? []).includes(e.id)
                return <td key={e.id} className={`c ${on && !(v1.schedule[f.id] ?? []).includes(e.id) ? 'new' : ''}`}>
                  <button data-agent-id={`sched-${f.id}-${e.id}`} role="checkbox" aria-checked={on} aria-pressed={on} aria-label={`${f.id} at ${e.id}`} disabled={!canEdit || on}
                    onClick={() => op({ kind: 'assign_form', target_id: f.id, event_ids: [e.id] })}>{on ? '✓' : ''}</button></td>
              })}</tr>))}
        </tbody></table></div>
      <p className="muted">⚡ created by a dynamic rule · green = new in this draft</p>
    </>
  )
}

// ------------------------------------------------------------------ Events
export function Events() {
  const { snap, cb, v1, canEdit } = useCb()
  const [dlg, setDlg] = useState<null | { mode: 'new' | 'edit' | 'group'; f: any }>(null)
  const old = new Set(v1.events.map((e: any) => e.id))
  const F = (k: string) => ({ value: dlg!.f[k] ?? '', onChange: (e: any) => setDlg({ ...dlg!, f: { ...dlg!.f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value } }) })
  const close = () => { setDlg(null); set({ uiError: null }) }
  const save = async () => {
    const f = dlg!.f
    const ok = dlg!.mode === 'group' ? await op({ kind: 'add_event_group', target_id: f.id?.trim(), label: f.label, repeating: !!f.repeating })
      : dlg!.mode === 'edit' ? await op({ kind: 'set_event_window', target_id: f.id, window_before: num(f.wb ?? ''), window_after: num(f.wa ?? '') })
        : await op({ kind: 'add_event', target_id: f.id?.trim(), label: f.label, group: f.group, day: num(f.day ?? ''), window_before: num(f.wb ?? ''), window_after: num(f.wa ?? ''), dynamic: !!f.dynamic })
    if (ok) close()
  }
  return (
    <>
      <PH title="Events" sub={`${cb.events.length} events in ${cb.event_groups.length} groups`}>
        <button className="btn" data-agent-id="groups-new" disabled={!canEdit} onClick={() => setDlg({ mode: 'group', f: {} })}>+ New Event Group</button>
        <button className="btn pri" data-agent-id="events-new" disabled={!canEdit} onClick={() => setDlg({ mode: 'new', f: { group: '' } })}>+ New Event</button>
      </PH>
      <Locked canEdit={canEdit} signed={snap.has_v2} />
      <table className="v"><thead><tr><th>Event ID</th><th>Label</th><th>Group</th><th>Study day</th><th>Window (days)</th><th>Created by</th><th></th></tr></thead>
        <tbody>{cb.events.map((e: any) => (
          <tr key={e.id} className={old.has(e.id) ? '' : 'new'}><td><b>{e.id}</b></td><td>{e.label}</td><td>{e.group}</td><td>{e.day ?? '—'}</td><td>{fmtWin(e)}</td>
            <td>{e.dynamic ? <span className="pill pur">⚡ Dynamic rule</span> : 'Always'}</td>
            <td><button className="btn sm" data-agent-id={`event-edit-${e.id}`} aria-label={`Edit window of ${e.id}`} disabled={!canEdit} onClick={() => setDlg({ mode: 'edit', f: { id: e.id, wb: e.window_before ?? '', wa: e.window_after ?? '' } })}>Edit window</button></td></tr>))}</tbody></table>
      {dlg && (
        <Modal title={dlg.mode === 'group' ? 'New Event Group' : dlg.mode === 'edit' ? `Event ${dlg.f.id}: visit window` : 'New Event'} onClose={close}
          footer={<button className="btn pri" data-agent-id={dlg.mode === 'group' ? 'group-save' : 'event-save'} onClick={save}>Save</button>}>
          {dlg.mode === 'group' ? <>
            <div className="fld"><label htmlFor="g1">Group ID</label><input id="g1" data-agent-id="group-id" aria-label="Group ID" {...F('id')} /></div>
            <div className="fld"><label htmlFor="g2">Label</label><input id="g2" data-agent-id="group-label" aria-label="Group label" {...F('label')} /></div>
            <label><input type="checkbox" data-agent-id="group-repeating" aria-label="Repeating" checked={!!dlg.f.repeating} onChange={F('repeating').onChange} /> Repeating (unscheduled)</label>
          </> : <>
            {dlg.mode === 'new' && <>
              <div className="fld2">
                <div className="fld"><label htmlFor="e1">Event ID</label><input id="e1" data-agent-id="event-id" aria-label="Event ID" {...F('id')} /></div>
                <div className="fld"><label htmlFor="e3">Event group</label><select id="e3" data-agent-id="event-group" aria-label="Event group" {...F('group')}><option value="">Select…</option>{cb.event_groups.map((g: any) => <option key={g.id} value={g.id}>{g.id} · {g.label}</option>)}</select></div>
              </div>
              <div className="fld"><label htmlFor="e2">Label</label><input id="e2" data-agent-id="event-label" aria-label="Event label" {...F('label')} /></div>
              <div className="fld"><label htmlFor="e4">Study day</label><input id="e4" data-agent-id="event-day" aria-label="Study day (relative to the anchor event; empty if unscheduled)" inputMode="numeric" {...F('day')} /></div>
            </>}
            <div className="fld2">
              <div className="fld"><label htmlFor="e5">Window before (days)</label><input id="e5" data-agent-id="event-window-before" aria-label="Window before in days (empty = no window)" inputMode="numeric" {...F('wb')} /></div>
              <div className="fld"><label htmlFor="e6">Window after (days)</label><input id="e6" data-agent-id="event-window-after" aria-label="Window after in days (empty = no window)" inputMode="numeric" {...F('wa')} /></div>
            </div>
            <div className="hint muted">Leave both empty when the protocol gives no day window.</div>
            {dlg.mode === 'new' && <label style={{ display: 'block', marginTop: 8 }}><input type="checkbox" data-agent-id="event-dynamic" aria-label="Dynamic: created by a rule" checked={!!dlg.f.dynamic} onChange={F('dynamic').onChange} /> Dynamic: created only when a rule adds it</label>}
          </>}
        </Modal>
      )}
    </>
  )
}

// ------------------------------------------------------------------ Forms
export function Forms() {
  const { snap, cb, v1, canEdit } = useCb()
  const [sel, setSel] = useState<string>(cb.forms[0]?.id)
  const [dlg, setDlg] = useState<null | { mode: 'form' | 'item'; f: any }>(null)
  const form = cb.forms.find((f: any) => f.id === sel) ?? cb.forms[0]
  const F = (k: string) => ({ value: dlg!.f[k] ?? '', onChange: (e: any) => setDlg({ ...dlg!, f: { ...dlg!.f, [k]: e.target.value } }) })
  const close = () => { setDlg(null); set({ uiError: null }) }
  const save = async () => {
    const f = dlg!.f
    const ok = dlg!.mode === 'form' ? await op({ kind: 'add_form', target_id: f.id?.trim(), label: f.label })
      : await op({ kind: 'add_item', target_id: form.id, item: { id: f.id?.trim(), label: f.label, type: f.type || 'text', codelist: f.codelist || undefined } })
    if (ok) { if (dlg!.mode === 'form') setSel(f.id.trim()); close() }
  }
  const oldItems = new Set((v1.forms.find((f: any) => f.id === form?.id)?.items ?? []).map((i: any) => i.id))
  return (
    <>
      <PH title="Forms" sub={`${cb.forms.length} forms`}><button className="btn pri" data-agent-id="forms-new" disabled={!canEdit} onClick={() => setDlg({ mode: 'form', f: {} })}>+ New Form</button></PH>
      <Locked canEdit={canEdit} signed={snap.has_v2} />
      <div className="grid2" style={{ gridTemplateColumns: '300px 1fr' }}>
        <table className="v"><thead><tr><th>Form</th><th>Events</th></tr></thead>
          <tbody>{cb.forms.map((f: any) => <tr key={f.id} className={f.id === form?.id ? 'sel' : ''}><td><button className="btn sm" data-agent-id={`form-open-${f.id}`} onClick={() => setSel(f.id)}>{f.id}</button> {f.label}</td><td>{(cb.schedule[f.id] ?? []).length}</td></tr>)}</tbody></table>
        {form && <div className="card"><h2>{form.id} · {form.label}<button className="btn sm" style={{ marginLeft: 'auto' }} data-agent-id="item-new" disabled={!canEdit} onClick={() => setDlg({ mode: 'item', f: { type: 'text' } })}>+ Add item</button></h2>
          <table className="v"><thead><tr><th>Item</th><th>Label</th><th>Type</th><th>Codelist</th></tr></thead>
            <tbody>{form.items.map((i: any) => <tr key={i.id} className={oldItems.has(i.id) || !v1.forms.some((f: any) => f.id === form.id) && false ? '' : 'new'}><td><b>{i.id}</b></td><td>{i.label}</td><td>{i.type}</td><td>{i.codelist ?? '—'}</td></tr>)}</tbody></table>
          <div className="bd muted">Collected at: {(cb.schedule[form.id] ?? []).join(', ') || 'no events yet'}</div></div>}
      </div>
      {dlg && (
        <Modal title={dlg.mode === 'form' ? 'New Form' : `Add item to ${form.id}`} onClose={close} footer={<button className="btn pri" data-agent-id={dlg.mode === 'form' ? 'form-save' : 'item-save'} onClick={save}>Save</button>}>
          <div className="fld"><label>ID<input data-agent-id={dlg.mode === 'form' ? 'form-id' : 'item-id'} aria-label={dlg.mode === 'form' ? 'Form ID' : 'Item ID'} {...F('id')} /></label></div>
          <div className="fld"><label>Label<input data-agent-id={dlg.mode === 'form' ? 'form-label' : 'item-label'} aria-label="Label" {...F('label')} /></label></div>
          {dlg.mode === 'item' && <div className="fld2">
            <div className="fld"><label>Type<select data-agent-id="item-type" aria-label="Item type" {...F('type')}>{['text', 'number', 'date', 'datetime', 'codelist'].map(t => <option key={t}>{t}</option>)}</select></label></div>
            <div className="fld"><label>Codelist<select data-agent-id="item-codelist" aria-label="Codelist" {...F('codelist')}><option value="">None</option>{Object.keys(cb.codelists).map(c => <option key={c}>{c}</option>)}</select></label></div>
          </div>}
        </Modal>
      )}
    </>
  )
}

// ------------------------------------------------------------------ Codelists
export function Codelists() {
  const { snap, cb, v1, canEdit } = useCb()
  const ids = Object.keys(cb.codelists)
  const [sel, setSel] = useState(ids.includes('PKTPT') ? 'PKTPT' : ids[0])
  const [entry, setEntry] = useState('')
  const err = useStore(s => s.uiError)
  const add = async () => { if (await op({ kind: 'add_codelist_entries', target_id: sel, entries: [entry.trim()] })) setEntry('') }
  return (
    <>
      <PH title="Codelists" sub={`${ids.length} codelists`} />
      <Locked canEdit={canEdit} signed={snap.has_v2} />
      <div className="grid2" style={{ gridTemplateColumns: '260px 1fr' }}>
        <table className="v"><thead><tr><th>Codelist</th><th>Entries</th></tr></thead>
          <tbody>{ids.map(c => <tr key={c} className={c === sel ? 'sel' : ''}><td><button className="btn sm" data-agent-id={`codelist-open-${c}`} onClick={() => setSel(c)}>{c}</button></td><td>{cb.codelists[c].length}</td></tr>)}</tbody></table>
        <div className="card"><h2>{sel}</h2><div className="bd">
          {err && <div className="err" role="alert">{err}</div>}
          <table className="v" style={{ marginBottom: 10 }}><tbody>{cb.codelists[sel].map((x: string) => <tr key={x} className={(v1.codelists[sel] ?? []).includes(x) ? '' : 'new'}><td>{x}</td></tr>)}</tbody></table>
          <div className="bar"><input data-agent-id="codelist-entry" aria-label={`New entry for codelist ${sel}`} placeholder="New entry" value={entry} disabled={!canEdit} onChange={e => setEntry(e.target.value)} onKeyDown={e => e.key === 'Enter' && entry.trim() && add()} style={{ padding: '5px 8px', border: '1px solid #bbb', borderRadius: 2, width: 220 }} />
            <button className="btn pri" data-agent-id="codelist-add" disabled={!canEdit || !entry.trim()} onClick={add}>Add entry</button></div>
        </div></div>
      </div>
    </>
  )
}

// ------------------------------------------------------------------ Rules
const ruleText = (r: any) => `If      ${r.when}\nThen    ${r.then?.action === 'add_events' ? 'Add events ' + r.then.events.join(', ') : `Open query "${r.then?.message ?? ''}"`}${r.scope?.events?.length ? `\nScope   ${r.scope.form} @ ${r.scope.events.join(', ')}` : ''}`

export function Rules() {
  const { snap, cb, v1, canEdit } = useCb()
  const [dlg, setDlg] = useState<null | { edit: string | null; f: any }>(null)
  const F = (k: string) => ({ value: dlg!.f[k] ?? '', onChange: (e: any) => setDlg({ ...dlg!, f: { ...dlg!.f, [k]: e.target.value } }) })
  const csv = (s: string) => (s ?? '').split(',').map(x => x.trim()).filter(Boolean)
  const close = () => { setDlg(null); set({ uiError: null }) }
  const openEdit = (r: any) => setDlg({ edit: r.id, f: { id: r.id, type: r.type, description: r.description ?? '', when: r.when, events: (r.then?.events ?? []).join(', '), message: r.then?.message ?? '', sform: r.scope?.form ?? '', sevents: (r.scope?.events ?? []).join(', ') } })
  const save = async () => {
    const f = dlg!.f
    const rule: any = { type: f.type, description: f.description, when: f.when }
    if (f.type === 'dynamic') rule.then = { action: 'add_events', events: csv(f.events) }
    else { rule.then = { action: 'open_query', message: f.message }; rule.scope = { form: f.sform, events: csv(f.sevents) } }
    if (await op({ kind: dlg!.edit ? 'modify_rule' : 'add_rule', target_id: dlg!.edit ?? f.id?.trim(), rule })) close()
  }
  const old = new Map(v1.rules.map((r: any) => [r.id, JSON.stringify(r)]))
  const dyn = dlg?.f.type === 'dynamic'
  return (
    <>
      <PH title="Rules" sub="Dynamic rules add events for some subjects. Edit checks open queries. Both are executed by the rule engine."><button className="btn pri" data-agent-id="rules-new" disabled={!canEdit} onClick={() => setDlg({ edit: null, f: { type: 'dynamic' } })}>+ New Rule</button></PH>
      <Locked canEdit={canEdit} signed={snap.has_v2} />
      <table className="v"><thead><tr><th>Rule</th><th>Type</th><th>Definition</th><th></th></tr></thead>
        <tbody>{cb.rules.map((r: any) => (
          <tr key={r.id} className={old.get(r.id) === JSON.stringify(r) ? '' : 'new'}><td><b>{r.id}</b><div className="muted">{r.description}</div></td>
            <td>{r.type === 'dynamic' ? <span className="pill pur">Dynamic</span> : <span className="pill blue">Edit check</span>}</td>
            <td><pre className="rule">{ruleText(r)}</pre></td>
            <td><button className="btn sm" data-agent-id={`rule-edit-${r.id}`} aria-label={`Edit rule ${r.id}`} disabled={!canEdit} onClick={() => openEdit(r)}>Edit</button></td></tr>))}</tbody></table>
      {dlg && (
        <Modal title={dlg.edit ? `Edit rule ${dlg.edit}` : 'New Rule'} onClose={close} footer={<button className="btn pri" data-agent-id="rule-save" onClick={save}>Save</button>}>
          <div className="fld2">
            <div className="fld"><label>Rule ID<input data-agent-id="rule-id" aria-label="Rule ID" disabled={!!dlg.edit} {...F('id')} /></label></div>
            <div className="fld"><label>Type<select data-agent-id="rule-type" aria-label="Rule type" disabled={!!dlg.edit} {...F('type')}><option value="dynamic">dynamic</option><option value="edit_check">edit_check</option></select></label></div>
          </div>
          <div className="fld"><label>Description<input data-agent-id="rule-description" aria-label="Description" {...F('description')} /></label></div>
          <div className="fld"><label>If (condition)<textarea data-agent-id="rule-when" aria-label="Condition: the rule fires when this is true" rows={2} className="mono" {...F('when')} /></label>
            <div className="hint">References: $EVENT.FORM.ITEM or FORM.ITEM · durations 48h, 2d · abs(), is_blank()</div></div>
          {dyn ? <div className="fld"><label>Then add events (comma separated)<input data-agent-id="rule-then-events" aria-label="Then add events, comma separated" {...F('events')} /></label></div> : <>
            <div className="fld"><label>Then open query with text<input data-agent-id="rule-then-message" aria-label="Then open query with this text" {...F('message')} /></label></div>
            <div className="fld2">
              <div className="fld"><label>Scope: form<select data-agent-id="rule-scope-form" aria-label="Scope form" {...F('sform')}><option value="">Select…</option>{cb.forms.map((f: any) => <option key={f.id}>{f.id}</option>)}</select></label></div>
              <div className="fld"><label>Scope: events (comma separated)<input data-agent-id="rule-scope-events" aria-label="Scope events, comma separated" {...F('sevents')} /></label></div>
            </div></>}
        </Modal>
      )}
    </>
  )
}

// ------------------------------------------------------------------ Review plans
export function Review() {
  const { snap, cb, v1, canEdit } = useCb()
  return (
    <>
      <PH title="Review Plans" sub="Which forms need source data verification, at which events." />
      <Locked canEdit={canEdit} signed={snap.has_v2} />
      {cb.review_plans.map((p: any) => {
        const forms = [...new Set([...Object.keys(p.coverage), 'PK'])].filter(f => cb.forms.some((x: any) => x.id === f))
        const o = v1.review_plans.find((x: any) => x.id === p.id)?.coverage ?? {}
        return (
          <div className="card" key={p.id}><h2>{p.id} · {p.label}</h2><div className="bd schedwrap"><table className="sched">
            <thead><tr><th className="l">Form</th>{cb.events.map((e: any) => <th key={e.id} className="rot"><div><span>{e.id}</span></div></th>)}</tr></thead>
            <tbody>{forms.map(f => <tr key={f}><td className="nw"><b>{f}</b></td>{cb.events.map((e: any) => {
              const sched = (cb.schedule[f] ?? []).includes(e.id), on = (p.coverage[f] ?? []).includes(e.id)
              return <td key={e.id} className={`c ${on && !(o[f] ?? []).includes(e.id) ? 'new' : ''}`}>{sched && <button data-agent-id={`review-${p.id}-${f}-${e.id}`} role="checkbox" aria-checked={on} aria-pressed={on} aria-label={`${f} at ${e.id} in ${p.id}`} disabled={!canEdit || on} onClick={() => op({ kind: 'update_review_plan', target_id: p.id, form_id: f, event_ids: [e.id] })}>{on ? '✓' : ''}</button>}</td>
            })}</tr>)}</tbody></table></div></div>)
      })}
    </>
  )
}

// ------------------------------------------------------------------ Test subjects
export function Tests() {
  const snap = useStore(s => s.snap)
  const a = snap.amendment
  const b = a.chosen ? a.builds[a.chosen] : null
  const t = a.tests ?? b?.tests_planned
  const subj = new Map((b?.subjects ?? []).map((s: any) => [s.id, s]))
  return (
    <>
      <PH title="Test Subjects" sub="Synthetic subjects written by the planner model and executed by the deterministic rule engine. The results below are real engine output." />
      {!t && <div className="note blue">No tests yet. They run after a design option is built.</div>}
      {t && <>
        <div className={`note ${t.passed === t.total ? 'green' : 'red'}`}><b>{t.passed} of {t.total} pass</b> {a.tests ? 'on the built casebook.' : 'on the planned diff (the build has not been verified yet).'} Rules that fired at least once: {t.rules_fired.join(', ') || 'none'}.</div>
        <table className="v"><thead><tr><th>Subject</th><th>Scenario</th><th>Exercises</th><th>Expected</th><th>Engine result</th><th>Result</th></tr></thead>
          <tbody>{t.results.map((r: any) => (
            <tr key={r.subject}><td><b>{r.subject}</b></td><td>{r.description}<details><summary className="muted">data</summary><pre className="rule">{JSON.stringify((subj.get(r.subject) as any)?.data, null, 1)}</pre></details></td>
              <td>{r.exercises.join(', ')}</td>
              <td>{r.checks.map((c: any) => <div key={c.what}>{c.what}: {c.expected.join(', ') || 'none'}</div>)}</td>
              <td>{r.checks.map((c: any) => <div key={c.what} className={c.pass ? '' : 'bad-t'}>{c.what}: {c.actual.join(', ') || 'none'}</div>)}{r.errors.map((e: any, i: number) => <div key={i} className="bad-t">{e.rule}: {e.error}</div>)}</td>
              <td>{r.pass ? <span className="ok-t">✓ Pass</span> : <span className="bad-t">✗ Fail</span>}</td></tr>))}</tbody></table>
      </>}
    </>
  )
}
