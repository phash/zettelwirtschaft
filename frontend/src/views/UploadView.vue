<script setup>
import { ref } from 'vue'
import { uploadDocuments, getJobStatus } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

const notify = useNotificationStore()
const isDragging = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadedJobs = ref([])
const fileInput = ref(null)

async function handleFiles(files) {
  if (!files.length) return

  uploading.value = true
  uploadProgress.value = 0

  try {
    const result = await uploadDocuments(files, (event) => {
      if (event.total) {
        uploadProgress.value = Math.round((event.loaded / event.total) * 100)
      }
    })

    if (result.uploaded?.length) {
      for (const item of result.uploaded) {
        uploadedJobs.value.unshift({
          id: item.document_id,
          filename: item.original_filename,
          status: item.status,
          statusLabel: 'Hochgeladen',
        })
      }
      notify.success(`${result.uploaded.length} Datei(en) hochgeladen.`)
      startPolling()
    }

    if (result.rejected?.length) {
      for (const r of result.rejected) {
        notify.error(`${r.filename}: ${r.error}`)
      }
    }
  } catch {
    notify.error('Upload fehlgeschlagen.')
  } finally {
    uploading.value = false
    uploadProgress.value = 0
  }
}

function handleDrop(e) {
  isDragging.value = false
  handleFiles(Array.from(e.dataTransfer.files))
}

function handleSelect(e) {
  handleFiles(Array.from(e.target.files))
  e.target.value = ''
}

let pollInterval = null

function startPolling() {
  if (pollInterval) return
  pollInterval = setInterval(async () => {
    let allDone = true
    for (const job of uploadedJobs.value) {
      if (['COMPLETED', 'FAILED', 'NEEDS_REVIEW'].includes(job.status)) continue
      try {
        const status = await getJobStatus(job.id)
        job.status = status.status
        job.statusLabel = statusLabels[status.status] || status.status
      } catch {
        // ignore polling errors
      }
      if (!['COMPLETED', 'FAILED', 'NEEDS_REVIEW'].includes(job.status)) {
        allDone = false
      }
    }
    if (allDone) {
      clearInterval(pollInterval)
      pollInterval = null
    }
  }, 3000)
}

const statusLabels = {
  PENDING: 'Wartet...',
  PROCESSING: 'Wird verarbeitet...',
  COMPLETED: 'Fertig',
  FAILED: 'Fehlgeschlagen',
  NEEDS_REVIEW: 'Pruefung noetig',
}

const statusColors = {
  PENDING: 'bg-gray-100 text-gray-600',
  PROCESSING: 'bg-blue-100 text-blue-700',
  COMPLETED: 'bg-green-100 text-green-700',
  FAILED: 'bg-red-100 text-red-700',
  NEEDS_REVIEW: 'bg-orange-100 text-orange-700',
}
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-2xl font-bold text-gray-900">Dokument hochladen</h1>

    <!-- Drop zone -->
    <div
      :class="[
        'card border-2 border-dashed text-center transition-all cursor-pointer py-16',
        isDragging ? 'border-primary-400 bg-primary-50 scale-[1.01]' : 'border-gray-300 hover:border-gray-400',
        uploading ? 'opacity-50 pointer-events-none' : '',
      ]"
      @dragover.prevent="isDragging = true"
      @dragleave="isDragging = false"
      @drop.prevent="handleDrop"
      @click="fileInput?.click()"
    >
      <svg class="mx-auto h-16 w-16 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
      </svg>
      <p class="mt-4 text-lg font-medium text-gray-600">
        Dateien hierher ziehen
      </p>
      <p class="mt-1 text-sm text-gray-400">
        oder klicken zum Auswaehlen
      </p>
      <p class="mt-4 text-xs text-gray-400">
        PDF, JPG, JPEG, PNG, TIFF, BMP &middot; Max. 50 MB
      </p>
      <input
        ref="fileInput"
        type="file"
        multiple
        accept=".pdf,.jpg,.jpeg,.png,.tiff,.tif,.bmp"
        class="hidden"
        @change="handleSelect"
      />
    </div>

    <!-- Upload progress -->
    <div v-if="uploading" class="card">
      <div class="flex items-center gap-3">
        <div class="h-6 w-6 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600"></div>
        <span class="text-sm text-gray-600">Wird hochgeladen... {{ uploadProgress }}%</span>
      </div>
      <div class="mt-2 h-2 w-full rounded-full bg-gray-200">
        <div class="h-2 rounded-full bg-primary-600 transition-all" :style="{ width: uploadProgress + '%' }"></div>
      </div>
    </div>

    <!-- Uploaded files list -->
    <div v-if="uploadedJobs.length > 0" class="card">
      <h2 class="text-lg font-semibold text-gray-900 mb-4">Hochgeladene Dateien</h2>
      <div class="divide-y divide-gray-100">
        <div v-for="job in uploadedJobs" :key="job.id" class="flex items-center justify-between py-3">
          <div class="flex items-center gap-3">
            <div v-if="['PENDING', 'PROCESSING'].includes(job.status)" class="h-5 w-5 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600"></div>
            <svg v-else-if="job.status === 'COMPLETED'" class="h-5 w-5 text-green-500" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
            </svg>
            <svg v-else-if="job.status === 'FAILED'" class="h-5 w-5 text-red-500" viewBox="0 0 20 20" fill="currentColor">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
            </svg>
            <span class="text-sm font-medium text-gray-700">{{ job.filename }}</span>
          </div>
          <div class="flex items-center gap-3">
            <span :class="['badge', statusColors[job.status]]">
              {{ statusLabels[job.status] || job.status }}
            </span>
            <router-link
              v-if="job.status === 'COMPLETED'"
              :to="`/dokumente/${job.id}`"
              class="text-sm text-primary-600 hover:text-primary-700"
            >
              Anzeigen
            </router-link>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
