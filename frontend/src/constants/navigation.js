// Hauptnavigation — einzige Quelle fuer Sidebar (Desktop) und BottomNav inkl.
// "Mehr"-Menue (Smartphone/Tablet). Vorher hatten beide eigene Listen und
// Icon-Pfade; unterhalb von lg waren Dashboard, Upload, Prüfen, Steuer,
// Garantien und System gar nicht erreichbar.

export const NAV_ICONS = {
  home: 'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6',
  folder: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z',
  upload: 'M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12',
  camera: 'M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z',
  'check-circle': 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
  search: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
  calculator: 'M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z',
  shield: 'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z',
  chat: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z',
  cog: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z',
  help: 'M9.879 7.519c1.171-1.025 3.071-1.025 4.242 0 1.172 1.025 1.172 2.687 0 3.712-.203.179-.43.326-.67.442-.745.361-1.45.999-1.45 1.827v.75M12 17.25h.008v.008H12v-.008zM21 12a9 9 0 11-18 0 9 9 0 0118 0z',
  menu: 'M4 6h16M4 12h16M4 18h16',
  logout: 'M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9',
}

// Reihenfolge = Sidebar-Reihenfolge. shortLabel fuer die schmale BottomNav,
// badge: 'review' -> Anzahl offener Prüfungen.
export const NAV_ITEMS = [
  { path: '/', label: 'Dashboard', icon: 'home' },
  { path: '/dokumente', label: 'Dokumente', icon: 'folder' },
  { path: '/upload', label: 'Upload', icon: 'upload' },
  { path: '/scan', label: 'Scan', icon: 'camera' },
  { path: '/pruefen', label: 'Zu prüfen', icon: 'check-circle', badge: 'review' },
  { path: '/suche', label: 'Suche', icon: 'search' },
  { path: '/assistent', label: 'KI-Assistent', shortLabel: 'Assistent', icon: 'chat' },
  { path: '/steuer', label: 'Steuer', icon: 'calculator' },
  { path: '/garantien', label: 'Garantien', icon: 'shield' },
  { path: '/einstellungen', label: 'System', icon: 'cog' },
  { path: '/hilfe', label: 'Hilfe', icon: 'help' },
]

// Direkt in der BottomNav (in dieser Reihenfolge, Scan hervorgehoben);
// alles andere landet im "Mehr"-Menue.
const BOTTOM_PATHS = ['/scan', '/dokumente', '/suche', '/assistent']

export const BOTTOM_NAV_ITEMS = BOTTOM_PATHS.map((path) => {
  const item = NAV_ITEMS.find((i) => i.path === path)
  return { ...item, primary: path === '/scan' }
})

export const MORE_NAV_ITEMS = NAV_ITEMS.filter((i) => !BOTTOM_PATHS.includes(i.path))

export function isNavActive(currentPath, path) {
  if (path === '/') return currentPath === '/'
  return currentPath === path || currentPath.startsWith(`${path}/`)
}
