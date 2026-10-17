import { BrowserContext, Page, Route } from '@playwright/test';
import type {
  AuthStatus,
  BackupListResponse,
  ChatHistoryResponse,
  ChatResponse,
  DashboardStats,
  DocumentListItem,
  DocumentResponse,
  EmailAccountResponse,
  EmailSettingsInfo,
  EmailStatsItem,
  FilingScopeResponse,
  FolderSettings,
  HealthResponse,
  JobStatusResponse,
  NotificationCount,
  NotificationResponse,
  Paginated,
  ProcessedEmailResponse,
  QueueStatus,
  ReviewDetailResponse,
  ReviewPendingResponse,
  ReviewStats,
  SavedSearchResponse,
  SearchResponse,
  SuggestResponse,
  SystemHealthResponse,
  TagListItem,
  TaskResponse,
  TaxExportValidation,
  TaxYears,
  TaxYearSummary,
  UploadResponse,
  WarrantyListItem,
  WarrantyStats,
} from './api-types';

// Alle Mocks sind gegen helpers/api-types.ts (Spiegel der Backend-Schemas)
// typgeprueft — `npm run typecheck` in e2e/ meldet Schema-Drift.

// ============================================================
// Mock Data
// ============================================================

export const MOCK_HEALTH = {
  status: 'ok',
  api: { status: 'ok' },
  database: { status: 'ok' },
  storage: { status: 'ok', message: '50.0 GB frei' },
  ollama: { status: 'ok' },
} satisfies HealthResponse;

export const MOCK_SYSTEM_HEALTH = {
  status: 'ok',
  app_version: '1.2.2',
  install_path: 'C:\\Users\\test\\AppData\\Local\\Zettelwirtschaft',
  deployment_mode: 'docker',
  components: {
    backend: { status: 'ok' },
    database: { status: 'ok' },
    ollama: {
      status: 'ok',
      message: null,
      configured_models: [
        { name: 'gemma4:e2b-it-qat', purpose: 'llm', available: true, downloading: false },
        { name: 'qwen3-embedding:4b', purpose: 'embedding', available: true, downloading: false },
      ],
      missing: [],
      version: '0.40.5',
      min_version: '0.30.3',
    },
    chromadb: { status: 'ok', vectors: 128 },
  },
  statistics: {
    total_documents: 42,
    database_size_bytes: 2_621_440,       // 2,5 MB
    archive_size_bytes: 157_286_400,      // 150,0 MB
    upload_size_bytes: 26_214_400,        // 25,0 MB
    disk_total_bytes: 274_877_906_944,    // 256,0 GB
    disk_used_bytes: 221_190_815_744,
    disk_free_bytes: 53_687_091_200,      // 50,0 GB
  },
} satisfies SystemHealthResponse;

export const MOCK_AUTH_STATUS = {
  pin_enabled: false,
  authenticated: true,
  pin_warning: true,
} satisfies AuthStatus;

export const MOCK_AUTH_STATUS_PIN_ENABLED = {
  pin_enabled: true,
  authenticated: false,
  pin_warning: false,
} satisfies AuthStatus;

export const MOCK_STATS = {
  total_documents: 42,
  documents_this_month: 7,
  pending_reviews: 3,
  expiring_warranties_30d: 2,
  processing_jobs: 0,
} satisfies DashboardStats;

export const MOCK_EMAIL_STATS = [
  {
    account_id: 1,
    account_name: 'Rechnungs-Postfach',
    total_processed: 15,
    relevant: 8,
    irrelevant: 6,
    failed: 1,
    last_checked_at: '2026-03-03T06:00:00Z',
  },
] satisfies EmailStatsItem[];

export const MOCK_QUEUE_STATUS = { paused: false } satisfies QueueStatus;
export const MOCK_QUEUE_STATUS_PAUSED = { paused: true } satisfies QueueStatus;

export const MOCK_JOBS_EMPTY = {
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
} satisfies Paginated<JobStatusResponse>;

function makeJob(overrides: Partial<JobStatusResponse> & Pick<JobStatusResponse, 'id' | 'original_filename' | 'status'>): JobStatusResponse {
  return {
    file_type: 'pdf',
    file_size_bytes: 120_000,
    source: 'UPLOAD',
    error_message: null,
    error_code: null,
    retry_count: 0,
    document_id: null,
    created_at: '2026-03-03T10:00:00Z',
    updated_at: '2026-03-03T10:00:00Z',
    ...overrides,
  };
}

export const MOCK_JOBS_ACTIVE = {
  items: [
    makeJob({ id: 'job-10', original_filename: 'rechnung-2026.pdf', status: 'PROCESSING' }),
    makeJob({ id: 'job-11', original_filename: 'quittung-cafe.jpg', status: 'PENDING', file_type: 'jpg' }),
  ],
  total: 2,
  page: 1,
  page_size: 20,
} satisfies Paginated<JobStatusResponse>;

export const MOCK_JOBS_FAILED = {
  items: [
    makeJob({
      id: 'job-20',
      original_filename: 'corrupt-file.pdf',
      status: 'FAILED',
      error_message: 'OCR fehlgeschlagen: Datei ist beschädigt',
      retry_count: 1,
      created_at: '2026-03-02T15:00:00Z',
      updated_at: '2026-03-02T15:00:00Z',
    }),
  ],
  total: 1,
  page: 1,
  page_size: 20,
} satisfies Paginated<JobStatusResponse>;

const TAG_NEBENKOSTEN = { id: 1, name: 'nebenkosten', category: null, is_auto_generated: true };
const TAG_ELEKTRONIK = { id: 2, name: 'elektronik', category: null, is_auto_generated: true };
const TAG_STROM = { id: 3, name: 'strom', category: null, is_auto_generated: false };

function makeListItem(overrides: Partial<DocumentListItem> & Pick<DocumentListItem, 'id' | 'title'>): DocumentListItem {
  return {
    original_filename: 'dokument.pdf',
    file_type: 'pdf',
    file_size_bytes: 120_000,
    document_type: 'SONSTIGES',
    document_date: null,
    amount: null,
    currency: 'EUR',
    issuer: null,
    tax_relevant: false,
    status: 'ACTIVE',
    review_status: 'OK',
    ai_confidence: 0.9,
    filing_scope_id: 'scope-privat',
    filing_scope_name: 'Privat',
    created_at: '2026-01-16T10:30:00Z',
    tags: [],
    ...overrides,
  };
}

export const MOCK_DOCUMENTS = {
  items: [
    makeListItem({
      id: 'doc-1',
      title: 'Stromrechnung Januar 2026',
      original_filename: 'stromrechnung_jan.pdf',
      document_type: 'RECHNUNG',
      document_date: '2026-01-15',
      amount: 89.5,
      issuer: 'Stadtwerke München',
      tax_relevant: true,
      tags: [TAG_NEBENKOSTEN],
      created_at: '2026-01-16T10:30:00Z',
    }),
    makeListItem({
      id: 'doc-2',
      title: 'Kaufvertrag Laptop Dell XPS',
      original_filename: 'kaufvertrag_laptop.pdf',
      document_type: 'KAUFVERTRAG',
      document_date: '2026-01-10',
      amount: 1299.0,
      issuer: 'MediaMarkt',
      tags: [TAG_ELEKTRONIK],
      created_at: '2026-01-11T14:20:00Z',
    }),
    makeListItem({
      id: 'doc-3',
      title: 'Lohnabrechnung Dezember 2025',
      original_filename: 'lohn_dez_2025.pdf',
      document_type: 'LOHNABRECHNUNG',
      document_date: '2025-12-31',
      amount: 3500.0,
      issuer: 'Arbeitgeber GmbH',
      tax_relevant: true,
      created_at: '2026-01-02T09:00:00Z',
    }),
  ],
  total: 3,
  page: 1,
  page_size: 20,
} satisfies Paginated<DocumentListItem>;

export const MOCK_DOCUMENTS_EMPTY = {
  items: [],
  total: 0,
  page: 1,
  page_size: 20,
} satisfies Paginated<DocumentListItem>;

export const MOCK_DOCUMENT_DETAIL = {
  ...MOCK_DOCUMENTS.items[0],
  recipient: 'Max Mustermann',
  reference_number: 'RE-2026-001',
  summary: 'Monatliche Stromrechnung für Januar 2026, Verbrauch 280 kWh.',
  ocr_text: 'Stadtwerke München Stromrechnung Januar 2026 Betrag 89,50 EUR',
  ocr_confidence: 0.92,
  ai_confidence: 0.92,
  tax_year: 2026,
  tax_category: 'Haushaltsnahe_Dienstleistungen',
  tags: [TAG_NEBENKOSTEN, TAG_STROM],
  warranty_info: null,
  review_questions: [],
  updated_at: '2026-01-16T10:30:00Z',
} satisfies DocumentResponse;

export const MOCK_DOCUMENT_WITH_WARRANTY = {
  ...MOCK_DOCUMENT_DETAIL,
  id: 'doc-2',
  title: 'Kaufvertrag Laptop Dell XPS',
  document_type: 'KAUFVERTRAG',
  tax_relevant: false,
  tax_category: null,
  warranty_info: {
    id: 'war-1',
    product_name: 'Dell XPS 15',
    product_category: 'Elektronik',
    purchase_date: '2026-01-10',
    warranty_end_date: '2028-01-10',
    warranty_type: 'MANUFACTURER',
    warranty_duration_months: 24,
    retailer: 'MediaMarkt',
    is_expired: false,
    reminder_sent: false,
    notes: null,
  },
} satisfies DocumentResponse;

const QUESTION_DOC_TYPE = {
  id: 'q-1',
  question: 'Um welchen Dokumenttyp handelt es sich?',
  question_type: 'classification',
  explanation: 'Die KI konnte den Dokumenttyp nicht eindeutig bestimmen.',
  suggested_answers: 'Rechnung|Quittung|Kaufvertrag',
  field_affected: 'document_type',
  answer: null,
  is_answered: false,
  priority: 2,
  created_at: '2026-02-15T10:00:00Z',
  answered_at: null,
};
const QUESTION_AMOUNT = {
  id: 'q-2',
  question: 'Wie lautet der Gesamtbetrag?',
  question_type: 'extraction',
  explanation: 'Der Betrag war im OCR-Text nicht eindeutig erkennbar.',
  suggested_answers: '45,99 EUR|459,90 EUR',
  field_affected: 'amount',
  answer: null,
  is_answered: false,
  priority: 1,
  created_at: '2026-02-15T10:00:00Z',
  answered_at: null,
};

export const MOCK_DOCUMENT_WITH_QUESTIONS = {
  ...MOCK_DOCUMENT_DETAIL,
  id: 'doc-4',
  title: 'Unklares Dokument',
  document_type: 'SONSTIGES',
  amount: null,
  issuer: null,
  ocr_confidence: 0.45,
  ai_confidence: 0.45,
  review_status: 'NEEDS_REVIEW',
  review_questions: [QUESTION_DOC_TYPE, QUESTION_AMOUNT],
} satisfies DocumentResponse;

export const MOCK_TAGS = [
  { ...TAG_NEBENKOSTEN },
  { ...TAG_ELEKTRONIK },
  { ...TAG_STROM },
  { id: 4, name: 'versicherung', category: null, is_auto_generated: false },
] satisfies TagListItem[];

export const MOCK_FILING_SCOPES = [
  {
    id: 'scope-privat',
    name: 'Privat',
    slug: 'privat',
    description: 'Private Dokumente',
    keywords: ['privat', 'haushalt'],
    is_default: true,
    color: '#3B82F6',
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 'scope-praxis',
    name: 'Praxis',
    slug: 'praxis',
    description: 'Dokumente der Arztpraxis',
    keywords: ['praxis', 'patient', 'behandlung'],
    is_default: false,
    color: '#10B981',
    created_at: '2026-01-01T00:00:00Z',
  },
] satisfies FilingScopeResponse[];

export const MOCK_UPLOAD_RESPONSE = {
  uploaded: [
    { job_id: 'job-100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'Hochgeladen' },
  ],
  rejected: [],
} satisfies UploadResponse;

export const MOCK_UPLOAD_RESPONSE_MULTI = {
  uploaded: [
    { job_id: 'job-100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'Hochgeladen' },
    { job_id: 'job-101', original_filename: 'scan.png', status: 'PENDING', message: 'Hochgeladen' },
  ],
  rejected: [],
} satisfies UploadResponse;

export const MOCK_JOB_STATUS_PENDING = makeJob({ id: 'job-100', original_filename: 'rechnung.pdf', status: 'PENDING' });
export const MOCK_JOB_STATUS_PROCESSING = makeJob({ id: 'job-100', original_filename: 'rechnung.pdf', status: 'PROCESSING' });
export const MOCK_JOB_STATUS_COMPLETED = makeJob({
  id: 'job-100', original_filename: 'rechnung.pdf', status: 'COMPLETED', document_id: 'doc-5',
});
export const MOCK_JOB_STATUS_NEEDS_REVIEW = makeJob({
  id: 'job-100', original_filename: 'rechnung.pdf', status: 'NEEDS_REVIEW', document_id: 'doc-6',
});

export const MOCK_REVIEW_PENDING = {
  documents: [
    {
      id: 'doc-4',
      title: 'Unklares Dokument',
      document_type: 'SONSTIGES',
      has_thumbnail: false,
      file_type: 'pdf',
      ai_confidence: 0.45,
      created_at: '2026-02-15T10:00:00Z',
      open_questions: 2,
      total_questions: 2,
    },
    {
      id: 'doc-5',
      title: 'Arztrechnung',
      document_type: 'ARZTRECHNUNG',
      has_thumbnail: false,
      file_type: 'pdf',
      ai_confidence: 0.65,
      created_at: '2026-02-20T10:00:00Z',
      open_questions: 1,
      total_questions: 1,
    },
  ],
  total: 2,
} satisfies ReviewPendingResponse;

/** Review-Detail (GET /api/review/documents/{id}) fuer ein Dokument mit Fragen. */
export function makeReviewDetail(
  doc: DocumentResponse = MOCK_DOCUMENT_WITH_QUESTIONS,
): ReviewDetailResponse {
  return {
    id: doc.id,
    title: doc.title,
    document_type: doc.document_type,
    file_type: doc.file_type,
    ai_confidence: doc.ai_confidence,
    summary: doc.summary,
    ocr_text: doc.ocr_text,
    questions: doc.review_questions.map(({ created_at: _c, answered_at: _a, ...q }) => q),
    confident_fields: {
      title: doc.title,
      document_type: doc.document_type,
      document_date: doc.document_date,
      amount: doc.amount,
      currency: doc.currency,
      issuer: doc.issuer,
      filing_scope: doc.filing_scope_name,
    },
  };
}

export const MOCK_REVIEW_DETAIL = makeReviewDetail();

export const MOCK_REVIEW_STATS = {
  open_documents: 2,
  open_questions: 3,
  answered_questions: 40,
} satisfies ReviewStats;

// Treffer-Markierung wie im Backend (search_service.HIGHLIGHT_START/END).
export const HL_START = '\u0002';
export const HL_END = '\u0003';

export const MOCK_SEARCH_RESULTS = {
  results: [
    {
      id: 'doc-1',
      title: 'Stromrechnung Januar 2026',
      document_type: 'RECHNUNG',
      document_date: '2026-01-15',
      amount: 89.5,
      currency: 'EUR',
      issuer: 'Stadtwerke München',
      tags: ['nebenkosten'],
      tax_relevant: true,
      highlight: `Monatliche ${HL_START}Stromrechnung${HL_END} für Januar <b>2026</b>`,
      relevance_score: 2.5,
      ai_confidence: 0.92,
      created_at: '2026-01-16T10:30:00Z',
    },
    {
      id: 'doc-3',
      title: 'Lohnabrechnung Dezember 2025',
      document_type: 'LOHNABRECHNUNG',
      document_date: '2025-12-31',
      amount: 3500.0,
      currency: 'EUR',
      issuer: 'Arbeitgeber GmbH',
      tags: [],
      tax_relevant: true,
      highlight: `Dezember ${HL_START}Abrechnung${HL_END} Brutto/Netto`,
      relevance_score: 1.8,
      ai_confidence: 0.88,
      created_at: '2026-01-02T09:00:00Z',
    },
  ],
  total: 2,
  page: 1,
  page_size: 25,
  facets: {
    document_types: { RECHNUNG: 1, LOHNABRECHNUNG: 1 },
    years: { '2026': 1, '2025': 1 },
    top_issuers: { 'Stadtwerke München': 1, 'Arbeitgeber GmbH': 1 },
    filing_scopes: [{ id: 'scope-privat', name: 'Privat', count: 2 }],
  },
} satisfies SearchResponse;

export const MOCK_SEARCH_SUGGESTIONS = {
  suggestions: ['Stromrechnung', 'Strom', 'Stadtwerke'],
} satisfies SuggestResponse;

export const MOCK_SAVED_SEARCHES = [
  { id: 'saved-1', name: 'Rechnungen 2026', query_params: { q: 'rechnung', date_from: '2026-01-01' }, created_at: '2026-01-05T08:00:00Z' },
  { id: 'saved-2', name: 'Alle Versicherungen', query_params: { q: 'versicherung' }, created_at: '2026-01-06T08:00:00Z' },
] satisfies SavedSearchResponse[];

export const MOCK_TAX_YEARS = {
  years: [2026, 2025, 2024],
} satisfies TaxYears;

export const MOCK_TAX_SUMMARY = {
  year: 2026,
  total_documents: 12,
  total_amount: 4580.5,
  categories: [
    { category: 'Werbungskosten', label: 'Werbungskosten', document_count: 5, total_amount: 2100.0 },
    { category: 'Haushaltsnahe_Dienstleistungen', label: 'Haushaltsnahe Dienstleistungen', document_count: 3, total_amount: 1280.5 },
    { category: 'Vorsorgeaufwendungen', label: 'Vorsorgeaufwendungen', document_count: 4, total_amount: 1200.0 },
  ],
  non_eur_documents: [],
  warnings: [],
} satisfies TaxYearSummary;

export const MOCK_TAX_SUMMARY_EMPTY = {
  year: 2026,
  total_documents: 0,
  total_amount: 0,
  categories: [],
  non_eur_documents: [],
  warnings: [],
} satisfies TaxYearSummary;

export const MOCK_TAX_VALIDATION = {
  year: 2026,
  total_documents: 12,
  warnings: [
    'Dokument "Handwerkerrechnung Bad" hat keinen Betrag',
    '2 Dokumente ohne Steuerkategorie',
  ],
  ready: true,
} satisfies TaxExportValidation;

export const MOCK_TAX_VALIDATION_EMPTY = {
  year: 2026,
  total_documents: 0,
  warnings: [],
  ready: false,
} satisfies TaxExportValidation;

export const MOCK_WARRANTIES = [
  {
    id: 'war-1',
    document_id: 'doc-2',
    product_name: 'Dell XPS 15',
    product_category: 'Elektronik',
    purchase_date: '2026-01-10',
    warranty_end_date: '2028-01-10',
    warranty_type: 'MANUFACTURER',
    warranty_duration_months: 24,
    retailer: 'MediaMarkt',
    is_expired: false,
    reminder_sent: false,
    notes: null,
    days_remaining: 675,
    document_title: 'Kaufvertrag Laptop',
  },
  {
    id: 'war-2',
    document_id: 'doc-7',
    product_name: 'Waschmaschine Bosch',
    product_category: null,
    purchase_date: '2024-06-15',
    warranty_end_date: '2026-06-15',
    warranty_type: 'LEGAL',
    warranty_duration_months: 24,
    retailer: 'Saturn',
    is_expired: false,
    reminder_sent: false,
    notes: null,
    days_remaining: 60,
    document_title: 'Kaufvertrag Waschmaschine',
  },
  {
    id: 'war-3',
    document_id: 'doc-8',
    product_name: 'Staubsauger Dyson',
    product_category: null,
    purchase_date: '2022-03-01',
    warranty_end_date: '2024-03-01',
    warranty_type: 'MANUFACTURER',
    warranty_duration_months: 24,
    retailer: 'Amazon',
    is_expired: true,
    reminder_sent: true,
    notes: null,
    days_remaining: 0,
    document_title: 'Kaufvertrag Staubsauger',
  },
] satisfies WarrantyListItem[];

export const MOCK_WARRANTY_STATS = {
  total: 3,
  active: 1,
  expiring_soon: 1,
  expired: 1,
} satisfies WarrantyStats;

const CHAT_SOURCE = {
  document_id: 'doc-1',
  title: 'Stromrechnung Januar 2026',
  document_type: 'RECHNUNG',
  document_date: '2026-01-15',
  issuer: 'Stadtwerke München',
};

export const MOCK_CHAT_RESPONSE = {
  answer: 'Ihre Stromrechnung vom Januar 2026 beträgt 89,50 EUR und wurde von den Stadtwerken München ausgestellt.',
  sources: [CHAT_SOURCE],
  chunks_found: 3,
} satisfies ChatResponse;

export const MOCK_CHAT_HISTORY = {
  messages: [
    {
      id: 'msg-1',
      role: 'user',
      content: 'Was war meine letzte Stromrechnung?',
      sources: [],
      filing_scope_id: null,
      created_at: '2026-03-01T10:00:00Z',
    },
    {
      id: 'msg-2',
      role: 'assistant',
      content: 'Ihre letzte Stromrechnung beträgt 89,50 EUR.',
      sources: [CHAT_SOURCE],
      filing_scope_id: null,
      created_at: '2026-03-01T10:00:01Z',
    },
  ],
  total: 2,
} satisfies ChatHistoryResponse;

export const MOCK_CHAT_HISTORY_EMPTY = { messages: [], total: 0 } satisfies ChatHistoryResponse;

export const MOCK_NOTIFICATIONS = [
  {
    id: 'notif-1',
    type: 'WARRANTY_EXPIRING',
    title: 'Garantie läuft bald ab',
    message: 'Die Garantie für "Waschmaschine Bosch" läuft in 30 Tagen ab.',
    is_read: false,
    created_at: '2026-03-01T08:00:00Z',
    document_id: 'doc-7',
  },
  {
    id: 'notif-2',
    type: 'REVIEW_NEEDED',
    title: 'Prüfung nötig',
    message: 'Dokument "Arztrechnung" braucht eine Prüfung.',
    is_read: true,
    created_at: '2026-02-28T15:00:00Z',
    document_id: 'doc-5',
  },
] satisfies NotificationResponse[];

export const MOCK_NOTIFICATION_COUNT = { unread: 1 } satisfies NotificationCount;

export const MOCK_BACKUPS = {
  backups: [
    {
      filename: 'backup_db_20260301_030000.zip',
      size_bytes: 1_258_291,                // 1,2 MB
      created_at: '2026-03-01T03:00:00',
      is_full: false,
    },
  ],
} satisfies BackupListResponse;

export const MOCK_SYSTEM_SETTINGS = {
  watch_dir: '/app/data/watch',
  export_dir: '/app/data/export',
  watch_dir_host: '',
  export_dir_host: '',
  restart_required: false,
  deployment_mode: 'docker',
} satisfies FolderSettings;

// Hintergrund-Vorgang (GET /api/tasks/{id}, 202-Antwort der Start-Endpoints).
export function makeMockTask(overrides: Partial<TaskResponse> = {}): TaskResponse {
  const status = overrides.status ?? 'PENDING';
  return {
    id: 'task-1',
    kind: 'demo',
    key: null,
    status,
    progress_percent: null,
    progress_text: null,
    result: null,
    error: null,
    created_at: '2026-10-17T10:00:00Z',
    started_at: status === 'PENDING' ? null : '2026-10-17T10:00:01Z',
    finished_at: status === 'SUCCEEDED' || status === 'FAILED' ? '2026-10-17T10:00:05Z' : null,
    done: status === 'SUCCEEDED' || status === 'FAILED',
    ...overrides,
  };
}

export const MOCK_EMAIL_ACCOUNTS = [
  {
    id: 1,
    name: 'Rechnungs-Postfach',
    imap_host: 'imap.example.org',
    imap_port: 993,
    use_ssl: true,
    username: 'rechnungen@example.org',
    folder_inbox: 'INBOX',
    folder_processed: 'Zettelwirtschaft/Verarbeitet',
    schedule_type: 'CRON',
    cron_expression: '0 */6 * * *',
    is_active: true,
    last_checked_at: '2026-03-03T06:00:00Z',
    last_error: null,
    filing_scope_id: 'scope-privat',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-03-03T06:00:00Z',
  },
] satisfies EmailAccountResponse[];

export const MOCK_EMAIL_SETTINGS = {
  timezone: 'Europe/Berlin',
  unseen_only: true,
  max_per_run: 50,
  idle_interval_minutes: 5,
} satisfies EmailSettingsInfo;

export const MOCK_EMAIL_HISTORY = [
  {
    id: 11,
    message_id: '<a1@example.org>',
    subject: 'Ihre Rechnung März',
    sender: 'rechnung@stadtwerke.example',
    received_at: '2026-03-03T05:50:00Z',
    status: 'RELEVANT',
    relevance_reason: 'Rechnung im Anhang',
    processing_job_id: 'job-55',
    created_at: '2026-03-03T06:00:00Z',
  },
  {
    id: 12,
    message_id: '<a2@example.org>',
    subject: 'Newsletter',
    sender: 'news@shop.example',
    received_at: '2026-03-03T05:40:00Z',
    status: 'IRRELEVANT',
    relevance_reason: 'Werbung',
    processing_job_id: null,
    created_at: '2026-03-03T06:00:00Z',
  },
] satisfies ProcessedEmailResponse[];

// ============================================================
// Mock Setup Functions
// ============================================================

/** Pfade, die der PIN-Middleware zufolge ohne Sitzung erreichbar sind. */
function isPublicApiPath(pathname: string): boolean {
  return pathname.startsWith('/api/health') || pathname.startsWith('/api/auth');
}

const contextsWithFallback = new WeakSet<BrowserContext>();

/**
 * Catch-all fuer ungemockte API-Aufrufe: antwortet mit 501, statt den Request
 * an den Preview-Proxy (und damit an ein ggf. laufendes echtes Backend)
 * durchzulassen. Liegt auf dem BrowserContext, damit jede page.route() — auch
 * spaeter registrierte — Vorrang hat; route.fallback() in einem page-Handler
 * landet hier. Wird von setupBaseMocks/setupPinAuthMocks installiert.
 */
export async function installApiFallback(page: Page) {
  const context = page.context();
  if (contextsWithFallback.has(context)) return;
  contextsWithFallback.add(context);
  await context.route('**/api/**', (route) => {
    const req = route.request();
    const { pathname } = new URL(req.url());
    console.warn(`[e2e] Nicht gemockter API-Aufruf: ${req.method()} ${pathname}`);
    return route.fulfill({
      status: 501,
      json: { detail: `E2E: ${req.method()} ${pathname} ist nicht gemockt` },
    });
  });
}

const ONBOARDING_KEY = 'zw_onboarding_v1_done';
const ONBOARDING_MANAGED_FLAG = 'e2e_onboarding_managed';

/**
 * Onboarding-Tour als gesehen markieren (sonst verdeckt das Modal die UI) —
 * ausser ein Test hat die Tour per showOnboardingTour() selbst uebernommen.
 */
async function markOnboardingSeen(page: Page) {
  await page.addInitScript(({ key, flag }) => {
    try {
      if (sessionStorage.getItem(flag) !== '1') localStorage.setItem(key, '1');
    } catch { /* ignore */ }
  }, { key: ONBOARDING_KEY, flag: ONBOARDING_MANAGED_FLAG });
}

/**
 * Tour beim ersten Laden als ungesehen markieren. Danach bleibt der Zustand
 * der App ueberlassen (auch ueber Reloads), damit Tests das Merken pruefen
 * koennen. Nach setupBaseMocks/setupPinAuthMocks aufrufen.
 */
export async function showOnboardingTour(page: Page) {
  await page.addInitScript(({ key, flag }) => {
    try {
      if (sessionStorage.getItem(flag) === '1') return;
      sessionStorage.setItem(flag, '1');
      localStorage.removeItem(key);
    } catch { /* ignore */ }
  }, { key: ONBOARDING_KEY, flag: ONBOARDING_MANAGED_FLAG });
}

/**
 * Set up base mocks needed for almost every page:
 * - API-Catch-all (501), Onboarding als gesehen
 * - /api/health, /api/auth/status (no PIN)
 * - /api/notifications(/count), /api/filing-scopes (GET), /api/review/stats
 * - /api/system/health, /api/system/update-check, /api/tasks?...
 */
export async function setupBaseMocks(page: Page) {
  await installApiFallback(page);
  // Onboarding-Tests entfernen den Key per eigenem addInitScript wieder.
  await markOnboardingSeen(page);
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: MOCK_HEALTH })
  );
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: MOCK_AUTH_STATUS })
  );
  await page.route('**/api/notifications/count', (route) =>
    route.fulfill({ json: MOCK_NOTIFICATION_COUNT })
  );
  await page.route('**/api/notifications?*', (route) =>
    route.fulfill({ json: MOCK_NOTIFICATIONS })
  );
  await page.route('**/api/filing-scopes', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_FILING_SCOPES });
    }
    return route.fallback();
  });
  await page.route('**/api/system/health', (route) =>
    route.fulfill({ json: MOCK_SYSTEM_HEALTH })
  );
  // Badge "Zu prüfen" in Sidebar/Mehr-Menue (useReviewCount).
  await page.route('**/api/review/stats', (route) =>
    route.fulfill({ json: MOCK_REVIEW_STATS })
  );
  // Keine laufenden Hintergrund-Vorgaenge (useBackgroundTask.resume beim Mount).
  await page.route('**/api/tasks?*', (route) => route.fulfill({ json: [] }));
  // Opt-in Update-Pruefung: standardmaessig deaktiviert. PUT echo't den Schalter.
  // Glob mit ** am Ende, damit auch der ?force=-Query-Param matcht.
  await page.route('**/api/system/update-check**', (route) => {
    if (route.request().method() === 'PUT') {
      const enabled = (route.request().postDataJSON() as { enabled?: boolean })?.enabled ?? false;
      return route.fulfill({
        json: {
          enabled,
          current_version: '1.4.0',
          latest_version: enabled ? '1.4.0' : null,
          update_available: false,
        },
      });
    }
    return route.fulfill({
      json: { enabled: false, current_version: '1.4.0', latest_version: null, update_available: false },
    });
  });
}

export interface PinSession {
  /** true, solange die (simulierte) Server-Sitzung gueltig ist. */
  readonly authenticated: boolean;
  /** Sitzung serverseitig beenden: ab jetzt antwortet jede geschuetzte Route mit 401. */
  expire(): void;
  /** Alle Login-Versuche (POST /api/auth/login) mit dem gesendeten PIN. */
  readonly loginAttempts: string[];
  /** Pfade der geschuetzten Requests, die mangels Sitzung 401 bekamen. */
  readonly blockedRequests: string[];
  /** Anzahl der Abmeldungen (POST /api/auth/logout). */
  readonly logoutCalls: number;
}

export interface PinAuthOptions {
  /** Richtiger PIN fuer den Login-Mock (Standard '1234'). */
  pin?: string;
  /** Sitzung von Anfang an gueltig (schon angemeldet). */
  authenticated?: boolean;
  /**
   * Datenmocks (z.B. setupDashboardMocks). Sie werden VOR dem 401-Gate
   * registriert, damit das Gate zuerst greift: ohne gueltige Sitzung liefert
   * jede geschuetzte /api/*-Route 401 — wie die PIN-Middleware im Backend.
   */
  dataMocks?: (page: Page) => Promise<void>;
}

/**
 * PIN-Schutz aktiv. Bildet die Backend-Semantik nach: /api/health und
 * /api/auth/* sind oeffentlich, alles andere liefert ohne Sitzung 401
 * ("Nicht authentifiziert"). Login mit dem richtigen PIN startet die Sitzung,
 * auth/status spiegelt sie wider.
 */
export async function setupPinAuthMocks(page: Page, options: PinAuthOptions = {}): Promise<PinSession> {
  const pin = options.pin ?? '1234';
  let authenticated = options.authenticated ?? false;
  let logoutCalls = 0;
  const loginAttempts: string[] = [];
  const blockedRequests: string[] = [];

  await installApiFallback(page);
  await markOnboardingSeen(page);
  if (options.dataMocks) await options.dataMocks(page);

  // Nach den Datenmocks registrieren: setupBaseMocks setzt sonst "PIN aus".
  await page.route('**/api/health', (route) =>
    route.fulfill({ json: MOCK_HEALTH })
  );
  await page.route('**/api/auth/status', (route) =>
    route.fulfill({ json: { pin_enabled: true, authenticated, pin_warning: false } satisfies AuthStatus })
  );
  await page.route('**/api/auth/login', (route) => {
    const body = route.request().postDataJSON() as { pin?: string };
    loginAttempts.push(body?.pin ?? '');
    if (body?.pin === pin) {
      authenticated = true;
      return route.fulfill({ json: { success: true } });
    }
    return route.fulfill({ status: 401, json: { success: false, detail: 'Falscher PIN' } });
  });
  await page.route('**/api/auth/logout', (route) => {
    authenticated = false;
    logoutCalls += 1;
    return route.fulfill({ json: { success: true } });
  });
  // 401-Gate: zuletzt registriert -> laeuft vor allen anderen page-Routen.
  await page.route('**/api/**', (route: Route) => {
    const { pathname } = new URL(route.request().url());
    if (isPublicApiPath(pathname) || authenticated) return route.fallback();
    blockedRequests.push(pathname);
    return route.fulfill({ status: 401, json: { detail: 'Nicht authentifiziert' } });
  });

  return {
    get authenticated() { return authenticated; },
    expire() { authenticated = false; },
    loginAttempts,
    blockedRequests,
    get logoutCalls() { return logoutCalls; },
  };
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
  await page.route('**/api/documents?*', (route) =>
    route.fulfill({ json: MOCK_DOCUMENTS })
  );
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
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_DOCUMENTS });
    }
    return route.fallback();
  });
  await page.route('**/api/tags', (route) =>
    route.fulfill({ json: MOCK_TAGS })
  );
}

/** Set up document detail mocks */
export async function setupDocumentDetailMocks(page: Page, doc: { id: string } & Record<string, unknown> = MOCK_DOCUMENT_DETAIL) {
  await setupBaseMocks(page);
  await page.route(`**/api/documents/${doc.id}`, (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: doc });
    }
    if (route.request().method() === 'PATCH') {
      return route.fulfill({ json: { ...doc, ...JSON.parse(route.request().postData() || '{}') } });
    }
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { message: 'Dokument gelöscht' } });
    }
    return route.fallback();
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
  // POST /documents/{id}/tags liefert das aktualisierte Dokument (DocumentResponse).
  await page.route(`**/api/documents/${doc.id}/tags`, (route) => {
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      const tags = Array.isArray(doc.tags) ? doc.tags : [];
      const name = String(body.name ?? '').trim().toLowerCase();
      return route.fulfill({ json: { ...doc, tags: [...tags, { id: 99, name, category: null, is_auto_generated: false }] } });
    }
    return route.fallback();
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
export async function setupReviewMocks(
  page: Page,
  details: DocumentResponse[] = [MOCK_DOCUMENT_WITH_QUESTIONS, { ...MOCK_DOCUMENT_WITH_QUESTIONS, id: 'doc-5', title: 'Arztrechnung', document_type: 'ARZTRECHNUNG', review_questions: [QUESTION_AMOUNT] }],
) {
  await setupBaseMocks(page);
  await page.route('**/api/review/pending', (route) =>
    route.fulfill({ json: MOCK_REVIEW_PENDING })
  );
  await page.route('**/api/review/documents/*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const id = new URL(route.request().url()).pathname.split('/').pop();
    const doc = details.find((d) => d.id === id);
    if (!doc) return route.fulfill({ status: 404, json: { detail: 'Dokument nicht gefunden' } });
    return route.fulfill({ json: makeReviewDetail(doc) });
  });
  await page.route('**/api/review/documents/*/approve', (route) =>
    route.fulfill({ json: { ok: true, review_status: 'REVIEWED' } })
  );
  await page.route('**/api/review/documents/*/accept', (route) =>
    route.fulfill({ json: { ok: true, review_status: 'REVIEWED' } })
  );
  await page.route('**/api/review/questions/*/answer', (route) =>
    route.fulfill({ json: { ok: true, all_answered: false } })
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
  await page.route('**/api/search/suggest*', (route) =>
    route.fulfill({ json: MOCK_SEARCH_SUGGESTIONS })
  );
  await page.route('**/api/saved-searches', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_SAVED_SEARCHES });
    }
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({
        status: 201,
        json: { id: 'saved-3', name: body.name, query_params: body.query_params, created_at: '2026-03-03T10:00:00Z' },
      });
    }
    return route.fallback();
  });
  await page.route('**/api/saved-searches/*', (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ status: 204, body: '' });
    }
    return route.fallback();
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
      headers: { 'content-disposition': 'attachment; filename="steuerpaket_2026.zip"' },
      body: Buffer.from('fake-zip-content'),
    })
  );
}

/** Set up warranty mocks */
export async function setupWarrantyMocks(page: Page) {
  await setupBaseMocks(page);
  await page.route('**/api/warranties?*', (route) =>
    route.fulfill({ json: MOCK_WARRANTIES })
  );
  await page.route('**/api/warranties', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_WARRANTIES });
    }
    return route.fallback();
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
    return route.fallback();
  });
  await page.route('**/api/chat/history*', (route) => {
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_CHAT_HISTORY });
    }
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { message: 'Verlauf gelöscht' } });
    }
    return route.fallback();
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
    return route.fallback();
  });
  await page.route('**/api/system/backups', (route) =>
    route.fulfill({ json: MOCK_BACKUPS })
  );
  // Backup laeuft als Hintergrund-Vorgang: 202 + TaskResponse, Status per Polling.
  await page.route('**/api/system/backup*', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    return route.fulfill({ status: 202, json: makeMockTask({ id: 'task-backup', kind: 'backup', key: 'global', status: 'RUNNING' }) });
  });
  await mockBackgroundTaskPolling(page, 'task-backup', [
    { kind: 'backup', key: 'global', status: 'SUCCEEDED', result: { filename: 'backup_db_20260303_100000.zip', is_full: false, size_bytes: 1024, message: 'Backup erstellt' } },
  ]);
  await page.route('**/api/system/maintenance/*', (route) =>
    route.fulfill({ json: { message: 'Wartung ausgeführt' } })
  );
  await page.route('**/api/filing-scopes', (route) => {
    if (route.request().method() === 'POST') {
      const body = JSON.parse(route.request().postData() || '{}');
      return route.fulfill({
        status: 201,
        json: { id: 'scope-neu', description: null, keywords: [], is_default: false, color: null, created_at: '2026-03-03T10:00:00Z', ...body, slug: String(body.name ?? '').toLowerCase() },
      });
    }
    return route.fulfill({ json: MOCK_FILING_SCOPES });
  });
  await page.route('**/api/filing-scopes/*', (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { message: 'Gelöscht' } });
    }
    if (route.request().method() === 'PATCH') {
      return route.fulfill({ json: { ...MOCK_FILING_SCOPES[0], ...JSON.parse(route.request().postData() || '{}') } });
    }
    return route.fallback();
  });
  await setupEmailMocks(page);
}

/**
 * E-Mail-Konten (CRUD, Test, Abruf als Hintergrund-Vorgang, Verlauf). Die
 * Konten-Liste ist zustandsbehaftet, damit Anlegen/Bearbeiten/Loeschen in der
 * nachgeladenen Liste sichtbar werden. Liefert die Liste der empfangenen
 * Schreib-Requests zur Pruefung.
 */
export async function setupEmailMocks(page: Page, initial: EmailAccountResponse[] = MOCK_EMAIL_ACCOUNTS) {
  const accounts = initial.map((a) => ({ ...a }));
  const writes: { method: string; path: string; body: Record<string, unknown> | null }[] = [];
  const record = (route: Route) => {
    const req = route.request();
    writes.push({ method: req.method(), path: new URL(req.url()).pathname, body: req.postDataJSON() as Record<string, unknown> | null });
  };

  await page.route('**/api/email/settings', (route) => route.fulfill({ json: MOCK_EMAIL_SETTINGS }));
  await page.route('**/api/email/stats', (route) => route.fulfill({ json: MOCK_EMAIL_STATS }));
  await page.route('**/api/email/accounts', (route) => {
    const req = route.request();
    if (req.method() === 'GET') return route.fulfill({ json: accounts });
    if (req.method() === 'POST') {
      record(route);
      const body = req.postDataJSON() as Partial<EmailAccountResponse> & { password?: string };
      const { password: _pw, ...rest } = body;
      const created: EmailAccountResponse = {
        ...MOCK_EMAIL_ACCOUNTS[0],
        schedule_type: 'MANUAL',
        cron_expression: null,
        last_checked_at: null,
        last_error: null,
        ...rest,
        id: 100 + accounts.length,
      };
      accounts.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    return route.fallback();
  });
  await page.route(/\/api\/email\/accounts\/\d+$/, (route) => {
    const req = route.request();
    const id = Number(new URL(req.url()).pathname.split('/').pop());
    const idx = accounts.findIndex((a) => a.id === id);
    if (idx < 0) return route.fulfill({ status: 404, json: { detail: 'Konto nicht gefunden' } });
    if (req.method() === 'PUT') {
      record(route);
      const { password: _pw, ...rest } = req.postDataJSON() as Record<string, unknown>;
      accounts[idx] = { ...accounts[idx], ...rest } as EmailAccountResponse;
      return route.fulfill({ json: accounts[idx] });
    }
    if (req.method() === 'DELETE') {
      record(route);
      accounts.splice(idx, 1);
      return route.fulfill({ status: 204, body: '' });
    }
    return route.fallback();
  });
  await page.route('**/api/email/accounts/*/test', (route) => {
    record(route);
    return route.fulfill({ json: { success: true, error: null, warning: null } });
  });
  await page.route('**/api/email/accounts/*/fetch', (route) => {
    record(route);
    const id = new URL(route.request().url()).pathname.split('/')[4];
    return route.fulfill({ status: 202, json: makeMockTask({ id: 'task-email', kind: 'email_fetch', key: id, status: 'RUNNING' }) });
  });
  await page.route('**/api/email/accounts/*/history*', (route) =>
    route.fulfill({ json: MOCK_EMAIL_HISTORY })
  );
  return { accounts, writes };
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
    if (route.request().method() === 'GET') {
      return route.fulfill({ json: MOCK_DOCUMENTS });
    }
    return route.fallback();
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

/**
 * Benachrichtigungen (Glocke im Header): Liste + Zaehler + Gelesen-Markierung.
 * Zustandsbehaftet: mark-read/read-all senken den Zaehler.
 */
export async function setupNotificationMocks(page: Page, initial: NotificationResponse[] = MOCK_NOTIFICATIONS) {
  const notifications = initial.map((n) => ({ ...n }));
  const unread = () => notifications.filter((n) => !n.is_read).length;
  await page.route('**/api/notifications/count', (route) =>
    route.fulfill({ json: { unread: unread() } })
  );
  await page.route(/\/api\/notifications(\?.*)?$/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const onlyUnread = new URL(route.request().url()).searchParams.get('unread_only') === 'true';
    return route.fulfill({ json: onlyUnread ? notifications.filter((n) => !n.is_read) : notifications });
  });
  await page.route('**/api/notifications/*/read', (route) => {
    const id = new URL(route.request().url()).pathname.split('/')[3];
    const n = notifications.find((x) => x.id === id);
    if (n) n.is_read = true;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/notifications/read-all', (route) => {
    for (const n of notifications) n.is_read = true;
    return route.fulfill({ json: { ok: true } });
  });
  return notifications;
}

/**
 * Mock fuer das Polling eines Hintergrund-Vorgangs: jeder GET /api/tasks/{id}
 * liefert den naechsten Zustand aus `states`, der letzte bleibt stehen.
 * Gibt eine Funktion zurueck, die die Anzahl der Poll-Requests liefert.
 *
 *   await mockBackgroundTaskPolling(page, 'task-1', [
 *     { status: 'RUNNING', progress_text: 'Analysiere …' },
 *     { status: 'SUCCEEDED', result: { message: 'ok' } },
 *   ]);
 */
export async function mockBackgroundTaskPolling(
  page: Page,
  taskId: string,
  states: Partial<TaskResponse>[],
) {
  let polls = 0;
  await page.route(`**/api/tasks/${taskId}`, (route) => {
    const state = states[Math.min(polls, states.length - 1)];
    polls += 1;
    return route.fulfill({ json: makeMockTask({ id: taskId, ...state }) });
  });
  return () => polls;
}
