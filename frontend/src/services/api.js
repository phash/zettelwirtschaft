import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      const { useAuthStore } = await import('../stores/auth')
      const auth = useAuthStore()
      auth.reset()
      const { default: router } = await import('../router/index')
      const currentPath = router.currentRoute.value.fullPath
      if (currentPath !== '/pin') {
        router.replace({ name: 'pin-login', query: { redirect: currentPath } })
      }
      return Promise.reject(error)
    }
    const message = error.response?.data?.detail || error.message || 'Ein Fehler ist aufgetreten'
    console.error('API-Fehler:', message)
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

export async function uploadDocuments(files, onProgress) {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }
  const { data } = await api.post('/documents/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: onProgress,
  })
  return data
}

export async function getJobStatus(id) {
  const { data } = await api.get(`/documents/${id}/status`)
  return data
}

// === Tags ===

export async function getTags() {
  const { data } = await api.get('/tags')
  return data
}

export async function addTag(documentId, name) {
  const { data } = await api.post(`/documents/${documentId}/tags`, { name })
  return data
}

export async function removeTag(documentId, tagName) {
  const { data } = await api.delete(`/documents/${documentId}/tags/${tagName}`)
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

export async function answerReviewQuestion(documentId, questionId, answer) {
  const { data } = await api.post(`/review/questions/${questionId}/answer`, { answer })
  return data
}

export async function approveReview(documentId) {
  const { data } = await api.post(`/review/documents/${documentId}/approve`)
  return data
}

export async function skipReview(documentId) {
  const { data } = await api.post(`/review/documents/${documentId}/skip`)
  return data
}

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
  const response = await api.post('/tax/export', body, { responseType: 'blob' })
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

export async function updateWarranty(id, updates) {
  const { data } = await api.patch(`/warranties/${id}`, updates)
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

export async function createBackup(full = false) {
  const { data } = await api.post('/system/backup', null, { params: { full } })
  return data
}

export async function getBackups() {
  const { data } = await api.get('/system/backups')
  return data
}

export async function getSystemSettings() {
  const { data } = await api.get('/system/settings')
  return data
}

export async function updateSystemSettings(settings) {
  const { data } = await api.put('/system/settings', settings)
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

export async function askQuestion(question, filingScopeId = null) {
  const body = { question }
  if (filingScopeId) body.filing_scope_id = filingScopeId
  const { data } = await api.post('/chat', body, { timeout: 60000 })
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

export async function rebuildVectors() {
  const { data } = await api.post('/system/maintenance/rebuild-vectors', null, { timeout: 300000 })
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

export async function reanalyzeDocument(documentId) {
  const { data } = await api.post(`/review/documents/${documentId}/reanalyze`)
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

export default api
