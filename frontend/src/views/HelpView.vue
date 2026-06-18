<script setup>
import { useOnboardingStore } from '../stores/onboarding'

const onboarding = useOnboardingStore()

const steps = [
  { n: '1', title: 'Erfassen', text: 'Dokument hochladen, mit dem Smartphone scannen, in den Watch-Ordner legen oder aus dem E-Mail-Postfach abholen lassen.' },
  { n: '2', title: 'Ordnen', text: 'Die lokale KI liest den Text, erkennt Typ, Datum, Betrag und Aussteller und legt das Dokument automatisch ab.' },
  { n: '3', title: 'Finden', text: 'Per Volltext suchen, den KI-Assistenten fragen, Steuerbelege exportieren oder Garantien im Blick behalten.' },
]

const features = [
  { to: '/upload', icon: 'upload', title: 'Upload', text: 'Dateien per Drag & Drop hinzufügen (PDF, JPG, PNG, TIFF).' },
  { to: '/scan', icon: 'camera', title: 'Scan', text: 'Dokumente mit der Smartphone-Kamera erfassen (PWA).' },
  { to: '/dokumente', icon: 'folder', title: 'Dokumente', text: 'Archiv durchsehen, filtern, sortieren und einzelne Dokumente öffnen.' },
  { to: '/pruefen', icon: 'check', title: 'Zu prüfen', text: 'Rückfragen der KI beantworten, Erkennung bestätigen oder korrigieren.' },
  { to: '/suche', icon: 'search', title: 'Suche', text: 'Volltextsuche mit Facetten-Filtern und gespeicherten Suchen.' },
  { to: '/assistent', icon: 'chat', title: 'KI-Assistent', text: 'Fragen in natürlicher Sprache stellen — mit Quellenangaben.' },
  { to: '/steuer', icon: 'calculator', title: 'Steuer', text: 'Steuerrelevante Belege filtern und als ZIP-Paket exportieren.' },
  { to: '/garantien', icon: 'shield', title: 'Garantien', text: 'Ablaufdaten verfolgen, mit Erinnerungen vor Garantieende.' },
  { to: '/einstellungen', icon: 'cog', title: 'System', text: 'Health, Backups, Ablagebereiche, E-Mail-Konten und Updates.' },
]

const faqs = [
  { q: 'Wie füge ich ein Dokument hinzu?', a: 'Vier Wege: über „Upload“ per Drag & Drop, über „Scan“ mit der Handykamera, über den Watch-Ordner (automatisch erkannt) oder per E-Mail-Abruf (in den Einstellungen einrichten). Erlaubt sind PDF, JPG, PNG, TIFF und BMP bis 50 MB.' },
  { q: 'Was bedeutet „Zu prüfen“?', a: 'Wenn die KI bei der Erkennung unsicher ist (z. B. unklarer Betrag oder Ablagebereich), landet das Dokument unter „Zu prüfen“ statt blind archiviert zu werden. Dort beantwortest du gezielte Rückfragen und bestätigst oder korrigierst die erkannten Werte.' },
  { q: 'Wie funktioniert die automatische Erkennung?', a: 'Zuerst extrahiert eine Texterkennung (OCR) den Inhalt. Anschließend bestimmt ein lokales Sprachmodell (Ollama) Dokumenttyp, Datum, Betrag, Aussteller, Steuer- und Garantieangaben und ordnet das Dokument einem Ablagebereich zu.' },
  { q: 'Etwas wurde falsch erkannt — was kann ich tun?', a: 'Öffne das Dokument in der Detailansicht und korrigiere die Felder direkt. Die App merkt sich wiederkehrende Korrekturen und wendet sie künftig automatisch an — die Erkennung wird mit der Zeit besser.' },
  { q: 'Wo werden meine Dokumente gespeichert?', a: 'Alles liegt lokal auf deiner Hardware im Ordner „data“: Originale, das geordnete Archiv (nach Bereich/Jahr/Monat/Typ) und die Sicherungen. Nichts wird in eine Cloud hochgeladen.' },
  { q: 'Wie suche ich in meinen Dokumenten?', a: 'Nutze die Suchleiste oben oder die Seite „Suche“. Sie durchsucht den Volltext aller Dokumente und lässt sich nach Typ, Zeitraum und weiteren Facetten filtern. Häufige Suchen kannst du speichern.' },
  { q: 'Was kann der KI-Assistent?', a: 'Stell Fragen in natürlicher Sprache, z. B. „Wie viel habe ich 2025 für Handwerker ausgegeben?“. Der Assistent durchsucht deine Dokumente und antwortet mit Verweisen auf die zugrunde liegenden Belege.' },
  { q: 'Wie exportiere ich Steuerbelege?', a: 'Auf der Seite „Steuer“ wählst du das Jahr und die Kategorien aus und erzeugst ein ZIP-Paket mit allen Belegen, einer Übersichts-PDF und einer CSV-Liste — fertig für die Steuererklärung.' },
  { q: 'Wie scanne ich mit dem Smartphone?', a: 'Öffne die App im Handy-Browser (gleiches WLAN) und nutze „Scan“. Die Kamera-Funktion benötigt eine sichere Verbindung (HTTPS oder localhost); ist die App nur über http erreichbar, richte HTTPS ein (siehe Installationshinweise) oder lade die Fotos stattdessen über „Upload“ hoch.' },
  { q: 'Brauche ich eine Internetverbindung?', a: 'Nein. Erfassung, OCR, KI-Analyse und Suche laufen vollständig lokal. Eine Verbindung nach außen entsteht nur, wenn du die optionale Update-Prüfung in den Einstellungen aktivierst.' },
  { q: 'Wie schütze ich den Zugang?', a: 'In der Konfiguration lässt sich ein optionaler PIN-Schutz aktivieren. Danach fragt die App beim Öffnen nach dem PIN. Für ein privates Heimnetz ist das meist ausreichend.' },
  { q: 'Wie sichere ich meine Daten?', a: 'Unter „System“ kannst du jederzeit ein Backup erstellen; zusätzlich läuft täglich eine automatische Sicherung. Für ein vollständiges Backup inklusive Originaldateien den Datenordner mitsichern (z. B. auf NAS).' },
]

const icons = {
  upload: 'M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12',
  camera: 'M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z',
  folder: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z',
  check: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
  search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
  chat: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
  calculator: 'M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z',
  shield: 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z',
  cog: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z',
}
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-10">
    <!-- Header -->
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">Hilfe &amp; Erste Schritte</h1>
        <p class="mt-1 max-w-2xl text-gray-600">
          So holst du das Beste aus deinem lokalen Dokumentenarchiv heraus.
        </p>
      </div>
      <button class="btn-secondary" @click="onboarding.restart()">
        <svg class="mr-2 h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
        </svg>
        Tour erneut starten
      </button>
    </div>

    <!-- Schnellstart -->
    <section>
      <h2 class="mb-4 text-lg font-semibold text-gray-900">In drei Schritten</h2>
      <div class="grid gap-4 sm:grid-cols-3">
        <div v-for="s in steps" :key="s.n" class="card">
          <div class="flex h-9 w-9 items-center justify-center rounded-lg bg-primary-600 text-sm font-bold text-white">
            {{ s.n }}
          </div>
          <h3 class="mt-3 font-semibold text-gray-900">{{ s.title }}</h3>
          <p class="mt-1 text-sm leading-relaxed text-gray-600">{{ s.text }}</p>
        </div>
      </div>
    </section>

    <!-- Funktionen -->
    <section>
      <h2 class="mb-4 text-lg font-semibold text-gray-900">Funktionen im Überblick</h2>
      <div class="grid gap-3 sm:grid-cols-2">
        <router-link
          v-for="f in features"
          :key="f.to"
          :to="f.to"
          class="group flex items-start gap-3 rounded-xl border border-gray-200 bg-white p-4 transition-colors hover:border-primary-300 hover:bg-primary-50/40"
        >
          <span class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
            <svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" :d="icons[f.icon]" />
            </svg>
          </span>
          <span class="min-w-0">
            <span class="flex items-center gap-1 font-semibold text-gray-900">
              {{ f.title }}
              <svg class="h-4 w-4 text-gray-300 transition-colors group-hover:text-primary-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
              </svg>
            </span>
            <span class="mt-0.5 block text-sm leading-relaxed text-gray-600">{{ f.text }}</span>
          </span>
        </router-link>
      </div>
    </section>

    <!-- FAQ -->
    <section>
      <h2 class="mb-4 text-lg font-semibold text-gray-900">Häufige Fragen</h2>
      <div class="divide-y divide-gray-100 overflow-hidden rounded-xl border border-gray-200 bg-white">
        <details v-for="(item, i) in faqs" :key="i" class="group">
          <summary class="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 text-sm font-medium text-gray-900 hover:bg-gray-50">
            {{ item.q }}
            <svg class="h-5 w-5 flex-shrink-0 text-gray-400 transition-transform group-open:rotate-180" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </summary>
          <p class="px-5 pb-4 text-sm leading-relaxed text-gray-600">{{ item.a }}</p>
        </details>
      </div>
    </section>

    <!-- Datenschutz -->
    <section class="rounded-xl border border-green-200 bg-green-50 p-5">
      <div class="flex items-start gap-3">
        <svg class="h-6 w-6 flex-shrink-0 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.623 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
        </svg>
        <div>
          <h3 class="font-semibold text-green-900">Deine Daten bleiben bei dir</h3>
          <p class="mt-1 text-sm leading-relaxed text-green-800">
            Zettelwirtschaft läuft komplett lokal — keine Cloud, keine Telemetrie. Bei Fragen, die hier nicht
            beantwortet sind, hilft die ausführliche Dokumentation auf
            <a href="https://github.com/phash/zettelwirtschaft" target="_blank" rel="noopener noreferrer" class="font-medium underline">GitHub</a> weiter.
          </p>
        </div>
      </div>
    </section>
  </div>
</template>
