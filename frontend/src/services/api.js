import axios from 'axios'
// FE-EXTRA: auth store UND router statisch importieren. Beide loesten als
// dynamischer Import in Vite 8 die INEFFECTIVE_DYNAMIC_IMPORT-Warnung aus, weil
// sie anderswo (PinWarningBanner/main.js) statisch geladen werden. Es gibt
// keinen Eval-Zyklus: der auth store nutzt rohes axios (kein api.js), und
// router/index.js laedt seine Views nur lazy (() => import(...)) — der Pfad
// api.js -> router -> views -> api.js entsteht also erst zur Laufzeit, nicht
// beim Modul-Eval.
import { useAuthStore } from '../stores/auth'
import router from '../router/index'
import { apiErrorMessage } from '../utils/apiErrors'

// Timeouts zentral: Standard fuer normale API-Aufrufe; LONG fuer synchrone
// Endpunkte, die auf dem Ziel-Hardware-Profil (7B-Modell auf CPU) laenger
// brauchen (Steuerpaket-ZIP/PDF, RAG-Chat mit optionalem Reranker). Wert =
// nginx proxy_read_timeout (frontend/nginx.conf, ssl/nginx-ssl.conf) — laenger
// zu warten waere sinnlos, nginx bricht dort ab. Re-Analyse, E-Mail-Abruf und
// Backup laufen als Hintergrund-Vorgang (202 + Polling) und brauchen keinen.
// Uploads: kein Timeout (50 MB ueber WLAN/Mobilfunk), Fortschritt per Callback.
export const DEFAULT_TIMEOUT_MS = 30000
export const LONG_OPERATION_TIMEOUT_MS = 120000
export const NO_TIMEOUT = 0

const api = axios.create({
  baseURL: '/api',
  timeout: DEFAULT_TIMEOUT_MS,
})

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      // Sitzung abgelaufen/ungueltig. Nur EINMAL zur PIN-Seite: steht der
      // Nutzer schon dort (fullPath ist dann '/pin?redirect=...'), nichts tun —
      // sonst verschachtelt jeder weitere 401 den redirect-Parameter.
      useAuthStore().markSessionExpired()
      const current = router.currentRoute.value
      if (current.name !== 'pin-login') {
        router.replace({ name: 'pin-login', query: { redirect: current.fullPath } })
      }
      return Promise.reject(error)
    }
    const message = error.response?.data?.detail || error.message || 'Ein Fehler ist aufgetreten'
    // FE-L1: API-Fehler nur im Dev-Build loggen — im Prod-Build sind sie Rauschen
    // (erwartete 404/Health-Fehler) und duplizieren die User-Toasts.
    if (import.meta.env.DEV) console.error('API-Fehler:', message)
    return Promise.reject(error)
  }
)

// === Documents ===

export async function getDocuments(params = {}) {
  const { data } = await api.get('/documents', { params })
  return data
}

export async function getDocument(id) {
  const { data } = await api.get(`/documents/${id}`)
  return data
}

export async function updateDocument(id, updates) {
  const { data } = await api.patch(`/documents/${id}`, updates)
  return data
}

export async function deleteDocument(id) {
  const { data } = await api.delete(`/documents/${id}`)
  return data
}

// === Upload ===

// Jede Datei in einem eigenen Request: nginx begrenzt den GANZEN Request-Body
// auf 50 MB (client_max_body_size) — ein Sammel-Upload mehrerer Scans scheiterte
// dort komplett mit einer 413-HTML-Seite, obwohl jede Datei einzeln erlaubt war.
// Ergebnis bleibt die MultiUploadResponse-Form { uploaded, rejected } ueber alle
// Dateien; ein Fehler bei einer Datei landet als { filename, error } in rejected.
// onProgress bekommt den Gesamtfortschritt { loaded, total } (Bytes ueber alle
// Dateien) plus { fileIndex, fileCount, filename } der gerade laufenden Datei.
// options.splitCheck: "Enthält mehrere Dokumente" — PDFs gehen vor der Analyse
// in den Aufteilungs-Editor (Formularfeld split_check).
export async function uploadDocuments(files, onProgress, options = {}) {
  const list = Array.from(files || [])
  const totalBytes = list.reduce((sum, f) => sum + (f.size || 0), 0)
  const result = { uploaded: [], rejected: [] }
  let doneBytes = 0

  for (const [index, file] of list.entries()) {
    const formData = new FormData()
    formData.append('files', file)
    if (options.splitCheck) formData.append('split_check', 'true')
    try {
      const { data } = await api.post('/documents/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: NO_TIMEOUT,
        onUploadProgress: (event) => {
          if (!onProgress) return
          const fileLoaded = Math.min(event.loaded || 0, file.size || event.loaded || 0)
          onProgress({
            loaded: doneBytes + fileLoaded,
            total: totalBytes,
            fileIndex: index,
            fileCount: list.length,
            filename: file.name,
          })
        },
      })
      result.uploaded.push(...(data?.uploaded || []))
      result.rejected.push(...(data?.rejected || []))
    } catch (error) {
      // 401 hat der Interceptor bereits behandelt (Redirect zur PIN-Seite) —
      // weitere Dateien waeren ebenfalls abgelehnt.
      if (error.response?.status === 401) throw error
      result.rejected.push({
        filename: file.name,
        error: apiErrorMessage(error, 'Upload fehlgeschlagen.'),
      })
    }
    doneBytes += file.size || 0
    if (onProgress) {
      onProgress({ loaded: doneBytes, total: totalBytes, fileIndex: index, fileCount: list.length, filename: file.name })
    }
  }
  return result
}

export async function getJobStatus(id) {
  const { data } = await api.get(`/documents/${id}/status`)
  return data
}

// === Aufteilen von Stapel-Scans (Spezifikation 12) ===

// Stapel-Liste, z.B. getSplitBatches({ status: 'PROPOSED' }) fuer "Prüfen"
export async function getSplitBatches(params = {}) {
  const { data } = await api.get('/split', { params })
  return data
}

// Automatische Pruefung (System -> Verarbeitung): { auto_enabled, min_pages, max_pages }
export async function getSplitSettings() {
  const { data } = await api.get('/system/split-settings')
  return data
}

export async function setSplitSettings(body) {
  const { data } = await api.put('/system/split-settings', body)
  return data
}

export async function getSplitBatch(batchId) {
  const { data } = await api.get(`/split/${encodeURIComponent(batchId)}`)
  return data
}

export function splitPageThumbnailUrl(batchId, page) {
  return `/api/split/${encodeURIComponent(batchId)}/pages/${page}/thumbnail`
}

export function splitPagePreviewUrl(batchId, page) {
  return `/api/split/${encodeURIComponent(batchId)}/pages/${page}/preview`
}

// body: { version, parts: [{ pages: [1, 2] }, ...], excluded: [3] } -> 202 { job_ids }
export async function confirmSplit(batchId, body) {
  const { data } = await api.post(`/split/${encodeURIComponent(batchId)}/confirm`, body)
  return data
}

export async function keepSplitWhole(batchId, version) {
  const { data } = await api.post(`/split/${encodeURIComponent(batchId)}/keep-whole`, { version })
  return data
}

export async function discardSplit(batchId, version) {
  const { data } = await api.post(`/split/${encodeURIComponent(batchId)}/discard`, { version })
  return data
}

// "Aufteilung rückgängig" -> { status, removed_document_ids, restored_document_id }
// 409 mit Begruendung, wenn ein Teil schon bearbeitet wurde.
export async function undoSplit(batchId, version) {
  const { data } = await api.post(`/split/${encodeURIComponent(batchId)}/undo`, { version })
  return data
}

export async function retryFailedSplitParts(batchId) {
  const { data } = await api.post(`/split/${encodeURIComponent(batchId)}/retry-failed`)
  return data
}

// "Dokument aufteilen" -> 202 { batch_id, job_id }
export async function requestDocumentSplit(documentId) {
  const { data } = await api.post(`/documents/${encodeURIComponent(documentId)}/split`)
  return data
}

// === Tags ===

export async function addTag(documentId, name) {
  const { data } = await api.post(`/documents/${documentId}/tags`, { name })
  return data
}

// Per Tag-ID statt Name: Namen sind Freitext ('strom/gas', 'Test #1') und
// zerlegten den URL-Pfad ('/' -> 404, '#'/'?' -> falscher Tag "entfernt").
export async function removeTag(documentId, tagId) {
  const { data } = await api.delete(
    `/documents/${encodeURIComponent(documentId)}/tags/${encodeURIComponent(tagId)}`,
  )
  return data
}

// === Review ===

export async function getReviewDocuments() {
  const { data } = await api.get('/review/pending')
  return data.documents
}

export async function getReviewDetail(documentId) {
  const { data } = await api.get(`/review/documents/${documentId}`)
  return data
}

export async function answerReviewQuestion(questionId, answer) {
  const { data } = await api.post(`/review/questions/${questionId}/answer`, { answer })
  return data
}

// "Bestätigen"/"Freigeben": Antworten bestätigen (auch mit offenen Rückfragen erlaubt)
export async function approveReview(documentId) {
  const { data } = await api.post(`/review/documents/${documentId}/approve`)
  return data
}

// "So übernehmen": das KI-Ergebnis unverändert als geprüft akzeptieren
export async function acceptReview(documentId) {
  const { data } = await api.post(`/review/documents/${documentId}/accept`)
  return data
}

// Badge "Zu prüfen" (composables/useReviewCount.js): { open_documents, ... }
export async function getReviewStats() {
  const { data } = await api.get('/review/stats')
  return data
}

// === Stats ===

export async function getDashboardStats() {
  const { data } = await api.get('/stats')
  return data
}

// === Search ===

export async function searchDocuments(params = {}) {
  const { data } = await api.get('/search', { params })
  return data
}

export async function searchSuggest(query) {
  const { data } = await api.get('/search/suggest', { params: { q: query } })
  return data.suggestions
}

// === Saved Searches ===

export async function getSavedSearches() {
  const { data } = await api.get('/saved-searches')
  return data
}

export async function createSavedSearch(name, queryParams) {
  const { data } = await api.post('/saved-searches', { name, query_params: queryParams })
  return data
}

export async function deleteSavedSearch(id) {
  const { data } = await api.delete(`/saved-searches/${id}`)
  return data
}

// === Tax ===

export async function getTaxSummary(year, params = {}) {
  const { data } = await api.get(`/tax/summary/${year}`, { params })
  return data
}

export async function getTaxYears() {
  const { data } = await api.get('/tax/years')
  return data
}

export async function getTaxValidation(year, params = {}) {
  const { data } = await api.get(`/tax/validate/${year}`, { params })
  return data
}

export async function exportTaxPackage(year, filingScopeId = null) {
  const body = { year }
  if (filingScopeId) body.filing_scope_id = filingScopeId
  let response
  try {
    response = await api.post('/tax/export', body, {
      responseType: 'blob',
      timeout: LONG_OPERATION_TIMEOUT_MS,
    })
  } catch (error) {
    // Bei responseType 'blob' kommt auch die Fehler-JSON als Blob — fuer
    // apiErrorMessage() in ein normales Objekt zurueckwandeln.
    const blob = error.response?.data
    if (blob instanceof Blob && blob.type.includes('json')) {
      try { error.response.data = JSON.parse(await blob.text()) } catch { /* bleibt Blob */ }
    }
    throw error
  }
  // Browser-Download ausloesen
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement('a')
  link.href = url
  link.setAttribute('download', `Steuerpaket_${year}.zip`)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

// === Warranties ===

export async function getWarranties(params = {}) {
  const { data } = await api.get('/warranties', { params })
  return data
}

export async function getWarrantyStats() {
  const { data } = await api.get('/warranties/stats')
  return data
}

// === Notifications ===

export async function getNotifications(unreadOnly = false) {
  const { data } = await api.get('/notifications', { params: { unread_only: unreadOnly } })
  return data
}

export async function getNotificationCount() {
  const { data } = await api.get('/notifications/count')
  return data.unread
}

export async function markNotificationRead(id) {
  const { data } = await api.post(`/notifications/${id}/read`)
  return data
}

export async function markAllNotificationsRead() {
  const { data } = await api.post('/notifications/read-all')
  return data
}

// === System ===

export async function getSystemHealth() {
  const { data } = await api.get('/system/health')
  return data
}

export async function getUpdateCheck(force = false) {
  const { data } = await api.get('/system/update-check', { params: { force } })
  return data
}

export async function setUpdateCheckEnabled(enabled) {
  const { data } = await api.put('/system/update-check', { enabled })
  return data
}

export async function createBackup(full = false) {
  const { data } = await api.post('/system/backup', null, { params: { full } })
  return data
}

export async function getBackups() {
  const { data } = await api.get('/system/backups')
  return data
}

// Download-Link (<a href>) fuer ein Backup — Dateiname kodiert, damit Sonder-
// zeichen den Pfad nicht zerlegen. Der Service-Worker cached diesen Pfad nie.
export function backupDownloadUrl(filename) {
  return `/api/system/backups/${encodeURIComponent(filename)}`
}

export async function getSystemSettings() {
  const { data } = await api.get('/system/settings')
  return data
}

// Ordner-Einstellungen speichern/pruefen: mit Netzwerkordnern verbindet sich
// das Backend erst (Watch-Task stoppen, Verbindungsprobe bis 20 s, Namens-
// aufloesung) — das dauert laenger als DEFAULT_TIMEOUT_MS. Sonst bricht die UI
// ab, waehrend das Backend noch erfolgreich speichert, und ein zweiter Klick
// startet einen ueberlappenden Speichervorgang (R8).
export const FOLDER_SETTINGS_TIMEOUT_MS = LONG_OPERATION_TIMEOUT_MS

export async function updateSystemSettings(settings) {
  const { data } = await api.put('/system/settings', settings, { timeout: FOLDER_SETTINGS_TIMEOUT_MS })
  return data
}

// Ordner pruefen, ohne zu speichern (Laufwerksbuchstabe -> UNC-Vorschlag,
// Netzwerkpfad -> Verbindungstest mit Zugangsdaten). Antwort: {ok, kind, message, suggested_paths}.
export async function testFolderSettings(body) {
  const { data } = await api.post('/system/settings/test-folder', body, { timeout: FOLDER_SETTINGS_TIMEOUT_MS })
  return data
}

export async function optimizeDb() {
  const { data } = await api.post('/system/maintenance/optimize-db')
  return data
}

export async function rebuildIndex() {
  const { data } = await api.post('/system/maintenance/rebuild-index')
  return data
}

// === Filing Scopes ===

export async function getFilingScopes() {
  const { data } = await api.get('/filing-scopes')
  return data
}

export async function createFilingScope(scope) {
  const { data } = await api.post('/filing-scopes', scope)
  return data
}

export async function updateFilingScope(id, updates) {
  const { data } = await api.patch(`/filing-scopes/${id}`, updates)
  return data
}

export async function deleteFilingScope(id) {
  const { data } = await api.delete(`/filing-scopes/${id}`)
  return data
}

// === Chat (KI-Assistent) ===

export async function askQuestion(question, filingScopeId = null, signal = null) {
  const body = { question }
  if (filingScopeId) body.filing_scope_id = filingScopeId
  const { data } = await api.post('/chat', body, { timeout: LONG_OPERATION_TIMEOUT_MS, signal })
  return data
}

export async function getChatHistory(limit = 50, offset = 0) {
  const { data } = await api.get('/chat/history', { params: { limit, offset } })
  return data
}

export async function clearChatHistory() {
  const { data } = await api.delete('/chat/history')
  return data
}

// Hintergrund-Vorgang (202 + Task, kind "rebuild_vectors"); Status ueber getTask/listTasks.
export async function rebuildVectors() {
  const { data } = await api.post('/system/maintenance/rebuild-vectors')
  return data
}

// === Health ===

export async function checkHealth() {
  try {
    const { data } = await api.get('/health')
    return data
  } catch {
    return null
  }
}

// === Jobs ===

export async function getJobs(params = {}) {
  const { data } = await api.get('/jobs', { params })
  return data
}

export async function getQueueStatus() {
  const { data } = await api.get('/jobs/queue-status')
  return data
}

export async function pauseQueue() {
  const { data } = await api.post('/jobs/pause')
  return data
}

export async function resumeQueue() {
  const { data } = await api.post('/jobs/resume')
  return data
}

export async function retryJob(jobId) {
  const { data } = await api.post(`/jobs/${jobId}/retry`)
  return data
}

// Fehlgeschlagenen Job verwerfen (nur FAILED): entfernt Eintrag + behaltene Upload-Datei.
export async function deleteJob(jobId) {
  await api.delete(`/jobs/${jobId}`)
}

// Neu-Analyse als Hintergrund-Vorgang (202 + Task). Legt nur einen Vorschlag an;
// withOcr erkennt den Text aus der Datei neu.
export async function reanalyzeDocument(documentId, { withOcr = false } = {}) {
  const { data } = await api.post(`/review/documents/${documentId}/reanalyze`, { with_ocr: withOcr })
  return data
}

// === Neu-Analyse: Vorschlaege und Massenlauf ===

export async function getProposal(documentId) {
  const { data } = await api.get(`/reanalysis/proposals/${documentId}`)
  return data
}

// fields = null uebernimmt alle Aenderungen; liefert das aktualisierte Dokument.
export async function applyProposal(documentId, fields = null) {
  const { data } = await api.post(`/reanalysis/proposals/${documentId}/apply`, { fields })
  return data
}

export async function discardProposal(documentId) {
  await api.post(`/reanalysis/proposals/${documentId}/discard`)
}

export async function getProposals(params = {}) {
  const { data } = await api.get('/reanalysis/proposals', { params })
  return data
}

// Sammel-Uebernahme laeuft als Hintergrund-Vorgang (202 + Task).
export async function applyAllProposals(field = null) {
  const { data } = await api.post('/reanalysis/proposals/apply-all', { field })
  return data
}

export async function discardAllProposals(field = null) {
  const { data } = await api.post('/reanalysis/proposals/discard-all', { field })
  return data
}

export async function previewReanalysisRun(body) {
  const { data } = await api.post('/reanalysis/runs/preview', body)
  return data
}

export async function startReanalysisRun(body) {
  const { data } = await api.post('/reanalysis/runs', body)
  return data
}

export async function getCurrentReanalysisRun() {
  const { data } = await api.get('/reanalysis/runs/current')
  return data
}

// action: 'pause' | 'resume' | 'cancel'
export async function setReanalysisRunStatus(runId, action) {
  const { data } = await api.post(`/reanalysis/runs/${runId}/${action}`)
  return data
}

// === E-Mail-Konten ===

export async function getEmailAccounts() {
  const { data } = await api.get('/email/accounts')
  return data
}

export async function createEmailAccount(account) {
  const { data } = await api.post('/email/accounts', account)
  return data
}

export async function updateEmailAccount(id, updates) {
  const { data } = await api.put(`/email/accounts/${id}`, updates)
  return data
}

export async function deleteEmailAccount(id) {
  await api.delete(`/email/accounts/${id}`)
}

export async function testEmailConnection(id) {
  const { data } = await api.post(`/email/accounts/${id}/test`)
  return data
}

export async function fetchEmailsNow(id) {
  const { data } = await api.post(`/email/accounts/${id}/fetch`)
  return data
}

export async function getEmailHistory(id, params = {}) {
  const { data } = await api.get(`/email/accounts/${id}/history`, { params })
  return data
}

export async function getEmailStats() {
  const { data } = await api.get('/email/stats')
  return data
}

// Feste Abruf-Regeln fuer die UI-Hinweise: { timezone, unseen_only, max_per_run, idle_interval_minutes }
export async function getEmailSettings() {
  const { data } = await api.get('/email/settings')
  return data
}

// === Hintergrund-Vorgaenge (Re-Analyse, E-Mail-Abruf, Backup) ===
// Polling bitte ueber composables/useBackgroundTask.js, nicht direkt.

export async function getTask(id) {
  const { data } = await api.get(`/tasks/${id}`)
  return data
}

// params: { kind, key, active, limit } — z.B. beim Mount laufende Vorgaenge finden.
export async function listTasks(params = {}) {
  const { data } = await api.get('/tasks', { params })
  return data
}

export default api
