import { describe, it, expect, vi, afterEach } from 'vitest'
import { copyToClipboard } from './clipboard'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  delete document.execCommand
})

describe('copyToClipboard', () => {
  it('nutzt die Clipboard-API im Secure Context', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('isSecureContext', true)
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })
    expect(await copyToClipboard('hallo')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('hallo')
  })

  it('faellt ueber plain-HTTP (kein navigator.clipboard) auf execCommand zurueck statt zu werfen', async () => {
    vi.stubGlobal('isSecureContext', false)
    vi.stubGlobal('navigator', { ...navigator, clipboard: undefined })
    let copiedValue = null
    document.execCommand = vi.fn(() => {
      copiedValue = document.querySelector('textarea')?.value
      return true
    })
    expect(await copyToClipboard('Fehlerbericht')).toBe(true)
    expect(document.execCommand).toHaveBeenCalledWith('copy')
    expect(copiedValue).toBe('Fehlerbericht')
    // Hilfs-Textfeld wieder entfernt
    expect(document.querySelector('textarea')).toBeNull()
  })

  it('liefert false, wenn auch der Fallback scheitert', async () => {
    vi.stubGlobal('isSecureContext', false)
    document.execCommand = vi.fn(() => { throw new Error('nope') })
    expect(await copyToClipboard('x')).toBe(false)
  })
})
