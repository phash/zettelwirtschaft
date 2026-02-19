import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../services/api'

export const useDocumentStore = defineStore('documents', () => {
  const documents = ref([])
  const currentDocument = ref(null)
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(25)
  const loading = ref(false)
  const stats = ref(null)

  async function fetchDocuments(params = {}) {
    loading.value = true
    try {
      const data = await api.getDocuments({
        page: page.value,
        page_size: pageSize.value,
        ...params,
      })
      documents.value = data.items
      total.value = data.total
    } finally {
      loading.value = false
    }
  }

  async function fetchDocument(id) {
    loading.value = true
    try {
      currentDocument.value = await api.getDocument(id)
    } finally {
      loading.value = false
    }
  }

  async function fetchStats() {
    stats.value = await api.getDashboardStats()
  }

  return {
    documents,
    currentDocument,
    total,
    page,
    pageSize,
    loading,
    stats,
    fetchDocuments,
    fetchDocument,
    fetchStats,
  }
})
