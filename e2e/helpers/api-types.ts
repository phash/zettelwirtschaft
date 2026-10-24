// Typen der Backend-Antworten, wie das Frontend sie erwartet — gespiegelt aus
// den Pydantic-Schemas unter backend/app/schemas/ bzw. den dict-Antworten der
// Router unter backend/app/api/. Die Mock-Daten in mock.helpers.ts werden mit
// `satisfies` gegen diese Typen geprueft: weicht ein Mock vom Schema ab (falscher
// Feldname, Zahl statt String-ID, Array statt Dict), schlaegt `npm run typecheck`
// fehl, statt dass ein Test still auf kaputter Darstellung gruen wird.
//
// Bei einer Schema-Aenderung im Backend: hier nachziehen, dann die Mocks.

export type Id = string;
export type IsoDate = string;      // 2026-01-15
export type IsoDateTime = string;  // 2026-01-16T10:30:00Z

export type DocumentType =
  | 'RECHNUNG' | 'QUITTUNG' | 'KAUFVERTRAG' | 'GARANTIESCHEIN' | 'VERSICHERUNGSPOLICE'
  | 'KONTOAUSZUG' | 'LOHNABRECHNUNG' | 'STEUERBESCHEID' | 'MIETVERTRAG'
  | 'HANDWERKER_RECHNUNG' | 'ARZTRECHNUNG' | 'REZEPT' | 'AMTLICHES_SCHREIBEN'
  | 'BEDIENUNGSANLEITUNG' | 'SONSTIGES';
export type DocumentStatus = 'ACTIVE' | 'ARCHIVED' | 'DELETED';
export type ReviewStatus = 'OK' | 'NEEDS_REVIEW' | 'REVIEWED';
export type JobStatus =
  | 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED' | 'NEEDS_REVIEW'
  | 'AWAITING_SPLIT' | 'SPLIT';
export type JobSource = 'UPLOAD' | 'WATCH_FOLDER' | 'EMAIL' | 'SPLIT_REQUEST';
export type ComponentState = 'ok' | 'warning' | 'error';

// --- /api/health ---------------------------------------------------------
export interface ComponentStatus { status: ComponentState; message?: string | null }
export interface HealthResponse {
  status: ComponentState;
  api: ComponentStatus;
  database: ComponentStatus;
  storage: ComponentStatus;
  ollama: ComponentStatus;
}

// --- /api/system/health --------------------------------------------------
export interface ConfiguredModel {
  name: string;
  purpose: 'llm' | 'embedding';
  available: boolean;
  downloading: boolean;
}
export interface SystemHealthComponent extends ComponentStatus {
  configured_models?: ConfiguredModel[];
  missing?: string[];
  last_calls?: Record<string, { at: IsoDateTime; ok: boolean; model: string; error_kind: string | null; error: string | null }>;
  vectors?: number;
  /** Ollama-Version laut /api/version (null = nicht ermittelbar) und Mindestversion. */
  version?: string | null;
  min_version?: string;
  /** watch_folder / export_folder: Erreichbarkeit (nur Pfad + Benutzername). */
  mode?: 'lokal' | 'netzwerk' | null;
  state?: 'ok' | 'verbinde' | 'getrennt' | 'fehler';
  path?: string | null;
  user?: string | null;
  reason?: string | null;
  last_ok_at?: IsoDateTime | null;
  next_retry_at?: IsoDateTime | null;
  poll_interval?: number | null;
  /** Zeitpunkt der letzten Pruefung (Export: nur beim Archivieren). */
  checked_at?: IsoDateTime | null;
}
export interface SystemHealthResponse {
  status: ComponentState;
  app_version: string;
  install_path?: string;
  deployment_mode: 'native' | 'docker' | 'dev';
  components: Record<string, SystemHealthComponent>;
  statistics: {
    total_documents: number;
    database_size_bytes: number;
    archive_size_bytes: number;
    upload_size_bytes: number;
    disk_total_bytes: number;
    disk_used_bytes: number;
    disk_free_bytes: number;
  };
}

export interface BackupItem { filename: string; size_bytes: number; created_at: IsoDateTime; is_full: boolean }
export interface BackupListResponse { backups: BackupItem[] }

/** (B) Lokale Scanner-Freigabe des Eingangsordners — nie ein Passwort. */
export interface ScannerShareStatus {
  configured: boolean;
  exists: boolean;
  unc_path: string;
  user: string;
  path: string;
  matches_watch_dir: boolean;
}

/** GET/PUT-Antwort /api/system/settings (Passwoerter werden nie ausgeliefert). */
export interface FolderSettings {
  watch_dir: string;
  export_dir: string;
  watch_dir_host: string;
  export_dir_host: string;
  restart_required?: boolean;
  deployment_mode?: 'native' | 'docker' | 'dev';
  watch_dir_net_user?: string;
  export_dir_net_user?: string;
  watch_dir_net_password_set?: boolean;
  export_dir_net_password_set?: boolean;
  watch_poll_interval?: number;
  network_folders_supported?: boolean;
  scanner_share?: ScannerShareStatus | null;
}

/** PUT-Body /api/system/settings: Passwoerter write-only (null = unveraendert). */
export interface FolderSettingsUpdate {
  watch_dir: string;
  export_dir: string;
  watch_dir_host?: string;
  export_dir_host?: string;
  watch_dir_net_user?: string;
  watch_dir_net_password?: string | null;
  export_dir_net_user?: string;
  export_dir_net_password?: string | null;
  watch_poll_interval?: number | null;
}

/** POST /api/system/settings/test-folder */
export interface TestFolderRequest {
  target: 'watch' | 'export';
  path: string;
  net_user?: string;
  net_password?: string | null;
}
export interface TestFolderResponse {
  ok: boolean;
  kind: 'local' | 'unc' | 'network_drive' | 'subst' | 'missing_drive' | 'unsuitable' | 'unsupported';
  message: string;
  suggested_paths: string[];
}

// --- /api/auth -----------------------------------------------------------
export interface AuthStatus { pin_enabled: boolean; authenticated: boolean; pin_warning: boolean }

// --- /api/stats, /api/jobs -----------------------------------------------
export interface DashboardStats {
  total_documents: number;
  documents_this_month: number;
  pending_reviews: number;
  expiring_warranties_30d: number;
  processing_jobs: number;
}

export interface JobStatusResponse {
  id: Id;
  original_filename: string;
  file_type: string;
  file_size_bytes: number;
  source: JobSource;
  status: JobStatus;
  error_message?: string | null;
  error_code?: string | null;
  retry_count: number;
  document_id?: Id | null;
  // Aufteilung (Spezifikation 12)
  split_batch_id?: Id | null;
  split_role?: 'SOURCE' | 'PART' | null;
  page_from?: number | null;
  page_to?: number | null;
  split_progress?: { pages_done: number; page_count: number } | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}
export interface Paginated<T> { items: T[]; total: number; page: number; page_size: number }
export interface QueueStatus { paused: boolean }

export interface UploadItem { job_id: Id; original_filename: string; status: JobStatus; message: string }
export interface UploadResponse { uploaded: UploadItem[]; rejected: { filename: string; error: string }[] }

// --- /api/documents ------------------------------------------------------
export interface TagResponse { id: number; name: string; category?: string | null; is_auto_generated: boolean }
export interface TagListItem extends TagResponse {}

export interface WarrantyInfoResponse {
  id: Id;
  product_name: string;
  product_category?: string | null;
  purchase_date: IsoDate;
  warranty_end_date: IsoDate;
  warranty_type: 'LEGAL' | 'MANUFACTURER' | 'EXTENDED';
  warranty_duration_months: number;
  retailer?: string | null;
  is_expired: boolean;
  reminder_sent: boolean;
  notes?: string | null;
}

export interface ReviewQuestionResponse {
  id: Id;
  question: string;
  question_type?: string | null;
  explanation?: string | null;
  answer?: string | null;
  field_affected?: string | null;
  suggested_answers?: string | null;  // Pipe-getrennt: "Rechnung|Quittung"
  is_answered: boolean;
  priority: number;
  created_at: IsoDateTime;
  answered_at?: IsoDateTime | null;
}

export interface DocumentListItem {
  id: Id;
  original_filename: string;
  file_type: string;
  file_size_bytes: number;
  document_type: DocumentType;
  title: string;
  document_date: IsoDate | null;
  amount: number | null;
  currency: string;
  issuer: string | null;
  tax_relevant: boolean;
  status: DocumentStatus;
  review_status: ReviewStatus;
  ai_confidence: number;
  filing_scope_id: Id | null;
  filing_scope_name: string | null;
  created_at: IsoDateTime;
  tags: TagResponse[];
}

export interface DocumentResponse extends DocumentListItem {
  recipient: string | null;
  reference_number: string | null;
  summary: string | null;
  ocr_text: string;
  ocr_confidence: number;
  tax_year: number | null;
  tax_category: string | null;
  updated_at: IsoDateTime;
  scanned_at?: IsoDateTime | null;
  warranty_info: WarrantyInfoResponse | null;
  review_questions: ReviewQuestionResponse[];
  // Herkunft aus einem aufgeteilten Stapel (Spezifikation 12)
  split_batch_id?: Id | null;
  split_part_index?: number | null;
  split_part_count?: number | null;
  split_source_filename?: string | null;
  source_pages?: string | null;
  can_undo_split?: boolean;
  split_undo_blocked_reason?: string | null;  // warum "Aufteilung rückgängig" gesperrt ist
  // Neu-Analyse (Issue #39)
  analysis_model?: string | null;
  has_proposal?: boolean;
}

// --- /api/review ---------------------------------------------------------
export interface ReviewPendingItem {
  id: Id;
  title: string;
  document_type: DocumentType;
  has_thumbnail: boolean;
  file_type: string;
  ai_confidence: number;
  created_at: IsoDateTime;
  open_questions: number;
  total_questions: number;
}
export interface ReviewPendingResponse { documents: ReviewPendingItem[]; total: number }
export interface ReviewDetailResponse {
  id: Id;
  title: string;
  document_type: DocumentType;
  file_type: string;
  ai_confidence: number;
  summary: string | null;
  ocr_text: string;
  questions: Omit<ReviewQuestionResponse, 'created_at' | 'answered_at'>[];
  confident_fields: {
    title: string;
    document_type: DocumentType;
    document_date: IsoDate | null;
    amount: number | null;
    currency: string;
    issuer: string | null;
    filing_scope: string | null;
  };
}
export interface ReviewStats {
  open_documents: number;
  open_questions: number;
  answered_questions: number;
  split_batches: number;  // Stapel in PROPOSED (Spezifikation 12)
}

// --- /api/search ---------------------------------------------------------
export interface SearchResultItem {
  id: Id;
  title: string;
  document_type: DocumentType;
  document_date: IsoDate | null;
  amount: number | null;
  currency: string;
  issuer: string | null;
  tags: string[];
  tax_relevant: boolean;
  highlight: string;  // Treffer zwischen \u0002 ... \u0003 (kein HTML)
  relevance_score: number;
  ai_confidence: number;
  created_at: IsoDateTime;
}
export interface SearchResponse {
  results: SearchResultItem[];
  total: number;
  page: number;
  page_size: number;
  facets: {
    document_types: Record<string, number>;
    years: Record<string, number>;
    top_issuers: Record<string, number>;
    filing_scopes: { id: Id; name: string; count: number }[];
  };
}
export interface SuggestResponse { suggestions: string[] }
export interface SavedSearchResponse { id: Id; name: string; query_params: Record<string, unknown>; created_at: IsoDateTime }

// --- /api/filing-scopes --------------------------------------------------
export interface FilingScopeResponse {
  id: Id;
  name: string;
  slug: string;
  description: string | null;
  keywords: string[];
  is_default: boolean;
  color: string | null;
  created_at: IsoDateTime;
}

// --- /api/tax ------------------------------------------------------------
export interface TaxYears { years: number[] }
export interface NonEurDocument { id: Id; title: string; document_date: IsoDate | null; amount: number | null; currency: string }
export interface TaxYearSummary {
  year: number;
  total_documents: number;
  total_amount: number;
  categories: { category: string; label: string; document_count: number; total_amount: number }[];
  non_eur_documents: NonEurDocument[];
  warnings: string[];
}
export interface TaxExportValidation { year: number; total_documents: number; warnings: string[]; ready: boolean }

// --- /api/warranties -----------------------------------------------------
export interface WarrantyListItem extends WarrantyInfoResponse { document_id: Id; document_title: string; days_remaining: number }
export interface WarrantyStats { total: number; active: number; expiring_soon: number; expired: number }

// --- /api/chat -----------------------------------------------------------
export interface ChatSource { document_id: Id; title: string; document_type: string | null; document_date: string | null; issuer: string | null }
export interface ChatResponse { answer: string; sources: ChatSource[]; chunks_found: number }
export interface ChatHistoryResponse {
  messages: { id: Id; role: 'user' | 'assistant'; content: string; sources: ChatSource[]; filing_scope_id: Id | null; created_at: IsoDateTime }[];
  total: number;
}

// --- /api/notifications --------------------------------------------------
export type NotificationType = 'WARRANTY_EXPIRING' | 'WARRANTY_EXPIRED' | 'REVIEW_NEEDED' | 'SYSTEM' | 'SPLIT_NEEDED' | 'REANALYSIS_DONE';
export interface NotificationResponse {
  id: Id;
  type: NotificationType;
  title: string;
  message: string;
  document_id: Id | null;
  is_read: boolean;
  created_at: IsoDateTime;
}
export interface NotificationCount { unread: number }

// --- /api/email ----------------------------------------------------------
export type ScheduleType = 'CRON' | 'MANUAL' | 'IDLE';
export interface EmailAccountResponse {
  id: number;
  name: string;
  imap_host: string;
  imap_port: number;
  use_ssl: boolean;
  username: string;
  folder_inbox: string;
  folder_processed: string;
  schedule_type: ScheduleType;
  cron_expression: string | null;
  is_active: boolean;
  last_checked_at: IsoDateTime | null;
  last_error: string | null;
  filing_scope_id: Id | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}
export interface EmailStatsItem {
  account_id: number;
  account_name: string;
  total_processed: number;
  relevant: number;
  irrelevant: number;
  failed: number;
  last_checked_at: IsoDateTime | null;
}
export interface EmailSettingsInfo { timezone: string; unseen_only: boolean; max_per_run: number; idle_interval_minutes: number }
export interface EmailTestResult { success: boolean; error?: string | null; warning?: string | null }
export interface EmailFetchResult { total: number; relevant: number; irrelevant: number; skipped: number; failed: number; remaining: number; error?: string | null }
export interface ProcessedEmailResponse {
  id: number;
  message_id: string;
  subject: string | null;
  sender: string | null;
  received_at: IsoDateTime | null;
  status: 'RELEVANT' | 'IRRELEVANT' | 'FAILED';
  relevance_reason: string | null;
  processing_job_id: Id | null;
  created_at: IsoDateTime;
}

// --- /api/tasks (Hintergrund-Vorgaenge) ----------------------------------
export interface TaskResponse {
  id: Id;
  kind: string;
  key: string | null;
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
  progress_percent: number | null;
  progress_text: string | null;
  result: unknown;
  error: string | null;
  created_at: IsoDateTime;
  started_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
  done: boolean;
}

// --- /api/split (Aufteilen von Stapel-Scans, Spezifikation 12) ------------
export type SplitBatchStatus =
  | 'DETECTING' | 'PROPOSED' | 'SPLITTING' | 'DONE' | 'DONE_WITH_ERRORS'
  | 'KEPT_WHOLE' | 'DISCARDED' | 'UNDONE' | 'FAILED';
export interface SplitPage {
  n: number;
  w: number;
  h: number;
  rot: number;
  blank: boolean;
  ink: number;
  source: 'ocr' | 'digital' | 'none' | 'skipped';
  conf: number;
  counter?: (number | null)[] | null;
  blank_kind?: 'back' | 'separator' | null;
}
export interface SplitBoundary {
  after_page: number;
  decision: 'cut' | 'uncertain' | 'none';
  score?: number | null;
  p?: number | null;
  reasons: string[];
  llm: boolean;
}
export interface SplitPartInfo {
  index: number;
  pages: number[];
  job_id: Id | null;
  job_status: JobStatus | null;
  document_id: Id | null;
  error_message: string | null;
  error_code: string | null;
}
export interface SplitBatchResponse {
  id: Id;
  origin: 'AUTO' | 'MANUAL';
  status: SplitBatchStatus;
  version: number;
  original_filename: string;
  job_source: JobSource;
  source_job_id: Id | null;
  source_document_id: Id | null;
  page_count: number;
  pages_done: number;
  pages: SplitPage[];
  boundaries: SplitBoundary[];
  duplex: boolean;
  duplex_conf: number;
  partial_from: number | null;
  llm_used: boolean;
  llm_unavailable: boolean;
  excluded: number[];
  parts: SplitPartInfo[];
  error_message: string | null;
  error_code: string | null;
  can_undo: boolean;
  undo_blocked_reason: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
  confirmed_at: IsoDateTime | null;
  finished_at: IsoDateTime | null;
}
export interface SplitBatchListItem {
  id: Id;
  origin: 'AUTO' | 'MANUAL';
  status: SplitBatchStatus;
  original_filename: string;
  job_source: JobSource;
  page_count: number;
  pages_done: number;
  proposed_parts: number;
  uncertain: number;
  created_at: IsoDateTime;
}
export interface PaginatedSplitBatches { items: SplitBatchListItem[]; total: number; page: number; page_size: number }
export interface SplitSettingsBody { auto_enabled: boolean; min_pages: number; use_llm?: boolean | null }
export interface SplitSettingsResponse { auto_enabled: boolean; min_pages: number; use_llm: boolean; max_pages: number }
export interface SplitConfirmRequest { version: number; parts: { pages: number[] }[]; excluded: number[] }
export interface SplitConfirmResponse { batch_id: Id; job_ids: Id[] }
export interface SplitRequestResponse { batch_id: Id; job_id: Id }
export interface SplitUndoResponse {
  batch_id: Id;
  status: SplitBatchStatus;
  removed_document_ids: Id[];
  restored_document_id: Id | null;
}

// --- /api/reanalysis (Änderungsvorschläge, Issue #39) --------------------
export interface ReanalyzeRequest {
  with_ocr: boolean;
}

export interface FieldChangeResponse {
  field: string;
  label: string;
  // Datum ISO, Betrag als String, Enum-Wert, Ablagebereich {id,name}, Tags als
  // sortierte Namensliste, Garantie als Objekt oder null, ocr_text {text, confidence}.
  old: unknown;
  new: unknown;
}

export interface ProposalResponse {
  document_id: Id;
  document_title: string;
  model: string;
  with_ocr: boolean;
  ocr_note: string | null;  // 'discarded_worse': neu erkannter Text war schlechter
  created_at: IsoDateTime;
  changes: FieldChangeResponse[];
}

export interface ProposalListResponse {
  items: ProposalResponse[];
  total: number;
  field_counts: Record<string, number>;
}

export interface ApplyRequest {
  fields: string[] | null;
}

export interface RunPreview {
  eligible: number;
  reviewed_excluded: number;
  no_text_excluded: number;
  deleted_or_missing: number;
  estimate_seconds: number;
}

export interface RunFailure {
  document_id: Id;
  title: string | null;
  error: string | null;
}

export interface RunResponse {
  id: Id;
  status: string;
  model: string;
  with_ocr: boolean;
  total: number;
  done: number;
  proposed: number;
  unchanged: number;
  failed: number;
  skipped: number;
  last_error: string | null;
  created_at: IsoDateTime;
  finished_at: IsoDateTime | null;
  failures: RunFailure[];
}
