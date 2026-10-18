<script setup>
import { ref, onUnmounted } from 'vue'
import { uploadDocuments, getJobStatus } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { reportUploadResult } from '../utils/uploadResult'
import { FILE_ACCEPT, FILE_TYPES_LABEL, MAX_UPLOAD_SIZE_MB } from '../constants/fileTypes'

const notify = useNotificationStore()
const isDragging = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadedJobs = ref([])
const fileInput = ref(null)
// "Enthält mehrere Dokumente": PDFs vor der Analyse in den Aufteilungs-Editor.
const splitCheck = ref(false)

// Endzustaende eines Jobs — danach wird nicht mehr gepollt. NOT_FOUND ist ein
// reiner Frontend-Status (Job per 404 verschwunden, z.B. verworfen).
// AWAITING_SPLIT wartet auf den Benutzer (Aufteilungs-Editor), SPLIT ist fertig.
const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'NEEDS_REVIEW', 'NOT_FOUND', 'AWAITING_SPLIT', 'SPLIT']

async function handleFiles(files) {
  if (!files.length) return

  uploading.value = true
  uploadProgress.value = 0

  try {
    const result = await uploadDocuments(files, (event) => {
      if (event.total) {
        uploadProgress.value = Math.round((event.loaded / event.total) * 100)
      }
    }, { splitCheck: splitCheck.value })

    const { uploaded } = reportUploadResult(result, notify)
    // UploadResponse liefert die ID des ProcessingJobs (job_id) — NICHT die des
    // spaeteren Dokuments. Die Dokument-ID kommt erst ueber den Job-Status.
    for (const item of uploaded) {
      uploadedJobs.value.unshift({
        jobId: item.job_id,
        documentId: null,
        splitBatchId: null,
        splitProgress: null,
        filename: item.original_filename,
        status: item.status,
      })
    }
    if (uploaded.length) startPolling()
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
let pollRunning = false

async function pollJobs() {
  if (pollRunning) return  // langsame Antworten: keine ueberlappenden Runden
  pollRunning = true
  try {
    let allDone = true
    for (const job of uploadedJobs.value) {
      if (TERMINAL_STATUSES.includes(job.status)) continue
      try {
        const status = await getJobStatus(job.jobId)
        job.status = status.status
        job.documentId = status.document_id || null
        job.splitBatchId = status.split_batch_id || null
        job.splitProgress = status.split_progress || null
      } catch (err) {
        if (err?.response?.status === 404) {
          job.status = 'NOT_FOUND'
        }
      }
      if (!TERMINAL_STATUSES.includes(job.status)) {
        allDone = false
      }
    }
    if (allDone) stopPolling()
  } finally {
    pollRunning = false
  }
}

function startPolling() {
  if (pollInterval) return
  pollInterval = setInterval(pollJobs, 3000)
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval)
    pollInterval = null
  }
}

const statusLabels = {
  PENDING: 'Wartet...',
  PROCESSING: 'Wird verarbeitet...',
  COMPLETED: 'Fertig',
  FAILED: 'Fehlgeschlagen',
  NEEDS_REVIEW: 'Prüfung nötig',
  AWAITING_SPLIT: 'Wartet auf Aufteilung',
  SPLIT: 'Aufgeteilt',
  NOT_FOUND: 'Nicht gefunden',
}

const statusColors = {
  PENDING: 'bg-gray-100 text-gray-600',
  PROCESSING: 'bg-blue-100 text-blue-700',
  COMPLETED: 'bg-green-100 text-green-700',
  FAILED: 'bg-red-100 text-red-700',
  NEEDS_REVIEW: 'bg-orange-100 text-orange-700',
  AWAITING_SPLIT: 'bg-orange-100 text-orange-700',
  SPLIT: 'bg-green-100 text-green-700',
  NOT_FOUND: 'bg-red-100 text-red-700',
}

function statusLabel(job) {
  if (job.status === 'PROCESSING' && job.splitProgress) {
    const { pages_done: done, page_count: count } = job.splitProgress
    return `Seite ${Math.min(done + 1, count)} von ${count} wird erkannt …`
  }
  return statusLabels[job.status] || job.status
}

onUnmounted(stopPolling)
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
      <svg class="mx-auto h-16 w-16 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
      </svg>
      <p class="mt-4 text-lg font-medium text-gray-600">
        Dateien hierher ziehen
      </p>
      <p class="mt-1 text-sm text-gray-400">
        oder klicken zum Auswählen
      </p>
      <p class="mt-4 text-xs text-gray-400">
        {{ FILE_TYPES_LABEL }} &middot; Max. {{ MAX_UPLOAD_SIZE_MB }} MB
      </p>
      <input
        ref="fileInput"
        type="file"
        multiple
        :accept="FILE_ACCEPT"
        class="hidden"
        aria-label="Dateien zum Hochladen auswählen"
        @change="handleSelect"
      />
    </div>

    <label class="flex items-start gap-3 text-sm text-gray-700">
      <input v-model="splitCheck" type="checkbox" class="mt-0.5 rounded border-gray-300" />
      <span>
        Enthält mehrere Dokumente – vor der Analyse aufteilen
        <span class="block text-xs text-gray-500">Für Stapel-Scans (PDF): Du legst die Schnitte selbst fest, danach wird jeder Teil einzeln archiviert.</span>
      </span>
    </label>

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
        <div
          v-for="job in uploadedJobs"
          :key="job.jobId"
          class="flex flex-wrap items-center justify-between gap-2 py-3"
          data-testid="upload-job"
        >
          <div class="flex min-w-0 items-center gap-3">
            <div v-if="['PENDING', 'PROCESSING'].includes(job.status)" class="h-5 w-5 flex-shrink-0 animate-spin rounded-full border-2 border-primary-200 border-t-primary-600"></div>
            <svg v-else-if="['COMPLETED', 'SPLIT'].includes(job.status)" class="h-5 w-5 flex-shrink-0 text-green-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd" />
            </svg>
            <svg v-else-if="['NEEDS_REVIEW', 'AWAITING_SPLIT'].includes(job.status)" class="h-5 w-5 flex-shrink-0 text-orange-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd" />
            </svg>
            <svg v-else class="h-5 w-5 flex-shrink-0 text-red-500" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd" />
            </svg>
            <span class="truncate text-sm font-medium text-gray-700">{{ job.filename }}</span>
          </div>
          <div class="flex items-center gap-3">
            <span :class="['badge', statusColors[job.status]]">
              {{ statusLabel(job) }}
            </span>
            <router-link
              v-if="job.splitBatchId && ['AWAITING_SPLIT', 'SPLIT'].includes(job.status)"
              :to="`/aufteilen/${job.splitBatchId}`"
              class="text-sm text-primary-600 hover:text-primary-700"
            >
              {{ job.status === 'AWAITING_SPLIT' ? 'Aufteilen' : 'Anzeigen' }}
            </router-link>
            <router-link
              v-if="job.documentId && ['COMPLETED', 'NEEDS_REVIEW'].includes(job.status)"
              :to="`/dokumente/${job.documentId}`"
              class="text-sm text-primary-600 hover:text-primary-700"
            >
              {{ job.status === 'NEEDS_REVIEW' ? 'Prüfen' : 'Anzeigen' }}
            </router-link>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
