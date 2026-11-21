// CRO workspace (deployment and site rollout) and Site workspace (what changes, transition plan).
import { useState } from 'react'
import { decide, set, uiPost, useStore } from '../store'
import { PH, PageLink, Signature, fmtWin } from './Shared'

// ------------------------------------------------------------------ CRO
export function Versions() {
  const snap = useStore(s => s.snap)
  const sig = snap.signatures.sponsor
  return (
    <>
      <PH title="Casebook Versions" sub={snap.study.num} />
      <table className="v"><thead><tr><th>Version</th><th>Status</th><th>Events</th><th>Rules</th><th>Approved by</th></tr></thead>
        <tbody>
          <tr><td><b>Version 1</b></td><td><span className="pill solid">In production</span></td><td>{snap.v1.events.length}</td><td>{snap.v1.rules.length}</td><td className="muted">Initial build</td></tr>
          <tr className={snap.has_v2 ? 'new' : 'dim'}><td><b>Version 2</b></td>
            <td>{!snap.has_v2 ? <span className="pill gray">Not signed by the sponsor yet</span> : snap.deploy.production ? <span className="pill solid">Deployed to production</span> : <span className="pill med">Signed · ready to deploy</span>}</td>
            <td>{snap.has_v2 ? snap.casebook.events.length : '—'}</td><td>{snap.has_v2 ? snap.casebook.rules.length : '—'}</td>
            <td>{sig ? <Signature m={sig} /> : '—'}</td></tr>
        </tbody></table>
      {snap.has_v2 && <p><a onClick={() => set({ page: 'deployment' })}>Go to deployment</a></p>}
    </>
  )
}

export function Deployment() {
  const snap = useStore(s => s.snap)
  const err = useStore(s => s.uiError)
  const open = snap.flow.global === 'deploy'
  const prod = snap.deploy.production
  return (
    <>
      <PH title="Deployment" sub="Move a signed casebook version into production. The sponsor signature is re-checked first." />
      {err && <div className="err" role="alert">{err}</div>}
      <table className="v"><thead><tr><th>Environment</th><th>Active version</th><th>Casebook hash</th><th></th></tr></thead>
        <tbody>
          <tr><td><b>Validation (UAT)</b></td><td>Version {snap.has_v2 ? 2 : 1}</td><td className="mono muted">—</td><td></td></tr>
          <tr className={prod ? 'new' : ''}><td><b>Production</b></td><td>Version {prod ? 2 : 1}</td><td className="mono muted">{prod ? prod.casebook_hash.slice(0, 24) + '…' : '—'}</td>
            <td><button className="btn pri" data-agent-id="deploy-production" disabled={!open || !!prod} onClick={() => uiPost('/api/deploy/production', {})}>Deploy version 2 to production</button></td></tr>
        </tbody></table>
      {!snap.has_v2 && <div className="note" style={{ marginTop: 12 }}>Nothing to deploy. The sponsor has not signed casebook version 2.</div>}
      {snap.has_v2 && !open && !prod && <div className="note blue" style={{ marginTop: 12 }}>Version 2 is signed. Ask the agent to deploy it, or say “deploy it”.</div>}
      {snap.signatures.cro && <div style={{ marginTop: 12 }}><Signature m={snap.signatures.cro} /></div>}
    </>
  )
}

export function Rollout() {
  const snap = useStore(s => s.snap)
  const err = useStore(s => s.uiError)
  const [sel, setSel] = useState<string[]>([])
  const open = snap.flow.global === 'deploy' && !!snap.deploy.production
  const sites = snap.sites as any[]
  const eligible = sites.filter(s => s.ec_approved && !s.deployed).map(s => s.id)
  const live = sites.filter(s => s.deployed).length
  const deploy = async () => { if (await uiPost('/api/deploy/sites', { site_ids: sel })) setSel([]) }
  return (
    <>
      <PH title="Site Rollout" sub={`${live} of ${sites.length} sites on casebook version 2 · sites need ethics committee approval of protocol Ver. 1.1 first`}>
        <button className="btn" data-agent-id="rollout-select-eligible" disabled={!open || eligible.length === 0} onClick={() => setSel(eligible)}>Select eligible sites ({eligible.length})</button>
        <button className="btn pri" data-agent-id="rollout-deploy" disabled={!open || sel.length === 0} onClick={deploy}>Deploy to selected sites ({sel.length})</button>
      </PH>
      {err && <div className="err" role="alert">{err}</div>}
      {snap.flow.global === 'rollout' && snap.flow.owner === snap.user.role && <div className="bar"><button className="btn org" data-agent-id="open-signature" onClick={() => decide('sign')}>Sign the rollout…</button></div>}
      <table className="v"><thead><tr><th></th><th>Site</th><th>Facility</th><th>Country</th><th>Principal investigator</th><th>EC approval</th><th>Casebook</th><th>Site sign-off</th></tr></thead>
        <tbody>{sites.map(s => (
          <tr key={s.id} className={s.deployed ? 'new' : ''}>
            <td><input type="checkbox" data-agent-id={`rollout-site-${s.id}`} aria-label={`Select ${s.id}`} disabled={!open || !!s.deployed} checked={sel.includes(s.id)} onChange={e => setSel(e.target.checked ? [...sel, s.id] : sel.filter(x => x !== s.id))} /></td>
            <td><b>{s.id}</b></td><td>{s.name}<div className="muted">{s.city}</div></td><td>{s.country}</td><td>{s.pi}</td>
            <td>{s.ec_approved ? <span className="ok-t">✓ {s.ec_approved}</span> : <span className="pill med">Submitted {s.ec_submitted}, pending</span>}</td>
            <td>{s.deployed ? <span className="pill solid">Version 2</span> : <span className="pill gray">Version 1</span>}</td>
            <td>{s.signed_off ? <span className="ok-t">✓ {s.signed_off.printed_name}</span> : s.deployed ? <span className="muted">waiting</span> : '—'}</td>
          </tr>))}</tbody></table>
    </>
  )
}

// ------------------------------------------------------------------ Site
export function Changes() {
  const snap = useStore(s => s.snap)
  const live = !!snap.site.deployed
  const old = new Set(snap.v1.events.map((e: any) => e.id))
  const added = snap.has_v2 ? snap.casebook.events.filter((e: any) => !old.has(e.id)) : []
  const dyn = snap.has_v2 ? snap.casebook.rules.filter((r: any) => r.type === 'dynamic' && !snap.v1.rules.some((x: any) => x.id === r.id)) : []
  return (
    <>
      <PH title="What Changes at Your Site" sub={`${snap.site.id} · ${snap.site.name} · protocol Ver. 1.1`} />
      {!live && <div className="note">Casebook version 2 is not live at your site yet{snap.site.ec_approved ? '' : ' (ethics committee approval is pending)'}. Nothing changes for now.</div>}
      {live && <>
        <div className="note green"><b>Casebook version 2 is live</b> at your site since {snap.site.deployed.on}.</div>
        {snap.amendment.summary && <p>{snap.amendment.summary}</p>}
        <div className="card"><h2>New visits</h2>
          <table className="v"><thead><tr><th>Visit</th><th>Study day</th><th>Window (days)</th><th>Forms</th><th>Who gets it</th></tr></thead>
            <tbody>{added.map((e: any) => <tr key={e.id}><td><b>{e.label}</b></td><td>{e.day ?? 'unscheduled'}</td><td>{fmtWin(e)}</td>
              <td>{Object.entries(snap.casebook.schedule).filter(([, v]: any) => v.includes(e.id)).map(([k]) => k).join(', ')}</td>
              <td>{e.dynamic ? dyn.filter((r: any) => r.then.events.includes(e.id)).map((r: any) => <code key={r.id}>{r.when}</code>) : 'Everyone'}</td></tr>)}</tbody></table></div>
        <p>Source: {[...new Set(snap.amendment.changes.filter((c: any) => c.affects_edc).map((c: any) => c.page))].map((p: any, i: number) => <span key={p}>{i > 0 && ', '}<PageLink page={p} quote={snap.amendment.changes.find((c: any) => c.page === p)?.quote} /></span>)}</p>
        <p><a onClick={() => set({ page: 'transition' })}>See what this means for participants already enrolled</a></p>
      </>}
    </>
  )
}

export function Transition() {
  const snap = useStore(s => s.snap)
  const err = useStore(s => s.uiError)
  const plan = snap.plan
  const live = !!snap.site.deployed
  const sig = snap.signatures.site[snap.site.id]
  return (
    <>
      <PH title="Transition Plan" sub="How the new schedule maps onto participants already in the trial. Calculated by a fixed rule from randomisation dates, not by a model.">
        <button className="btn pri" data-agent-id="transition-calculate" disabled={!live} onClick={() => uiPost('/api/transition/calculate', {})}>Calculate plan</button>
      </PH>
      {err && <div className="err" role="alert">{err}</div>}
      {!live && <div className="note">Casebook version 2 is not live at your site yet.</div>}
      {plan && <>
        <div className="note blue"><b>{plan.affected} participant{plan.affected !== 1 && 's'} affected</b>, {plan.upcoming_visits} new visit{plan.upcoming_visits !== 1 && 's'} to schedule. Calculated for {plan.calculated_on}. Rule: {plan.rule}</div>
        <table className="v"><thead><tr><th>Participant</th><th>Status</th><th>Study day</th><th>What to do</th><th>New visits</th></tr></thead>
          <tbody>{plan.participants.map((p: any) => (
            <tr key={p.subject} className={p.affected ? 'new' : 'dim'}><td><b>{p.subject}</b></td><td>{p.status}</td><td>{p.study_day ?? '—'}</td>
              <td>{p.affected ? 'Re-consent, then schedule the visits marked below' : p.reason}</td>
              <td><div className="chips">{p.visits.map((v: any) => <span key={v.event} className={`chip ${v.state === 'missed' ? 'miss' : v.note.startsWith('Due') ? 'soon' : 'up'}`} title={v.note}>{v.event} · {v.due}</span>)}</div></td></tr>))}</tbody></table>
        <p className="muted">Green: schedule · amber: due within 7 days, re-consent first · struck through: before the amendment took effect, not created.</p>
      </>}
      {snap.flow.state === 'site_sign' && <div className="bar"><button className="btn org" data-agent-id="open-signature" onClick={() => decide('sign')}>Sign off…</button></div>}
      {sig && <Signature m={sig} />}
    </>
  )
}
