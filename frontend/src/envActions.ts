// Action layer for the embedded RELAY environment (the existing audit-amendment-demo site,
// served from our own origin at /env/ so the browser lets the agent act inside the frame).
// That site has no data-agent-id attributes, so ids are derived from each control's label.
import { get } from './store'
import type { ActResult, Action, TreeNode } from './agentActions'

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))
export const ENV_WIDTH = 1280   // the site is laid out at this width, then scaled to the panel

const frame = () => document.getElementById('env-frame') as HTMLIFrameElement | null
const doc = () => { try { return frame()?.contentDocument ?? null } catch { return null } }

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 36)

function clickable(d: Document): HTMLElement[] {
  const dialog = d.querySelector<HTMLElement>('#layer .modal')
  const scope: ParentNode = dialog ?? d
  return [...scope.querySelectorAll<HTMLElement>('button, a, [onclick], select, input, textarea, aside .it, .nav span, summary')].filter(el => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && !(el as HTMLButtonElement).disabled
  })
}

/** Label every control with a stable id (from its text) and return the tree for the navigator. */
export function observeEnv(): TreeNode[] {
  const d = doc()
  if (!d) return []
  const seen: Record<string, number> = {}
  const out: TreeNode[] = []
  for (const el of clickable(d)) {
    const label = (el.getAttribute('aria-label') || (el as HTMLInputElement).placeholder || el.textContent || el.getAttribute('title') || '').trim().replace(/\s+/g, ' ').slice(0, 70)
    if (!label && !(el instanceof HTMLSelectElement)) continue
    const base = slug(label) || el.tagName.toLowerCase()
    const n = (seen[base] = (seen[base] ?? 0) + 1)
    const id = n === 1 ? base : `${base}-${n}`
    el.dataset.agentId = id
    const tag = el.tagName
    const node: TreeNode = { id, role: tag === 'SELECT' ? 'select' : tag === 'INPUT' || tag === 'TEXTAREA' ? 'textbox' : tag === 'A' ? 'link' : 'button', label }
    if (el instanceof HTMLSelectElement) { node.value = el.value; node.options = [...el.options].map(o => o.value).filter(Boolean).slice(0, 12) }
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) node.value = el.value
    out.push(node)
    if (out.length >= 90) break
  }
  return out
}

function locate(args: Record<string, any>): HTMLElement | null {
  const d = doc()
  if (!d) return null
  observeEnv()
  if (args.agent_id) {
    const el = d.querySelector<HTMLElement>(`[data-agent-id="${CSS.escape(String(args.agent_id))}"]`)
    if (el) return el
  }
  const want = String(args.label ?? args.agent_id ?? '').trim().toLowerCase()
  if (!want) return null
  return clickable(d).find(el => (el.textContent ?? '').trim().toLowerCase() === want)
    ?? clickable(d).find(el => (el.textContent ?? '').trim().toLowerCase().startsWith(want)) ?? null
}

/** Move the shared agent cursor (which lives in the outer page) over an element inside the frame. */
async function moveCursor(el: HTMLElement) {
  const f = frame()!
  el.scrollIntoView({ block: 'center', inline: 'nearest' })
  await sleep(40)
  const fr = f.getBoundingClientRect()
  const scale = fr.width / f.offsetWidth
  const r = el.getBoundingClientRect()
  const cur = document.getElementById('agent-cursor')
  const dur = get().fast ? 60 : 620
  if (cur) {
    cur.style.setProperty('--t', `${dur}ms`)
    cur.style.opacity = '1'
    cur.style.left = `${fr.left + (r.left + Math.min(r.width / 2, 50)) * scale}px`
    cur.style.top = `${fr.top + (r.top + r.height / 2) * scale}px`
  }
  await sleep(dur + 60)
  const prev = el.style.outline
  el.style.outline = '3px solid #0D9488'
  el.style.outlineOffset = '2px'
  setTimeout(() => { el.style.outline = prev }, 900)
  if (cur) { cur.classList.remove('click'); void cur.offsetWidth; cur.classList.add('click') }
  await sleep(get().fast ? 20 : 220)
}

function pageName(): string {
  const d = doc()
  if (!d) return 'not loaded'
  const on = d.querySelector('aside .it.on')?.textContent?.trim()
  const role = d.querySelector('.demobar button.on')?.textContent?.trim()
  return [role, on].filter(Boolean).join(' › ') || d.title
}

export async function performEnv(a: Action): Promise<ActResult> {
  let error: string | undefined
  for (let i = 0; i < 40 && !doc()?.body?.childElementCount; i++) await sleep(100)   // wait for the frame to load
  if (!doc()) error = 'The RELAY environment is not loaded.'
  else if (a.name === 'navigate') error = 'This application has no navigate action. Click a button or menu item instead.'
  else if (a.name !== 'observe') {
    const el = locate(a.args)
    if (!el) error = `No element with agent_id "${a.args.agent_id ?? a.args.label}" is on screen.`
    else {
      await moveCursor(el)
      if (a.name === 'click') el.click()
      else if (a.name === 'type' && (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) {
        el.focus(); el.value = String(a.args.text ?? ''); el.dispatchEvent(new Event('input', { bubbles: true }))
      } else if (a.name === 'select' && el instanceof HTMLSelectElement) {
        el.value = String(a.args.value ?? ''); el.dispatchEvent(new Event('change', { bubbles: true }))
      } else if (a.name !== 'scroll') error = `"${a.args.agent_id}" cannot be used with ${a.name}.`
      await sleep(get().fast ? 60 : 350)
    }
  }
  return { ok: !error, error, tree: observeEnv(), page: pageName(), screenshot: null }
}
