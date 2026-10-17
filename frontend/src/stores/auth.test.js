import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import axios from 'axios'
import { useAuthStore } from './auth'

beforeEach(() => {
  setActivePinia(createPinia())
  axios.get.mockReset()
  axios.post.mockReset()
  vi.unstubAllGlobals()
})

describe('auth store', () => {
  it('checkStatus uebernimmt den Backend-Status und meldet Erfolg', async () => {
    axios.get.mockResolvedValue({ data: { pin_enabled: true, authenticated: false, pin_warning: false } })
    const auth = useAuthStore()
    expect(await auth.checkStatus()).toBe(true)
    expect(auth.pinEnabled).toBe(true)
    expect(auth.isAuthenticated).toBe(false)
  })

  it('checkStatus meldet einen Netzwerkfehler (Guard fragt dann erneut)', async () => {
    axios.get.mockRejectedValue(new Error('offline'))
    const auth = useAuthStore()
    expect(await auth.checkStatus()).toBe(false)
  })

  it('markSessionExpired (401) haelt den PIN-Schutz aktiv statt ihn zu "vergessen"', () => {
    const auth = useAuthStore()
    auth.isAuthenticated = true
    auth.pinEnabled = false
    auth.markSessionExpired()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.pinEnabled).toBe(true)
  })

  it('login: Erfolg bei 2xx, Backend-Meldung bei 401/429', async () => {
    const auth = useAuthStore()
    axios.post.mockResolvedValueOnce({ data: { success: true } })
    expect(await auth.login('1234')).toEqual({ success: true })
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.pinEnabled).toBe(true)

    axios.post.mockRejectedValueOnce({ response: { status: 429, data: { detail: 'Zu viele Versuche' } } })
    expect(await auth.login('0000')).toEqual({ success: false, detail: 'Zu viele Versuche' })
    expect(auth.loading).toBe(false)
  })

  it('logout beendet die Session, leert den API-Cache und bleibt im PIN-Modus', async () => {
    const del = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('caches', { delete: del })
    axios.post.mockResolvedValue({ data: { success: true } })
    const auth = useAuthStore()
    auth.pinEnabled = true
    auth.isAuthenticated = true
    await auth.logout()
    expect(axios.post).toHaveBeenCalledWith('/api/auth/logout')
    expect(del).toHaveBeenCalledWith('api-cache')
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.pinEnabled).toBe(true)
  })
})
