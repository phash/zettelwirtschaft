import { describe, it, expect, vi, beforeEach } from 'vitest'

// Modul-Level-Cache -> pro Test frisch importieren.
const getFilingScopes = vi.fn()
vi.mock('../services/api', () => ({ getFilingScopes: (...args) => getFilingScopes(...args) }))

async function load() {
  vi.resetModules()
  const mod = await import('./useFilingScopes')
  return mod.useFilingScopes()
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const A = [{ id: 'a', name: 'Privat' }]
const B = [{ id: 'a', name: 'Privat' }, { id: 'b', name: 'Praxis' }]

beforeEach(() => {
  getFilingScopes.mockReset()
})

describe('useFilingScopes', () => {
  it('laedt einmal und teilt den Cache', async () => {
    getFilingScopes.mockResolvedValue(A)
    const s = await load()
    await s.ensureLoaded()
    await s.ensureLoaded()
    expect(getFilingScopes).toHaveBeenCalledTimes(1)
    expect(s.scopes.value).toEqual(A)
  })

  it('ensureLoaded wirft bei Fehler nicht, setzt aber error und retried beim naechsten Aufruf', async () => {
    getFilingScopes.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(A)
    const s = await load()
    expect(await s.ensureLoaded()).toEqual([])
    expect(s.error.value).toBeInstanceOf(Error)
    expect(s.loaded.value).toBe(false)
    expect(await s.ensureLoaded()).toEqual(A)
    expect(s.error.value).toBeNull()
  })

  it('ensureLoaded wirft auch nicht, wenn ein gleichzeitig laufender Request scheitert', async () => {
    const pending = deferred()
    getFilingScopes.mockReturnValueOnce(pending.promise)
    const s = await load()
    const first = s.ensureLoaded()
    const second = s.ensureLoaded() // haengt sich an den laufenden Request
    expect(getFilingScopes).toHaveBeenCalledTimes(1)
    pending.reject(new Error('offline'))
    expect(await first).toEqual([])
    expect(await second).toEqual([])
    expect(s.error.value).toBeInstanceOf(Error)
  })

  it('reload() behaelt die alte Liste bis die neue da ist und wirft bei Fehler', async () => {
    getFilingScopes.mockResolvedValueOnce(A)
    const s = await load()
    await s.ensureLoaded()

    const pending = deferred()
    getFilingScopes.mockReturnValueOnce(pending.promise)
    const reloading = s.reload()
    expect(s.scopes.value).toEqual(A) // nicht geleert
    pending.resolve(B)
    expect(await reloading).toEqual(B)
    expect(s.scopes.value).toEqual(B)

    getFilingScopes.mockRejectedValueOnce(new Error('500'))
    await expect(s.reload()).rejects.toThrow('500')
    expect(s.scopes.value).toEqual(B)
  })

  it('invalidate() leert die Liste nicht und ein ueberholter Request schreibt nicht', async () => {
    getFilingScopes.mockResolvedValueOnce(A)
    const s = await load()
    await s.ensureLoaded()

    const stale = deferred()
    getFilingScopes.mockReturnValueOnce(stale.promise)
    const staleCall = s.ensureLoaded(true)
    s.invalidate()
    expect(s.scopes.value).toEqual(A)

    getFilingScopes.mockResolvedValueOnce(B)
    await s.ensureLoaded()
    stale.resolve([{ id: 'old', name: 'Veraltet' }])
    await staleCall
    expect(s.scopes.value).toEqual(B)
  })

  it('forceReload startet trotz laufendem Request einen neuen, juengster gewinnt', async () => {
    const first = deferred()
    getFilingScopes.mockReturnValueOnce(first.promise).mockResolvedValueOnce(B)
    const s = await load()
    const p1 = s.ensureLoaded()
    const p2 = s.ensureLoaded(true)
    expect(getFilingScopes).toHaveBeenCalledTimes(2)
    expect(await p2).toEqual(B)
    first.resolve(A)
    // Auch der Aufrufer des alten Requests bekommt die frischen Daten.
    expect(await p1).toEqual(B)
    expect(s.scopes.value).toEqual(B)
  })
})
