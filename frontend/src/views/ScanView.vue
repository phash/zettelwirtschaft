<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { uploadDocuments } from '../services/api'
import { useNotificationStore } from '../stores/notifications'
import { reportUploadResult } from '../utils/uploadResult'
import { FILE_ACCEPT, isAllowedFile } from '../constants/fileTypes'

const router = useRouter()
const notify = useNotificationStore()

const cameraActive = ref(false)
const videoRef = ref(null)
const canvasRef = ref(null)
const captures = ref([])
const uploading = ref(false)
const uploadProgress = ref(0)
const facingMode = ref('environment')
const cameraError = ref('')

// MediaStream bewusst nicht reaktiv. startSeq verwirft Streams, deren
// getUserMedia erst aufloest, nachdem die Kamera gestoppt/gewechselt oder die
// View verlassen wurde — sonst bliebe die Kamera (LED) dauerhaft an.
let stream = null
let startSeq = 0
let unmounted = false
let captureSeq = 0

function cameraErrorMessage(err) {
  switch (err?.name) {
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return 'Kamerazugriff nicht möglich. Bitte im Browser die Kamera-Berechtigung erteilen.'
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return 'Keine Kamera gefunden. Bitte stattdessen eine Datei wählen.'
    case 'NotReadableError':
    case 'TrackStartError':
      return 'Die Kamera wird gerade von einer anderen Anwendung verwendet. Bitte diese schließen und erneut versuchen.'
    case 'OverconstrainedError':
      return 'Die gewählte Kamera ist auf diesem Gerät nicht verfügbar. Bitte die andere Kamera versuchen.'
    case 'SecurityError':
      return 'Kamerazugriff wurde aus Sicherheitsgründen blockiert. Bitte über https:// oder http://localhost aufrufen.'
    default:
      return 'Kamera konnte nicht gestartet werden. Bitte erneut versuchen oder eine Datei wählen.'
  }
}

function stopTracks(s) {
  s?.getTracks().forEach(t => t.stop())
}

async function startCamera() {
  cameraError.value = ''

  if (!window.isSecureContext) {
    cameraError.value = 'Kamerazugriff erfordert HTTPS oder localhost. Bitte über https:// oder http://localhost aufrufen.'
    return
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    cameraError.value = 'Kamera wird in diesem Browser nicht unterstützt.'
    return
  }

  const seq = ++startSeq
  try {
    const constraints = {
      video: {
        facingMode: facingMode.value,
        width: { ideal: 2000 },
        height: { ideal: 2000 },
      },
    }
    const newStream = await navigator.mediaDevices.getUserMedia(constraints)
    if (unmounted || seq !== startSeq) {
      stopTracks(newStream)  // veralteter Start — Kamera sofort wieder freigeben
      return
    }
    stream = newStream
    cameraActive.value = true
    await nextTick()
    if (videoRef.value) {
      videoRef.value.srcObject = stream
    }
  } catch (err) {
    if (unmounted || seq !== startSeq) return
    cameraError.value = cameraErrorMessage(err)
    console.error('Camera error:', err)
  }
}

function stopCamera() {
  startSeq++  // laufende getUserMedia-Aufrufe verwerfen
  stopTracks(stream)
  stream = null
  cameraActive.value = false
}

async function switchCamera() {
  facingMode.value = facingMode.value === 'environment' ? 'user' : 'environment'
  stopCamera()
  await startCamera()
}

function uniqueName(name) {
  const taken = new Set(captures.value.map(c => c.name))
  if (!taken.has(name)) return name
  const dot = name.lastIndexOf('.')
  const stem = dot > 0 ? name.slice(0, dot) : name
  const ext = dot > 0 ? name.slice(dot) : ''
  let n = 2
  while (taken.has(`${stem}_${n}${ext}`)) n++
  return `${stem}_${n}${ext}`
}

function addCapture(file) {
  captures.value.push({
    id: ++captureSeq,
    file,
    name: file.name,
    url: URL.createObjectURL(file),
    isImage: file.type.startsWith('image/'),
  })
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
        const name = uniqueName(`scan_${Date.now()}_${captureSeq + 1}.jpg`)
        addCapture(new File([blob], name, { type: 'image/jpeg' }))
      }
    },
    'image/jpeg',
    0.85,
  )
}

function removeCapture(capture) {
  URL.revokeObjectURL(capture.url)
  captures.value = captures.value.filter(c => c.id !== capture.id)
}

async function uploadCaptures() {
  if (captures.value.length === 0) return
  uploading.value = true
  uploadProgress.value = 0

  try {
    const result = await uploadDocuments(captures.value.map(c => c.file), (event) => {
      if (event.total) {
        uploadProgress.value = Math.round((event.loaded / event.total) * 100)
      }
    })
    const { uploaded, rejected } = reportUploadResult(result, notify, { noun: 'Scan(s)' })

    // Abgelehnte Aufnahmen behalten (der Nutzer kann sie entfernen oder neu
    // aufnehmen), nur erfolgreich hochgeladene verwerfen. Zuordnung ueber den
    // (eindeutigen) Dateinamen; falls der Server den Namen veraendert hat,
    // konservativ alles behalten, was nicht als hochgeladen gemeldet wurde.
    const rejectedNames = new Set(rejected.map(r => r.filename))
    let keep = captures.value.filter(c => rejectedNames.has(c.name))
    if (keep.length < rejected.length) {
      const uploadedNames = new Set(uploaded.map(u => u.original_filename))
      keep = captures.value.filter(c => !uploadedNames.has(c.name))
    }
    const keepIds = new Set(keep.map(c => c.id))
    captures.value.filter(c => !keepIds.has(c.id)).forEach(c => URL.revokeObjectURL(c.url))
    captures.value = keep

    if (rejected.length === 0 && uploaded.length > 0) {
      stopCamera()
      router.push('/dokumente')
    }
  } catch {
    notify.error('Upload fehlgeschlagen.')
  } finally {
    uploading.value = false
  }
}

function handleFileSelect(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  for (const file of files) {
    if (!isAllowedFile(file)) {
      notify.error(`${file.name}: Dateityp wird nicht unterstützt.`)
      continue
    }
    const name = uniqueName(file.name)
    // Original-File (inkl. MIME-Typ) behalten, nur bei Namenskollision umbenennen.
    addCapture(name === file.name ? file : new File([file], name, { type: file.type }))
  }
}

onMounted(() => {
  if (navigator.mediaDevices?.getUserMedia) {
    startCamera()
  } else {
    cameraError.value = 'Kamera wird in diesem Browser nicht unterstützt.'
  }
})

onUnmounted(() => {
  unmounted = true
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
          <button @click="switchCamera" aria-label="Kamera wechseln" title="Kamera wechseln" class="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-white backdrop-blur">
            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
          <button
            @click="capturePhoto"
            aria-label="Foto aufnehmen"
            title="Foto aufnehmen"
            class="flex h-16 w-16 items-center justify-center rounded-full border-4 border-white bg-white/20 backdrop-blur transition-transform active:scale-90"
          >
            <div class="h-12 w-12 rounded-full bg-white"></div>
          </button>
          <button @click="stopCamera" aria-label="Kamera schließen" title="Kamera schließen" class="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 text-white backdrop-blur">
            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Kamera-Fehler oder Start-Button -->
      <div v-else class="flex flex-col items-center justify-center py-16 px-4">
        <svg class="h-16 w-16 text-gray-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
          <path stroke-linecap="round" stroke-linejoin="round" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
        <p v-if="cameraError" class="mt-3 text-center text-sm text-red-500" role="alert">{{ cameraError }}</p>
        <div class="mt-4 flex gap-3">
          <button @click="startCamera" class="btn-primary">Kamera starten</button>
          <label class="btn-secondary cursor-pointer">
            Datei wählen
            <input type="file" :accept="FILE_ACCEPT" multiple class="hidden" @change="handleFileSelect" />
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
        <div v-for="(cap, i) in captures" :key="cap.id" class="relative group aspect-[3/4] rounded-lg overflow-hidden bg-gray-100" data-testid="capture">
          <img v-if="cap.isImage" :src="cap.url" :alt="`Aufnahme ${i + 1}`" class="h-full w-full object-cover" />
          <div v-else class="flex h-full w-full items-center justify-center p-2 text-center text-xs font-medium text-gray-500 break-all">
            {{ cap.name }}
          </div>
          <!-- Auf Touch-Geraeten (kein Hover) immer sichtbar, sonst bei Hover/Fokus. -->
          <button
            @click="removeCapture(cap)"
            :aria-label="`Aufnahme ${i + 1} entfernen`"
            title="Aufnahme entfernen"
            class="absolute top-1 right-1 flex h-6 w-6 items-center justify-center rounded-full bg-red-500 text-white transition-opacity [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 [@media(hover:hover)]:focus-visible:opacity-100"
          >
            <svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <span class="absolute bottom-1 left-1 badge bg-black/50 text-white text-xs">{{ i + 1 }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
