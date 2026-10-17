import { describe, it, expect, beforeEach } from 'vitest'
import { trapTabKey } from './focusTrap'

function tab(shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key: 'Tab', shiftKey, cancelable: true })
  return event
}

let outside
let container
let first
let last

beforeEach(() => {
  document.body.innerHTML = `
    <button id="outside">draussen</button>
    <div id="dlg"><a href="/a" id="first">A</a><button disabled>aus</button><button id="last">B</button></div>`
  outside = document.getElementById('outside')
  container = document.getElementById('dlg')
  first = document.getElementById('first')
  last = document.getElementById('last')
})

describe('trapTabKey', () => {
  it('Tab auf dem letzten Element springt zum ersten', () => {
    last.focus()
    const e = tab()
    trapTabKey(e, container)
    expect(e.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(first)
  })

  it('Shift+Tab auf dem ersten Element springt zum letzten (deaktivierte uebersprungen)', () => {
    first.focus()
    const e = tab(true)
    trapTabKey(e, container)
    expect(document.activeElement).toBe(last)
  })

  it('Fokus ausserhalb wird in den Dialog zurueckgeholt', () => {
    outside.focus()
    const e = tab()
    trapTabKey(e, container)
    expect(e.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(first)
  })

  it('andere Tasten und Tab in der Mitte bleiben unberuehrt', () => {
    first.focus()
    const e = tab()
    trapTabKey(e, container)
    expect(e.defaultPrevented).toBe(false)
    const esc = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
    trapTabKey(esc, container)
    expect(esc.defaultPrevented).toBe(false)
  })
})
