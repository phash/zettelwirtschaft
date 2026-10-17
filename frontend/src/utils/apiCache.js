// Name des Workbox-Runtime-Caches fuer API-JSON (vite.config.js, NetworkFirst-Regel).
export const API_CACHE_NAME = 'api-cache'

// Beim Abmelden die vom Service-Worker zwischengespeicherten API-Antworten
// (OCR-Texte, Chat-Verlauf, Dokumentlisten ...) aus Cache Storage entfernen,
// damit auf einem geteilten Geraet nach dem Logout nichts davon zurueckbleibt.
// Best effort: ohne Service-Worker/Cache-API (plain HTTP, privater Modus) no-op.
export async function clearApiCache() {
  try {
    if (typeof caches !== 'undefined') await caches.delete(API_CACHE_NAME)
  } catch {
    // ignore
  }
}
