import { Page } from '@playwright/test';

// ============================================================
// Mock Data
// ============================================================

export const MOCK_HEALTH = {
  status: 'ok',
  database: 'ok',
  ollama: 'ok',
  chromadb: 'ok',
  app_version: '1.2.2',
  install_path: 'C:\\Users\\test\\AppData\\Local\\Zettelwirtschaft',
};

export const MOCK_SYSTEM_HEALTH = {
  ...MOCK_HEALTH,
  storage: {
    database_size: '2.5 MB',
    archive_size: '150 MB',
    upload_size: '25 MB',
    disk_free: '50 GB',
    disk_total: '256 GB',
    disk_usage_percent: 80.5,
  },
  components: {
    backend: { status: 'ok', message: 'FastAPI laeuft' },
    database: { status: 'ok', message: 'SQLite verbunden' },
    ollama: { status: 'ok', message: 'Ollama erreichbar' },
    chromadb: { status: 'ok', message: 'ChromaDB verbunden' },
  },
};

export const MOCK_AUTH_STATUS = {
  pin_enabled: false,
  authenticated: true,
};

export const MOCK_AUTH_STATUS_PIN_ENABLED = {
  pin_enabled: true,
  authenticated: false,
};

export const MOCK_STATS = {
  total_documents: 42,
  documents_this_month: 7,
  pending_reviews: 3,
  expiring_warranties_30d: 2,
  processing_jobs: 0,
};

export const MOCK_EMAIL_STATS = {
  total_accounts: 1,
  active_accounts: 1,
  total_processed: 15,
  relevant_count: 8,
  irrelevant_count: 6,
  failed_count: 1,
};

export const MOCK_QUEUE_STATUS = {
  paused: false,
};

export const MOCK_QUEUE_STATUS_ACTIVE = {
  paused: false,
};

export const MOCK_JOBS_EMPTY = {
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
};

export const MOCK_JOBS_ACTIVE = {
  items: [
    {
      id: 10,
      filename: 'rechnung-2026.pdf',
      status: 'PROCESSING',
      source: 'UPLOAD',
      created_at: '2026-03-03T10:00:00',
      updated_at: '2026-03-03T10:00:00',
    },
    {
      id: 11,
      filename: 'quittung-cafe.jpg',
      status: 'PENDING',
      source: 'UPLOAD',
      created_at: '2026-03-03T10:01:00',
      updated_at: '2026-03-03T10:01:00',
    },
  ],
  total: 2,
  page: 1,
  page_size: 20,
};

export const MOCK_JOBS_FAILED = {
  items: [
    {
      id: 20,
      filename: 'corrupt-file.pdf',
      status: 'FAILED',
      source: 'UPLOAD',
      error_message: 'OCR fehlgeschlagen: Datei ist beschaedigt',
      created_at: '2026-03-02T15:00:00',
      updated_at: '2026-03-02T15:00:00',
    },
  ],
  total: 1,
  page: 1,
  page_size: 20,
};

export const MOCK_DOCUMENTS = {
  items: [
    {
      id: 1,
      title: 'Stromrechnung Januar 2026',
      original_filename: 'stromrechnung_jan.pdf',
      document_type: 'RECHNUNG',
      date: '2026-01-15',
      amount: 89.50,
      issuer: 'Stadtwerke Muenchen',
      tax_relevant: true,
      tax_category: 'Haushaltsnahe Dienstleistungen',
      tags: [{ id: 1, name: 'Nebenkosten' }],
      filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
      status: 'ACTIVE',
      created_at: '2026-01-16T10:30:00',
    },
    {
      id: 2,
      title: 'Kaufvertrag Laptop Dell XPS',
      original_filename: 'kaufvertrag_laptop.pdf',
      document_type: 'KAUFVERTRAG',
      date: '2026-01-10',
      amount: 1299.00,
      issuer: 'MediaMarkt',
      tax_relevant: false,
      tax_category: null,
      tags: [{ id: 2, name: 'Elektronik' }],
      filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
      status: 'ACTIVE',
      created_at: '2026-01-11T14:20:00',
    },
    {
      id: 3,
      title: 'Lohnabrechnung Dezember 2025',
      original_filename: 'lohn_dez_2025.pdf',
      document_type: 'LOHNABRECHNUNG',
      date: '2025-12-31',
      amount: 3500.00,
      issuer: 'Arbeitgeber GmbH',
      tax_relevant: true,
      tax_category: 'Werbungskosten',
      tags: [],
      filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
      status: 'ACTIVE',
      created_at: '2026-01-02T09:00:00',
    },
  ],
  total: 3,
  page: 1,
  page_size: 20,
};

export const MOCK_DOCUMENT_DETAIL = {
  id: 1,
  title: 'Stromrechnung Januar 2026',
  original_filename: 'stromrechnung_jan.pdf',
  file_path: '/app/data/archive/privat/2026/01/rechnung/abc-stromrechnung_jan.pdf',
  file_type: 'pdf',
  ai_confidence: 0.92,
  document_type: 'RECHNUNG',
  date: '2026-01-15',
  amount: 89.50,
  issuer: 'Stadtwerke Muenchen',
  reference_number: 'RE-2026-001',
  tax_relevant: true,
  tax_category: 'Haushaltsnahe Dienstleistungen',
  ocr_confidence: 0.92,
  summary: 'Monatliche Stromrechnung fuer Januar 2026, Verbrauch 280 kWh.',
  tags: [
    { id: 1, name: 'Nebenkosten' },
    { id: 3, name: 'Strom' },
  ],
  filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
  filing_scope_id: 1,
  warranty_info: null,
  review_questions: [],
  status: 'ACTIVE',
  created_at: '2026-01-16T10:30:00',
  updated_at: '2026-01-16T10:30:00',
};

export const MOCK_DOCUMENT_WITH_WARRANTY = {
  ...MOCK_DOCUMENT_DETAIL,
  id: 2,
  title: 'Kaufvertrag Laptop Dell XPS',
  document_type: 'KAUFVERTRAG',
  warranty_info: {
    id: 1,
    product_name: 'Dell XPS 15',
    purchase_date: '2026-01-10',
    warranty_end_date: '2028-01-10',
    warranty_type: 'MANUFACTURER',
    retailer: 'MediaMarkt',
  },
};

export const MOCK_DOCUMENT_WITH_QUESTIONS = {
  ...MOCK_DOCUMENT_DETAIL,
  id: 4,
  title: 'Unklares Dokument',
  ocr_confidence: 0.45,
  review_questions: [
    {
      id: 1,
      question: 'Um welchen Dokumenttyp handelt es sich?',
      question_type: 'classification',
      explanation: 'Die KI konnte den Dokumenttyp nicht eindeutig bestimmen.',
      suggested_answers: ['Rechnung', 'Quittung', 'Kaufvertrag'],
      field_affected: 'document_type',
      answer: null,
      is_answered: false,
      priority: 1,
    },
    {
      id: 2,
      question: 'Wie lautet der Gesamtbetrag?',
      question_type: 'extraction',
      explanation: 'Der Betrag war im OCR-Text nicht eindeutig erkennbar.',
      suggested_answers: ['45,99 EUR', '459,90 EUR'],
      field_affected: 'amount',
      answer: null,
      is_answered: false,
      priority: 2,
    },
  ],
  status: 'NEEDS_REVIEW',
};

export const MOCK_TAGS = [
  { id: 1, name: 'Nebenkosten', count: 5 },
  { id: 2, name: 'Elektronik', count: 3 },
  { id: 3, name: 'Strom', count: 2 },
  { id: 4, name: 'Versicherung', count: 4 },
];

export const MOCK_FILING_SCOPES = [
  {
    id: 1,
    name: 'Privat',
    slug: 'privat',
    description: 'Private Dokumente',
    keywords: ['privat', 'haushalt'],
    is_default: true,
    color: '#3B82F6',
  },
  {
    id: 2,
    name: 'Praxis',
    slug: 'praxis',
    description: 'Dokumente der Arztpraxis',
    keywords: ['praxis', 'patient', 'behandlung'],
    is_default: false,
    color: '#10B981',
  },
];

export const MOCK_UPLOAD_RESPONSE = {
  uploaded: [
    { document_id: '100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'Hochgeladen' },
  ],
  rejected: [],
};

export const MOCK_UPLOAD_RESPONSE_MULTI = {
  uploaded: [
    { document_id: '100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'Hochgeladen' },
    { document_id: '101', original_filename: 'quittung.jpg', status: 'PENDING', message: 'Hochgeladen' },
  ],
  rejected: [],
};

export const MOCK_JOB_STATUS_PENDING = {
  id: 100,
  filename: 'rechnung.pdf',
  status: 'PENDING',
  source: 'UPLOAD',
};

export const MOCK_JOB_STATUS_PROCESSING = {
  id: 100,
  filename: 'rechnung.pdf',
  status: 'PROCESSING',
  source: 'UPLOAD',
};

export const MOCK_JOB_STATUS_COMPLETED = {
  id: 100,
  filename: 'rechnung.pdf',
  status: 'COMPLETED',
  source: 'UPLOAD',
  document_id: 5,
};

export const MOCK_JOB_STATUS_NEEDS_REVIEW = {
  id: 100,
  filename: 'rechnung.pdf',
  status: 'NEEDS_REVIEW',
  source: 'UPLOAD',
  document_id: 6,
};

export const MOCK_REVIEW_PENDING = {
  documents: [
    {
      id: 4,
      title: 'Unklares Dokument',
      document_type: 'SONSTIGES',
      date: '2026-02-15',
      amount: null,
      issuer: null,
      ocr_confidence: 0.45,
      open_questions: 2,
      total_questions: 2,
      filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
    },
    {
      id: 5,
      title: 'Arztrechnung',
      document_type: 'ARZTRECHNUNG',
      date: '2026-02-20',
      amount: 120.00,
      issuer: 'Dr. Mueller',
      ocr_confidence: 0.65,
      open_questions: 1,
      total_questions: 1,
      filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
    },
  ],
  total: 2,
};

export const MOCK_REVIEW_DETAIL = {
  document: MOCK_DOCUMENT_WITH_QUESTIONS,
  questions: MOCK_DOCUMENT_WITH_QUESTIONS.review_questions,
  confident_fields: {
    document_type: 'SONSTIGES',
    issuer: null,
    document_date: '2026-02-15',
    amount: null,
    currency: 'EUR',
    filing_scope: 'Privat',
  },
};

export const MOCK_REVIEW_STATS = {
  total_pending: 2,
  total_reviewed: 40,
  average_confidence: 0.78,
};

export const MOCK_SEARCH_RESULTS = {
  results: [
    {
      id: 1,
      title: 'Stromrechnung Januar 2026',
      document_type: 'RECHNUNG',
      date: '2026-01-15',
      amount: 89.50,
      issuer: 'Stadtwerke Muenchen',
      highlight: 'Monatliche <b>Stromrechnung</b> fuer Januar 2026',
      tags: [{ id: 1, name: 'Nebenkosten' }],
      score: 2.5,
    },
    {
      id: 3,
      title: 'Lohnabrechnung Dezember 2025',
      document_type: 'LOHNABRECHNUNG',
      date: '2025-12-31',
      amount: 3500.00,
      issuer: 'Arbeitgeber GmbH',
      highlight: 'Dezember <b>Abrechnung</b> Brutto/Netto',
      tags: [],
      score: 1.8,
    },
  ],
  total: 2,
  page: 1,
  per_page: 20,
  facets: {
    document_types: [
      { value: 'RECHNUNG', count: 1 },
      { value: 'LOHNABRECHNUNG', count: 1 },
    ],
    years: [
      { value: 2026, count: 1 },
      { value: 2025, count: 1 },
    ],
  },
};

export const MOCK_SEARCH_SUGGESTIONS = {
  suggestions: ['Stromrechnung', 'Strom', 'Stadtwerke'],
};

export const MOCK_SAVED_SEARCHES = [
  { id: 1, name: 'Rechnungen 2026', query_params: { q: 'rechnung', date_from: '2026-01-01' } },
  { id: 2, name: 'Alle Versicherungen', query_params: { q: 'versicherung' } },
];

export const MOCK_TAX_YEARS = {
  years: [2026, 2025, 2024],
};

export const MOCK_TAX_SUMMARY = {
  year: 2026,
  total_documents: 12,
  total_amount: 4580.50,
  categories: [
    {
      category: 'Werbungskosten',
      label: 'Werbungskosten',
      document_count: 5,
      total_amount: 2100.00,
    },
    {
      category: 'Haushaltsnahe Dienstleistungen',
      label: 'Haushaltsnahe Dienstleistungen',
      document_count: 3,
      total_amount: 1280.50,
    },
    {
      category: 'Vorsorgeaufwendungen',
      label: 'Vorsorgeaufwendungen',
      document_count: 4,
      total_amount: 1200.00,
    },
  ],
};

export const MOCK_TAX_VALIDATION = {
  warnings: [
    'Dokument "Handwerkerrechnung Bad" hat keinen Betrag',
    '2 Dokumente ohne Steuerkategorie',
  ],
  is_valid: true,
};

export const MOCK_WARRANTIES = [
  {
    id: 1,
    document_id: 2,
    product_name: 'Dell XPS 15',
    purchase_date: '2026-01-10',
    warranty_end_date: '2028-01-10',
    warranty_type: 'MANUFACTURER',
    warranty_duration_months: 24,
    retailer: 'MediaMarkt',
    is_expired: false,
    reminder_sent: false,
    days_remaining: 675,
    document_title: 'Kaufvertrag Laptop',
  },
  {
    id: 2,
    document_id: 7,
    product_name: 'Waschmaschine Bosch',
    purchase_date: '2024-06-15',
    warranty_end_date: '2026-06-15',
    warranty_type: 'LEGAL',
    warranty_duration_months: 24,
    retailer: 'Saturn',
    is_expired: false,
    reminder_sent: false,
    days_remaining: 104,
    document_title: 'Kaufvertrag Waschmaschine',
  },
  {
    id: 3,
    document_id: 8,
    product_name: 'Staubsauger Dyson',
    purchase_date: '2022-03-01',
    warranty_end_date: '2024-03-01',
    warranty_type: 'MANUFACTURER',
    warranty_duration_months: 24,
    retailer: 'Amazon',
    is_expired: true,
    reminder_sent: true,
    days_remaining: 0,
    document_title: 'Kaufvertrag Staubsauger',
  },
];

export const MOCK_WARRANTY_STATS = {
  total: 3,
  active: 1,
  expiring_soon: 1,
  expired: 1,
};

export const MOCK_CHAT_RESPONSE = {
  answer: 'Ihre Stromrechnung vom Januar 2026 betraegt 89,50 EUR und wurde von den Stadtwerken Muenchen ausgestellt.',
  sources: [
    { document_id: 1, title: 'Stromrechnung Januar 2026', relevance: 0.95 },
  ],
};

export const MOCK_CHAT_HISTORY = {
  messages: [
    {
      id: 1,
      role: 'user',
      content: 'Was war meine letzte Stromrechnung?',
      sources: [],
      created_at: '2026-03-01T10:00:00',
    },
    {
      id: 2,
      role: 'assistant',
      content: 'Ihre letzte Stromrechnung betraegt 89,50 EUR.',
      sources: [{ document_id: 1, title: 'Stromrechnung Januar 2026', relevance: 0.95 }],
      created_at: '2026-03-01T10:00:01',
    },
  ],
  total: 2,
};

export const MOCK_NOTIFICATIONS = {
  notifications: [
    {
      id: 1,
      type: 'WARRANTY_EXPIRING',
      title: 'Garantie laeuft bald ab',
      message: 'Die Garantie fuer "Waschmaschine Bosch" laeuft in 30 Tagen ab.',
      is_read: false,
      created_at: '2026-03-01T08:00:00',
      document_id: 7,
    },
    {
      id: 2,
      type: 'PROCESSING_DONE',
      title: 'Verarbeitung abgeschlossen',
      message: 'Dokument "Stromrechnung" wurde erfolgreich verarbeitet.',
      is_read: true,
      created_at: '2026-02-28T15:00:00',
      document_id: 1,
    },
  ],
  total: 2,
};

export const MOCK_NOTIFICATION_COUNT = { unread: 1 };

export const MOCK_BACKUPS = {
  backups: [
    {
      filename: 'backup_2026-03-01_db.zip',
      size: '1.2 MB',
      created_at: '2026-03-01T03:00:00',
      type: 'db',
    },
  ],
};

export const MOCK_SYSTEM_SETTINGS = {
  watch_dir: '/app/data/watch',
  export_dir: '/app/data/export',
  watch_dir_host: '',
  export_dir_host: '',
};

export const MOCK_EMAIL_ACCOUNTS = [
  {
    id: 1,
    name: 'Rechnungs-Postfach',
    imap_host: 'imap.gmail.com',
    imap_port: 993,
    use_ssl: true,
    username: 'rechnungen@gmail.com',
    folder_inbox: 'INBOX',
    folder_processed: 'Verarbeitet',
    schedule_type: 'CRON',
    cron_expression: '0 */6 * * *',
    is_active: true,
    filing_scope_id: 1,
    filing_scope: { id: 1, name: 'Privat', slug: 'privat', color: '#3B82F6' },
    last_fetch_at: '2026-03-03T06:00:00',
    last_fetch_error: null,
  },
];

// ============================================================
// Mock Setup Functions
// ============================================================

/**
 * Set up base mocks needed for almost every page:
 * - /api/health
 * - /api/auth/status (no PIN)
 * - /api/notifications/count
 * - /api/filing-scopes
 * - /api/system/health
 */
export async function setupBaseMocks(page: Page) {
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: MOCK_HEALTH })
  );
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: MOCK_AUTH_STATUS })
  );
  await page.route('**/api/notifications/count', (route) =>
    route.fulfill({ json: MOCK_NOTIFICATION_COUNT })
  );
  await page.route('**/api/filing-scopes', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_FILING_SCOPES });
    }
    return route.continue();
  });
  await page.route('**/api/system/health', (route) =>
    route.fulfill({ json: MOCK_SYSTEM_HEALTH })
  );
}

/** Set up PIN-enabled auth mock */
export async function setupPinAuthMocks(page: Page) {
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: MOCK_HEALTH })
  );
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: MOCK_AUTH_STATUS_PIN_ENABLED })
  );
  await page.route('**/api/notifications/count', (route) =>
    route.fulfill({ json: { unread: 0 } })
  );
}

/** Set up dashboard-specific mocks */
export async function setupDashboardMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/stats', (route) =>
    route.fulfill({ json: MOCK_STATS })
  );
  await page.route('**/api/jobs/queue-status', (route) =>
    route.fulfill({ json: MOCK_QUEUE_STATUS })
  );
  await page.route('**/api/jobs?*', (route) =>
    route.fulfill({ json: MOCK_JOBS_EMPTY })
  );
  await page.route('**/api/jobs', (route) => {
    if (route.request().url().includes('?')) return route.continue();
    return route.fulfill({ json: MOCK_JOBS_EMPTY });
  });
  await page.route('**/api/documents?*', (route) =>
    route.fulfill({ json: MOCK_DOCUMENTS })
  );
  await page.route('**/api/documents', (route) => {
    if (route.request().method() === 'GET' && !route.request().url().includes('/documents/')) {
      return route.fulfill({ json: MOCK_DOCUMENTS });
    }
    return route.continue();
  });
  await page.route('**/api/email/stats', (route) =>
    route.fulfill({ json: MOCK_EMAIL_STATS })
  );
}

/** Set up documents list mocks */
export async function setupDocumentsMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/documents?*', (route) =>
    route.fulfill({ json: MOCK_DOCUMENTS })
  );
  await page.route('**/api/documents', (route) => {
    if (route.request().method() === 'GET' && !route.request().url().includes('/documents/')) {
      return route.fulfill({ json: MOCK_DOCUMENTS });
    }
    return route.continue();
  });
  await page.route('**/api/tags', (route) =>
    route.fulfill({ json: MOCK_TAGS })
  );
}

/** Set up document detail mocks */
export async function setupDocumentDetailMocks(page: Page, doc: Record<string, unknown> = MOCK_DOCUMENT_DETAIL) {
  await setupBaseMocks(page);
  await page.route(`**/api/documents/${doc.id}`, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: doc });
    }
    if (route.request().method() === 'PATCH') {
      return route.fulfill({ json: { ...doc, ...JSON.parse(route.request().postData() || '{}') } });
    }
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { message: 'Dokument geloescht' } });
    }
    return route.continue();
  });
  await page.route(`**/api/documents/${doc.id}/thumbnail`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from('fake-thumbnail'),
    })
  );
  await page.route(`**/api/documents/${doc.id}/file`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/pdf',
      body: Buffer.from('%PDF-1.4 fake'),
    })
  );
  await page.route(`**/api/documents/${doc.id}/tags`, (route) => {
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({ json: { id: 99, name: body.name } });
    }
    return route.continue();
  });
  await page.route('**/api/tags', (route) =>
    route.fulfill({ json: MOCK_TAGS })
  );
}

/** Set up upload mocks */
export async function setupUploadMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/documents/upload', (route) =>
    route.fulfill({ json: MOCK_UPLOAD_RESPONSE })
  );
}

/** Set up review mocks */
export async function setupReviewMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/review/pending', (route) =>
    route.fulfill({ json: MOCK_REVIEW_PENDING })
  );
  await page.route('**/api/review/documents/*', (route) => {
    const url = route.request().url();
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_REVIEW_DETAIL });
    }
    if (url.endsWith('/approve') && route.request().method() === 'POST') {
      return route.fulfill({ json: { message: 'Bestaetigt' } });
    }
    if (url.endsWith('/skip') && route.request().method() === 'POST') {
      return route.fulfill({ json: { message: 'Uebersprungen' } });
    }
    return route.continue();
  });
  await page.route('**/api/review/questions/*/answer', (route) =>
    route.fulfill({ json: { message: 'Antwort gespeichert' } })
  );
  await page.route('**/api/review/stats', (route) =>
    route.fulfill({ json: MOCK_REVIEW_STATS })
  );
  // Mock the document file for preview
  await page.route('**/api/documents/*/file', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/pdf',
      body: Buffer.from('%PDF-1.4 fake'),
    })
  );
  await page.route('**/api/documents/*/thumbnail', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'image/png',
      body: Buffer.from('fake-thumb'),
    })
  );
}

/** Set up search mocks */
export async function setupSearchMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/search?*', (route) =>
    route.fulfill({ json: MOCK_SEARCH_RESULTS })
  );
  await page.route('**/api/search', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_SEARCH_RESULTS });
    }
    return route.continue();
  });
  await page.route('**/api/search/suggest*', (route) =>
    route.fulfill({ json: MOCK_SEARCH_SUGGESTIONS })
  );
  await page.route('**/api/saved-searches', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_SAVED_SEARCHES });
    }
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({ json: { id: 3, name: body.name, query_params: body.query_params } });
    }
    return route.continue();
  });
  await page.route('**/api/saved-searches/*', (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { message: 'Geloescht' } });
    }
    return route.continue();
  });
}

/** Set up tax mocks */
export async function setupTaxMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/tax/years', (route) =>
    route.fulfill({ json: MOCK_TAX_YEARS })
  );
  await page.route('**/api/tax/summary/*', (route) =>
    route.fulfill({ json: MOCK_TAX_SUMMARY })
  );
  await page.route('**/api/tax/validate/*', (route) =>
    route.fulfill({ json: MOCK_TAX_VALIDATION })
  );
  await page.route('**/api/tax/export', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/zip',
      body: Buffer.from('fake-zip-content'),
    })
  );
}

/** Set up warranty mocks */
export async function setupWarrantyMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/warranties?*', (route) => {
    if (route.request().url().includes('/stats')) return route.continue();
    return route.fulfill({ json: MOCK_WARRANTIES });
  });
  await page.route('**/api/warranties', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_WARRANTIES });
    }
    return route.continue();
  });
  await page.route('**/api/warranties/stats', (route) =>
    route.fulfill({ json: MOCK_WARRANTY_STATS })
  );
}

/** Set up chat mocks */
export async function setupChatMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/chat', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({ json: MOCK_CHAT_RESPONSE });
    }
    return route.continue();
  });
  await page.route('**/api/chat/history*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_CHAT_HISTORY });
    }
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { message: 'Verlauf geloescht' } });
    }
    return route.continue();
  });
}

/** Set up settings mocks */
export async function setupSettingsMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/system/settings', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_SYSTEM_SETTINGS });
    }
    if (route.request().method() === 'PUT') {
      return route.fulfill({ json: { ...MOCK_SYSTEM_SETTINGS, ...JSON.parse(route.request().postData() || '{}') } });
    }
    return route.continue();
  });
  await page.route('**/api/system/backups', (route) =>
    route.fulfill({ json: MOCK_BACKUPS })
  );
  await page.route('**/api/system/backup', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({ json: { message: 'Backup erstellt', filename: 'backup_2026-03-03.zip' } });
    }
    return route.continue();
  });
  await page.route('**/api/system/maintenance/*', (route) =>
    route.fulfill({ json: { message: 'Wartung ausgefuehrt' } })
  );
  await page.route('**/api/filing-scopes', (route) => {
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({ json: { id: 3, ...body, slug: body.name?.toLowerCase() } });
    }
    return route.fulfill({ json: MOCK_FILING_SCOPES });
  });
  await page.route('**/api/filing-scopes/*', (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { message: 'Geloescht' } });
    }
    if (route.request().method() === 'PATCH') {
      return route.fulfill({ json: { ...MOCK_FILING_SCOPES[0], ...JSON.parse(route.request().postData() || '{}') } });
    }
    return route.continue();
  });
  await page.route('**/api/email/accounts', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_EMAIL_ACCOUNTS });
    }
    return route.continue();
  });
  await page.route('**/api/email/stats', (route) =>
    route.fulfill({ json: MOCK_EMAIL_STATS })
  );
  await page.route('**/api/notifications', (route) =>
    route.fulfill({ json: MOCK_NOTIFICATIONS })
  );
}

/** Set up scan page mocks (camera mock via addInitScript + upload endpoint) */
export async function setupScanMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/documents/upload', (route) =>
    route.fulfill({ json: MOCK_UPLOAD_RESPONSE })
  );
  await page.route('**/api/documents?*', (route) =>
    route.fulfill({ json: MOCK_DOCUMENTS })
  );
  await page.route('**/api/documents', (route) => {
    if (route.request().method() === 'GET' && !route.request().url().includes('/documents/')) {
      return route.fulfill({ json: MOCK_DOCUMENTS });
    }
    return route.continue();
  });
}

/** Mock camera API via addInitScript (must be called before page.goto) */
export async function mockCameraAPI(page: Page) {
  await page.addInitScript(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 640;
    canvas.height = 480;
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.fillStyle = '#888888';
      ctx.fillRect(0, 0, 640, 480);
    }
    const stream = canvas.captureStream();
    navigator.mediaDevices.getUserMedia = async () => stream;
    navigator.mediaDevices.enumerateDevices = async () => [
      { deviceId: 'cam1', kind: 'videoinput', label: 'Front Camera', groupId: 'g1', toJSON() { return {}; } } as MediaDeviceInfo,
    ];
  });
}

/** Mock camera API to simulate permission denied */
export async function mockCameraPermissionDenied(page: Page) {
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      throw new DOMException('Permission denied', 'NotAllowedError');
    };
    navigator.mediaDevices.enumerateDevices = async () => [];
  });
}

/** Set up notification mocks */
export async function setupNotificationMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/notifications', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_NOTIFICATIONS });
    }
    return route.continue();
  });
  await page.route('**/api/notifications/*/read', (route) =>
    route.fulfill({ json: { message: 'Gelesen' } })
  );
  await page.route('**/api/notifications/read-all', (route) =>
    route.fulfill({ json: { message: 'Alle gelesen' } })
  );
}
