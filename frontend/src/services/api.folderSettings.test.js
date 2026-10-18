import { describe, it, expect, vi } from 'vitest'

// api.js zieht Router und Auth-Store — fuer diesen Test nicht noetig.
vi.mock('../stores/auth', () => ({ useAuthStore: () => ({ markSessionExpired: vi.fn() }) }))
vi.mock('../router/index', () => ({ default: { currentRoute: { value: {} }, replace: vi.fn() } }))

import api, {
  DEFAULT_TIMEOUT_MS,
  FOLDER_SETTINGS_TIMEOUT_MS,
  testFolderSettings,
  updateSystemSettings,
} from './api'

describe('Ordner-Einstellungen (R8)', () => {
  it('Speichern und Verbindungstest warten laenger als das Backend fuer Netzwerkordner braucht', async () => {
    const put = vi.spyOn(api, 'put').mockResolvedValue({ data: { ok: true } })
    const post = vi.spyOn(api, 'post').mockResolvedValue({ data: { ok: true } })
    await updateSystemSettings({ watch_dir: '' })
    await testFolderSettings({ target: 'watch', path: 'D:/Scans' })
    expect(put.mock.calls[0][2].timeout).toBe(FOLDER_SETTINGS_TIMEOUT_MS)
    expect(post.mock.calls[0][2].timeout).toBe(FOLDER_SETTINGS_TIMEOUT_MS)
    // Backend-Budget: Watch-Task stoppen (Observer 10 s + Importe 30 s) +
    // parallele Verbindungsproben (20 s) + Namensaufloesung (15 s) = 75 s.
    expect(FOLDER_SETTINGS_TIMEOUT_MS).toBeGreaterThan(75000)
    expect(FOLDER_SETTINGS_TIMEOUT_MS).toBeGreaterThan(DEFAULT_TIMEOUT_MS)
  })
})
