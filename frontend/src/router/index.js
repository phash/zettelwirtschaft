import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: '/dokumente',
    name: 'documents',
    component: () => import('../views/DocumentsView.vue'),
  },
  {
    path: '/dokumente/:id',
    name: 'document-detail',
    component: () => import('../views/DocumentDetailView.vue'),
    props: true,
  },
  {
    path: '/upload',
    name: 'upload',
    component: () => import('../views/UploadView.vue'),
  },
  {
    path: '/pruefen',
    name: 'review',
    component: () => import('../views/ReviewView.vue'),
  },
  {
    path: '/suche',
    name: 'search',
    component: () => import('../views/SearchView.vue'),
  },
  {
    path: '/steuer',
    name: 'tax',
    component: () => import('../views/TaxView.vue'),
  },
  {
    path: '/garantien',
    name: 'warranties',
    component: () => import('../views/WarrantyView.vue'),
  },
  {
    path: '/scan',
    name: 'scan',
    component: () => import('../views/ScanView.vue'),
  },
  {
    path: '/einstellungen',
    name: 'settings',
    component: () => import('../views/SettingsView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
