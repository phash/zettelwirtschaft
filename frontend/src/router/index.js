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
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    redirect: '/'
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

let authChecked = false

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (!authChecked) {
    await auth.checkStatus()
    authChecked = true
  }

  if (to.meta.public) return true

  if (auth.pinEnabled && !auth.isAuthenticated) {
    return { name: 'pin-login', query: { redirect: to.fullPath } }
  }

  return true
})

router.afterEach((to) => {
  const title = to.meta.title
  document.title = title ? `${title} — Zettelwirtschaft` : 'Zettelwirtschaft'
})

export default router
