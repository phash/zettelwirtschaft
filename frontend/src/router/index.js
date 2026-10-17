import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/pin',
    name: 'pin-login',
    component: () => import('../views/PinLoginView.vue'),
    meta: { public: true, title: 'Anmelden' },
  },
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
    meta: { title: 'Dashboard' },
  },
  {
    path: '/dokumente',
    name: 'documents',
    component: () => import('../views/DocumentsView.vue'),
    meta: { title: 'Dokumente' },
  },
  {
    path: '/dokumente/:id',
    name: 'document-detail',
    component: () => import('../views/DocumentDetailView.vue'),
    props: true,
    meta: { title: 'Dokument' },
  },
  {
    path: '/upload',
    name: 'upload',
    component: () => import('../views/UploadView.vue'),
    meta: { title: 'Upload' },
  },
  {
    path: '/pruefen',
    name: 'review',
    component: () => import('../views/ReviewView.vue'),
    meta: { title: 'Prüfen' },
  },
  {
    path: '/suche',
    name: 'search',
    component: () => import('../views/SearchView.vue'),
    meta: { title: 'Suche' },
  },
  {
    path: '/steuer',
    name: 'tax',
    component: () => import('../views/TaxView.vue'),
    meta: { title: 'Steuer' },
  },
  {
    path: '/garantien',
    name: 'warranties',
    component: () => import('../views/WarrantyView.vue'),
    meta: { title: 'Garantien' },
  },
  {
    path: '/assistent',
    name: 'chat',
    component: () => import('../views/ChatView.vue'),
    meta: { title: 'Assistent' },
  },
  {
    path: '/scan',
    name: 'scan',
    component: () => import('../views/ScanView.vue'),
    meta: { title: 'Scan' },
  },
  {
    path: '/einstellungen',
    name: 'settings',
    component: () => import('../views/SettingsView.vue'),
    meta: { title: 'Einstellungen' },
  },
  {
    path: '/hilfe',
    name: 'help',
    component: () => import('../views/HelpView.vue'),
    meta: { title: 'Hilfe' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    redirect: '/'
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Erst nach einer erfolgreichen Statusabfrage als geprueft markieren — schlaegt
// /api/auth/status fehl (Backend startet noch), wird bei der naechsten
// Navigation erneut gefragt.
let authChecked = false

// H-FE-1: redirect-Param muss einem internen Pfad entsprechen. Werte wie
// `https://evil.com`, `//evil.com/x`, oder `javascript:` werden ignoriert und
// fallen auf `/` zurueck. Ziele, die selbst wieder auf die PIN-Seite fuehren,
// ebenfalls — sonst landet der Nutzer nach korrektem PIN erneut im Formular.
export function safeRedirect(raw) {
  if (typeof raw !== 'string' || raw.length === 0) return '/'
  if (!raw.startsWith('/')) return '/'
  if (raw.startsWith('//')) return '/'
  if (raw.startsWith('/\\')) return '/'
  try {
    if (router.resolve(raw).name === 'pin-login') return '/'
  } catch {
    return '/'
  }
  return raw
}

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (!authChecked) {
    authChecked = await auth.checkStatus()
  }

  const loginRequired = auth.pinEnabled && !auth.isAuthenticated

  // PIN-Seite ohne Grund (PIN aus oder schon angemeldet) -> direkt zum Ziel.
  if (to.name === 'pin-login' && authChecked && !loginRequired) {
    return safeRedirect(to.query.redirect)
  }

  if (to.meta.public) return true

  if (loginRequired) {
    return { name: 'pin-login', query: { redirect: to.fullPath } }
  }

  return true
})

router.afterEach((to) => {
  const title = to.meta.title
  document.title = title ? `${title} — Zettelwirtschaft` : 'Zettelwirtschaft'
})

export default router
