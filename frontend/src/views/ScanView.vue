<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { uploadDocuments } from '../services/api'
import { useNotificationStore } from '../stores/notifications'

const router = useRouter()
const notify = useNotificationStore()

const cameraActive = ref(false)
const videoRef = ref(null)
const canvasRef = ref(null)
const stream = ref(null)
const captures = ref([])
const uploading = ref(false)
const uploadProgress = ref(0)
const facingMode = ref('environment')
const cameraError = ref('')

async function startCamera() {
  cameraError.value = ''
  try {
    const constraints = {
      video: {
        facingMode: facingMode.value,
        width: { ideal: 2000 },
        height: { ideal: 2000 },
      },
    }
    stream.value = await navigator.mediaDevices.getUserMedia(constraints)
    cameraActive.value = true
    await nextTick()
    if (videoRef.value) {
      videoRef.value.srcObject = stream.value
    }
  } catch (err) {
    cameraError.value = 'Kamerazugriff nicht moeglich. Bitte Berechtigung erteilen.'
    console.error('Camera error:', err)
  }
}

function stopCamera() {
  if (stream.value) {
    stream.value.getTracks().forEach(t => t.stop())
    stream.value = null
  }
  cameraActive.value = false
}

function switchCamera() {
  facingMode.value = facingMode.value === 'environment' ? 'user' : 'environment'
  stopCamera()
  startCamera()
}

function capturePhoto() {
  if (!videoRef.value || !canvasRef.value) return

  const video = videoRef.value
  const canvas = canvasRef.value
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  canvas.getContext('2d').drawImage(video, 0, 0)

  canvas.toBlob(
    (blob) => {
      if (blob) {
        const url = URL.createObjectURL(blob)
        captures.value.push({ blob, url, name: `scan_${Date.now()}.jpg` })
      }
    },
    'image/jpeg',
    0.85,
  )
}

function removeCapture(index) {
  URL.revokeObjectURL(captures.value[index].url)
  captures.value.splice(index, 1)
}

async function uploadCaptures() {
  if (captures.value.length === 0) return
  uploading.value = true
  uploadProgress.value = 0

  try {
    const files = captures.value.map((c, i) => {
      return new File([c.blob], c.name, { type: 'image/jpeg' })
    })

    await uploadDocuments(files, (event) => {
      if (event.total) {
        uploadProgress.value = Math.round((event.loaded / event.total) * 100)
      }
    })

    notify.success(`${files.length} Scan(s) hochgeladen.`)
    captures.value.forEach(c => URL.revokeObjectURL(c.url))
    captures.value = []
    stopCamera()
    router.push('/dokumente')
  } catch {
    notify.error('Upload fehlgeschlagen.')
  } finally {
    uploading.value = false
  }
}

function handleFileSelect(event) {
  const files = event.target.files
  if (!files?.length) return

  for (const file of files) {
    const url = URL.createObjectURL(file)
    captures.value.push({ blob: file, url, name: file.name })
  }
}

onMounted(() => {
  if (navigator.mediaDevices?.getUserMedia) {
    startCamera()
  } else {
    cameraError.value = 'Kamera wird in diesem Browser nicht unterstuetzt.'
  }
})

onUnmounted(() => {
  stopCamera()
  captures.value.forEach(c => URL.revokeObjectURL(c.url))
})
</script>

<template>
  <div class="space-y-4">
    <!-- Kamera-Bereich -->
    <div class="card !p-0 overflow-hidden">
      <div v-if="cameraActive" class="relative bg-black">
        <video ref="videoRef" autoplay playsinline class="w-full max-h-[60vh] object-contain"></video>
        <!-- Hilfslinien -->
        <div class="absolute inset-8 border-2 border-white/30 rounded-lg pointer-events-none"></div>
        <!-- Steuerung -->
        <div class="absolute bottom-4 left-0 right-0 flex items-center justify-center gap-6">
          <button @click="switchCamera" class="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-white backdrop-blur">
            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
          <button
            @click="capturePhoto"
            class="flex h-16 w-16 items-center justify-center rounded-full border-4 border-white bg-white/20 backdrop-blur transition-transform active:scale-90"
          >
            <div class="h-12 w-12 rounded-full bg-white"></div>
          </button>
          <button @click="stopCamera" class="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-white backdrop-blur">
            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Kamera-Fehler oder Start-Button -->
      <div v-else class="flex flex-col items-center justify-center py-16 px-4">
        <svg class="h-16 w-16 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1">
          <path stroke-linecap="round" stroke-linejoin="round" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
          <path stroke-linecap="round" stroke-linejoin="round" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
        <p v-if="cameraError" class="mt-3 text-sm text-red-500">{{ cameraError }}</p>
        <div class="mt-4 flex gap-3">
          <button @click="startCamera" class="btn-primary">Kamera starten</button>
          <label class="btn-secondary cursor-pointer">
            Datei waehlen
            <input type="file" accept="image/*,application/pdf" multiple class="hidden" @change="handleFileSelect" />
          </label>
        </div>
      </div>
    </div>

    <canvas ref="canvasRef" class="hidden"></canvas>

    <!-- Aufnahmen -->
    <div v-if="captures.length" class="space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-700">{{ captures.length }} Aufnahme{{ captures.length !== 1 ? 'n' : '' }}</h2>
        <button @click="uploadCaptures" :disabled="uploading" class="btn-primary">
          <span v-if="uploading">Hochladen... {{ uploadProgress }}%</span>
          <span v-else>Hochladen</span>
        </button>
      </div>

      <!-- Fortschrittsbalken -->
      <div v-if="uploading" class="h-2 rounded-full bg-gray-200 overflow-hidden">
        <div class="h-full bg-primary-500 transition-all" :style="{ width: uploadProgress + '%' }"></div>
      </div>

      <!-- Vorschau-Grid -->
      <div class="grid grid-cols-3 gap-2 sm:grid-cols-4">
        <div v-for="(cap, i) in captures" :key="i" class="relative group aspect-[3/4] rounded-lg overflow-hidden bg-gray-100">
          <img :src="cap.url" class="h-full w-full object-cover" />
          <button
            @click="removeCapture(i)"
            class="absolute top-1 right-1 flex h-6 w-6 items-center justify-center rounded-full bg-red-500 text-white opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <span class="absolute bottom-1 left-1 badge bg-black/50 text-white text-xs">{{ i + 1 }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
