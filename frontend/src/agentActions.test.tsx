// Tests for the DOM action layer: the navigator's hands.
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('html-to-image', () => ({ toPng: async () => 'data:image/png;base64,AAAA' }))
import { observe, perform } from './agentActions'
import { set } from './store'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
// jsdom has no layout: give every element a size so "visible" is true
Element.prototype.getBoundingClientRect = () => ({ width: 10, height: 10, left: 0, top: 0, right: 10, bottom: 10, x: 0, y: 0, toJSON() {} }) as DOMRect
Element.prototype.scrollIntoView = () => {}
;(globalThis as any).CSS ??= { escape: (s: string) => s.replace(/"/g, '\\"') }
;(globalThis as any).requestAnimationFrame = (f: any) => setTimeout(f, 0)

let saved: any = null
function Fixture() {
  const [open, setOpen] = useState(false)
  const [id, setId] = useState('')
  const [group, setGroup] = useState('')
  const [dyn, setDyn] = useState(false)
  return (
    <div id="app-canvas">
      <button data-agent-id="nav-events" onClick={() => set({ page: 'events' })}>Events</button>
      <button data-agent-id="events-new" onClick={() => setOpen(true)}>+ New Event</button>
      <button data-agent-id="locked" disabled>Locked</button>
      <button data-agent-id="bad-save" onClick={() => setTimeout(() => set({ uiError: 'Event V2 already exists' }), 0)}>Save duplicate</button>
      {open && (
        <div role="dialog">
          <input data-agent-id="event-id" aria-label="Event ID" value={id} onChange={e => setId(e.target.value)} />
          <select data-agent-id="event-group" aria-label="Event group" value={group} onChange={e => setGroup(e.target.value)}><option value="">Select…</option><option value="TRT">TRT · Treatment</option><option value="FU">FU · Follow-up</option></select>
          <input type="checkbox" data-agent-id="event-dynamic" aria-label="Dynamic" checked={dyn} onChange={e => setDyn(e.target.checked)} />
          <button data-agent-id="event-save" onClick={() => { saved = { id, group, dyn }; setOpen(false) }}>Save</button>
        </div>
      )}
    </div>
  )
}

const run = (name: any, args: any) => act(() => perform({ name, args }, { shot: false }))

beforeEach(async () => {
  document.body.innerHTML = '<div id="root"></div><div id="agent-cursor"></div>'
  saved = null
  set({ fast: true, page: 'amendment', uiError: null })
  await act(async () => { createRoot(document.getElementById('root')!).render(<Fixture />) })
})

describe('action layer', () => {
  it('lists elements by agent id with role, label and state', () => {
    const t = observe()
    expect(t.map(n => n.id)).toEqual(['nav-events', 'events-new', 'locked', 'bad-save'])
    expect(t[1]).toMatchObject({ role: 'button', label: '+ New Event' })
    expect(t[2].disabled).toBe(true)
  })

  it('navigates, opens a dialog, and then only sees the dialog', async () => {
    expect((await run('navigate', { page: 'events' })).page).toBe('events')
    const r = await run('click', { agent_id: 'events-new' })
    expect(r.ok).toBe(true)
    expect(r.tree.map(n => n.id)).toEqual(['event-id', 'event-group', 'event-dynamic', 'event-save'])
    expect(r.tree[1].options).toEqual(['TRT', 'FU'])
  })

  it('types, selects and clicks through real React handlers', async () => {
    await run('click', { agent_id: 'events-new' })
    const typed = await run('type', { agent_id: 'event-id', text: 'V2.1' })
    expect(typed.tree.find(n => n.id === 'event-id')!.value).toBe('V2.1')
    expect((await run('select', { agent_id: 'event-group', value: 'TRT' })).ok).toBe(true)
    expect((await run('click', { agent_id: 'event-dynamic' })).tree.find(n => n.id === 'event-dynamic')!.value).toBe('checked')
    const r = await run('click', { agent_id: 'event-save' })
    expect(saved).toEqual({ id: 'V2.1', group: 'TRT', dyn: true })
    expect(r.tree.some(n => n.id === 'event-save')).toBe(false)  // dialog closed
  })

  it('reports errors instead of acting', async () => {
    expect((await run('click', { agent_id: 'nope' })).error).toMatch(/No element/)
    expect((await run('click', { agent_id: 'locked' })).error).toMatch(/disabled/)
    expect((await run('type', { agent_id: 'events-new', text: 'x' })).error).toMatch(/not a text field/)
    await run('click', { agent_id: 'events-new' })
    expect((await run('select', { agent_id: 'event-group', value: 'ZZZ' })).error).toMatch(/no option "ZZZ"\. Options: TRT, FU/)
    expect((await run('click', { agent_id: 'nav-events' })).error).toMatch(/dialog is open/)   // page behind a dialog
    expect((await run('navigate', { page: 'events' })).error).toMatch(/dialog is open/)
  })

  it('surfaces an error the application showed after the action', async () => {
    const r = await run('click', { agent_id: 'bad-save' })
    expect(r.ok).toBe(false)
    expect(r.error).toMatch(/application refused: Event V2 already exists/)
  })
})
