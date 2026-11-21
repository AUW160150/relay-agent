// DOM action layer. The navigator model names an element by its stable data-agent-id;
// this module moves the visible cursor there, highlights it and performs the action
// through real DOM events, so the application behaves exactly as it does for a person.
import { toPng } from 'html-to-image'
import { get, idle, set } from './store'

export type Action = { name: 'observe' | 'navigate' | 'click' | 'type' | 'select' | 'scroll'; args: Record<string, any> }
export type TreeNode = { id: string; role: string; label: string; value?: string; disabled?: boolean; options?: string[] }
export type ActResult = { ok: boolean; error?: string; message?: string; tree: TreeNode[]; page: string; screenshot: string | null; expected?: string }

let halted = false
export const halt = () => { halted = true }
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))
const speed = () => (get().fast ? 0.12 : 1)

export function find(id: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-agent-id="${CSS.escape(id)}"]`)
}

function visible(el: HTMLElement): boolean {
  if (el.closest('[inert]')) return false
  const r = el.getBoundingClientRect()
  return r.width > 0 && r.height > 0
}

function roleOf(el: HTMLElement): string {
  const t = el.tagName
  if (t === 'SELECT') return 'select'
  if (t === 'TEXTAREA') return 'textbox'
  if (t === 'INPUT') return (el as HTMLInputElement).type === 'checkbox' ? 'checkbox' : 'textbox'
  return el.getAttribute('role') || (t === 'A' ? 'link' : 'button')
}

/** The accessibility-style tree handed to the navigator: every visible element with an agent id. */
export function observe(): TreeNode[] {
  // when a dialog is open, only its elements are reachable (as for a person)
  const dialogs = document.querySelectorAll<HTMLElement>('[role="dialog"]')
  const scope: ParentNode = dialogs.length ? dialogs[dialogs.length - 1] : document
  const out: TreeNode[] = []
  scope.querySelectorAll<HTMLElement>('[data-agent-id]').forEach(el => {
    if (!visible(el)) return
    const role = roleOf(el)
    const n: TreeNode = {
      id: el.dataset.agentId!, role,
      label: (el.getAttribute('aria-label') || el.dataset.label || (role === 'textbox' || role === 'select' ? '' : el.textContent) || '').trim().replace(/\s+/g, ' ').slice(0, 90),
    }
    if (el instanceof HTMLInputElement) n.value = el.type === 'checkbox' ? (el.checked ? 'checked' : 'unchecked') : el.value
    else if (el instanceof HTMLTextAreaElement) n.value = el.value
    else if (el instanceof HTMLSelectElement) { n.value = el.value; n.options = [...el.options].map(o => o.value).filter(Boolean) }
    else if (el.getAttribute('aria-pressed')) n.value = el.getAttribute('aria-pressed') === 'true' ? 'ticked' : 'empty'
    if ((el as HTMLButtonElement).disabled) n.disabled = true
    out.push(n)
  })
  return out
}

async function moveCursor(el: HTMLElement) {
  el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' as ScrollBehavior })
  const cur = document.getElementById('agent-cursor')
  const r = el.getBoundingClientRect()
  const dur = 380 * speed()
  if (cur) {
    cur.style.setProperty('--t', `${dur}ms`)
    cur.style.opacity = '1'
    cur.style.left = `${r.left + Math.min(r.width / 2, 60)}px`
    cur.style.top = `${r.top + r.height / 2}px`
  }
  await sleep(dur + 30)
  el.classList.add('agent-hl')
  setTimeout(() => el.classList.remove('agent-hl'), 900 * Math.max(speed(), 0.4))
}

function clickFx() {
  const cur = document.getElementById('agent-cursor')
  if (!cur) return
  cur.classList.remove('click')
  void cur.offsetWidth
  cur.classList.add('click')
}

/** Set a value the way React expects: through the native setter, then an input event. */
function setNative(el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, value: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value)
  el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
}

async function settle() {
  await idle()
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))
  await idle()
}

export async function screenshot(): Promise<string | null> {
  const node = document.getElementById('app-canvas')
  if (!node) return null
  // html-to-image clones the DOM from attributes, so a dropdown changed through its property
  // would be drawn with its old value. Mirror the live state into attributes first.
  node.querySelectorAll('select').forEach(sel => [...sel.options].forEach(o => o.toggleAttribute('selected', o.selected)))
  node.querySelectorAll<HTMLInputElement>('input[type=checkbox]').forEach(c => c.toggleAttribute('checked', c.checked))
  try {
    const url = await toPng(node, { pixelRatio: 0.6, cacheBust: false, skipFonts: true, filter: n => !(n instanceof HTMLElement && n.dataset.noShot !== undefined) })
    return url.slice(url.indexOf(',') + 1)
  } catch { return null }
}

async function doAction(a: Action): Promise<{ error?: string; expected?: string }> {
  if (a.name === 'observe') return {}
  const id = a.name === 'navigate' ? `nav-${a.args.page}` : String(a.args.agent_id ?? '')
  const el = find(id)
  if (!el || !visible(el)) {
    const dialogOpen = document.querySelector('[role="dialog"]')
    return { error: a.name === 'navigate' ? `There is no page "${a.args.page}"${dialogOpen ? '; a dialog is open, close or save it first' : ''}.` : `No element with agent_id "${id}" is on screen.` }
  }
  if (document.querySelector('[role="dialog"]') && !el.closest('[role="dialog"]')) return { error: 'A dialog is open. Finish or cancel it before using the page behind it.' }
  if ((el as HTMLButtonElement).disabled) return { error: `"${id}" is disabled.` }
  await moveCursor(el)
  if (halted) return { error: 'Stopped by the user.' }
  set({ uiError: null })
  switch (a.name) {
    case 'navigate':
    case 'click':
      clickFx()
      el.click()
      return {
        expected: a.name === 'navigate' ? `The ${a.args.page} page is open.`
          : /-save$/.test(id) ? 'The dialog has closed and the saved item is now in the page behind it. No error message is shown.'
            : /-new$|-edit-/.test(id) ? 'A dialog with input fields has opened.'
              : `The click on "${id}" took effect: a cell is ticked, a row is selected, or the page changed.`,
      }
    case 'type': {
      if (!(el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement)) return { error: `"${id}" is not a text field.` }
      const text = String(a.args.text ?? '')
      el.focus()
      if (get().fast || text.length > 60) setNative(el, text)
      else {
        setNative(el, '')
        for (let i = 1; i <= text.length; i++) {
          if (halted) return { error: 'Stopped by the user.' }
          setNative(el, text.slice(0, i))
          await sleep(14)
        }
      }
      return { expected: `The field "${id}" shows “${text}”.` }
    }
    case 'select': {
      if (!(el instanceof HTMLSelectElement)) return { error: `"${id}" is not a dropdown.` }
      const v = String(a.args.value ?? '')
      const opt = [...el.options].find(o => o.value === v) ?? [...el.options].find(o => o.textContent?.trim().toLowerCase() === v.toLowerCase())
      if (!opt) return { error: `"${id}" has no option "${v}". Options: ${[...el.options].map(o => o.value).filter(Boolean).join(', ')}` }
      clickFx()
      setNative(el, opt.value)
      return { expected: `The dropdown "${id}" shows “${opt.value}”.` }
    }
    case 'scroll':
      return {}
  }
}

export async function perform(a: Action, opts: { shot?: boolean } = {}): Promise<ActResult> {
  halted = false
  let res: { error?: string; expected?: string } = {}
  try {
    res = await doAction(a)
  } catch (e: any) {
    res = { error: String(e?.message ?? e) }
  }
  await settle()
  await sleep(60 * speed())
  const uiError = get().uiError
  const error = res.error ?? (uiError ? `The application refused: ${uiError}` : undefined)
  return {
    ok: !error, error, expected: res.expected, tree: observe(), page: get().page,
    screenshot: opts.shot === false ? null : await screenshot(),
  }
}
