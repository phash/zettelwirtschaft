import { test, expect, Page } from '@playwright/test';
import { setupBaseMocks, makeMockTask, mockBackgroundTaskPolling } from '../helpers/mock.helpers';

// Regressionstests ReviewView: "Später" ändert keinen Status, "So übernehmen"
// bestätigt bewusst, kein leeres Ende der Warteschlange, Re-Analyse als
// Hintergrund-Vorgang, formatierte Werte, Touch-Zoom.

const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

interface ReviewDoc {
  id: string;
  title: string;
  document_type: string;
  file_type?: string;
  questions?: Record<string, unknown>[];
  confident_fields?: Record<string, unknown>;
}

function question(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    question: `Frage ${id}?`,
    question_type: 'extraction',
    explanation: null,
    field_affected: 'amount',
    suggested_answers: null,
    answer: null,
    is_answered: false,
    priority: 1,
    ...overrides,
  };
}

function doc(id: string, title: string, extra: Partial<ReviewDoc> = {}): ReviewDoc {
  return { id, title, document_type: 'SONSTIGES', file_type: 'pdf', questions: [question(`${id}-q1`)], ...extra };
}

interface ReviewState {
  open: ReviewDoc[];
  approved: string[];
  accepted: string[];
  skipCalls: number;
  pendingCalls: number;
}

async function setupReview(page: Page, docs: ReviewDoc[]) {
  await setupBaseMocks(page);
  const state: ReviewState = { open: [...docs], approved: [], accepted: [], skipCalls: 0, pendingCalls: 0 };
  await page.route('**/api/review/pending', (route) => {
    state.pendingCalls += 1;
    return route.fulfill({
      json: {
        documents: state.open.map((d) => ({
          id: d.id, title: d.title, document_type: d.document_type, has_thumbnail: false,
          file_type: d.file_type ?? 'pdf', ai_confidence: 0.4, created_at: '2026-02-15T10:00:00Z',
          open_questions: 1, total_questions: 1,
        })),
        total: state.open.length,
      },
    });
  });
  await page.route('**/api/review/documents/*', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    const id = new URL(route.request().url()).pathname.split('/').pop();
    const d = docs.find((x) => x.id === id);
    if (!d) return route.fulfill({ status: 404, json: { detail: 'Dokument nicht gefunden' } });
    return route.fulfill({
      json: {
        id: d.id, title: d.title, document_type: d.document_type, file_type: d.file_type ?? 'pdf',
        ai_confidence: 0.4, summary: null, ocr_text: '',
        questions: d.questions ?? [],
        confident_fields: d.confident_fields ?? { title: d.title, document_type: d.document_type },
      },
    });
  });
  await page.route('**/api/review/documents/*/approve', (route) => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2)!;
    state.approved.push(id);
    state.open = state.open.filter((d) => d.id !== id);
    return route.fulfill({ json: { ok: true, review_status: 'REVIEWED' } });
  });
  await page.route('**/api/review/documents/*/accept', (route) => {
    const id = new URL(route.request().url()).pathname.split('/').at(-2)!;
    state.accepted.push(id);
    state.open = state.open.filter((d) => d.id !== id);
    return route.fulfill({ json: { ok: true, review_status: 'REVIEWED' } });
  });
  await page.route('**/api/review/documents/*/skip', (route) => {
    state.skipCalls += 1;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/documents/*/file', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1X1 })
  );
  return state;
}

test.describe('Review regressions', () => {
  test('"Später" defers client-side without changing the review status', async ({ page }) => {
    const state = await setupReview(page, [doc('a', 'Dokument A'), doc('b', 'Dokument B')]);
    await page.goto('/pruefen');
    await expect(page.getByText('Dokument A').first()).toBeVisible();

    await page.getByRole('button', { name: 'Später' }).click();
    await expect(page.getByText('Dokument B').first()).toBeVisible();
    await expect(page.getByTestId('review-counter')).toContainText('1 auf später verschoben');

    await page.getByRole('button', { name: 'Später' }).click();
    await expect(page.getByTestId('review-later-end')).toContainText('2 Dokumente auf später verschoben');
    expect(state.skipCalls).toBe(0);
    expect(state.approved).toEqual([]);
    expect(state.accepted).toEqual([]);

    // Zurückstellen überlebt einen Reload (sessionStorage) …
    await page.reload();
    await expect(page.getByTestId('review-later-end')).toBeVisible();
    // … und lässt sich wieder aufheben.
    await page.getByRole('button', { name: 'Erneut durchgehen' }).click();
    await expect(page.getByText('Dokument A').first()).toBeVisible();
  });

  test('"So übernehmen" confirms explicitly and accepts the AI result despite open questions', async ({ page }) => {
    const state = await setupReview(page, [doc('a', 'Dokument A')]);
    await page.goto('/pruefen');

    await expect(page.getByRole('button', { name: 'Bestätigen' })).toHaveCount(0);
    await page.getByRole('button', { name: 'So übernehmen' }).click();
    await expect(page.getByText('Die offene Rückfrage wird nicht mehr gestellt.', { exact: false })).toBeVisible();
    await page.getByRole('button', { name: 'So übernehmen' }).last().click();

    // Eigener Endpoint (/accept, AuditLog "accept") statt /approve ("Freigeben")
    await expect.poll(() => state.accepted).toEqual(['a']);
    expect(state.approved).toEqual([]);
    await expect(page.getByText('Alles geprüft!')).toBeVisible();
  });

  test('queue end never renders a blank page (list reloaded after each action)', async ({ page }) => {
    const answered = [question('q', { is_answered: true, answer: 'ok' })];
    const state = await setupReview(page, [
      doc('a', 'Dokument A', { questions: answered }),
      doc('b', 'Dokument B', { questions: answered }),
    ]);
    await page.goto('/pruefen');

    await page.getByRole('button', { name: 'Bestätigen' }).click();
    await expect(page.getByText('Dokument B').first()).toBeVisible();
    await page.getByRole('button', { name: 'Bestätigen' }).click();
    await expect(page.getByText('Alles geprüft!')).toBeVisible();
    expect(state.approved).toEqual(['a', 'b']);
  });

  test('failed detail load shows a retry instead of an empty page', async ({ page }) => {
    await setupReview(page, [doc('a', 'Dokument A')]);
    await page.route('**/api/review/documents/a', (route) =>
      route.fulfill({ status: 500, json: { detail: 'kaputt' } })
    );
    await page.goto('/pruefen');
    await expect(page.getByTestId('review-load-error')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Erneut versuchen' })).toBeVisible();
  });

  test('re-analysis runs as background task and is offered via question_type', async ({ page }) => {
    const state = await setupReview(page, [
      doc('a', 'Dokument A', {
        questions: [question('qa', {
          question: 'Die automatische Analyse ist fehlgeschlagen. Um welchen Typ handelt es sich?',
          question_type: 'analysis_failed',
          field_affected: 'document_type',
        })],
      }),
    ]);
    let started = 0;
    await page.route('**/api/review/documents/a/reanalyze', (route) => {
      started += 1;
      state.open = [];
      return route.fulfill({ status: 202, json: makeMockTask({ id: 'task-re', kind: 'reanalyze', key: 'a' }) });
    });
    const polls = await mockBackgroundTaskPolling(page, 'task-re', [
      { status: 'RUNNING', kind: 'reanalyze', key: 'a', progress_text: 'Analysiere Dokument …' },
      { status: 'SUCCEEDED', kind: 'reanalyze', key: 'a', result: { message: 'Keine Änderungen.', status: 'unchanged', changes: 0, ocr_note: null } },
    ]);

    await page.goto('/pruefen');
    await expect(page.getByTestId('reanalysis-panel')).toBeVisible();
    await page.getByRole('button', { name: 'Erneut analysieren' }).click();
    await expect(page.getByText('Keine Änderungen.')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Alles geprüft!')).toBeVisible();
    expect(started).toBe(1);
    expect(polls()).toBeGreaterThanOrEqual(2);
  });

  test('recognised values are shown formatted (type label, German date, currency)', async ({ page }) => {
    await setupReview(page, [
      doc('a', 'Dokument A', {
        document_type: 'HANDWERKER_RECHNUNG',
        confident_fields: {
          title: 'Dokument A',
          document_type: 'HANDWERKER_RECHNUNG',
          document_date: '2026-02-15',
          amount: 12.5,
          currency: 'EUR',
          issuer: 'Maler Müller',
          filing_scope: 'Privat',
        },
      }),
    ]);
    await page.goto('/pruefen');
    const box = page.getByTestId('review-confident-fields');
    await expect(box).toContainText('Handwerkerrechnung');
    await expect(box).not.toContainText('HANDWERKER_RECHNUNG');
    await expect(box).toContainText('15.2.2026');
    await expect(box).toContainText('12,50 €');
  });

  test.describe('touch', () => {
    test.use({ hasTouch: true });

    test('zoom buttons react to taps and the preview allows vertical scrolling', async ({ page }) => {
      await setupReview(page, [doc('a', 'Bild-Beleg', { file_type: 'png' })]);
      await page.goto('/pruefen');

      const preview = page.getByTestId('review-image-preview');
      await expect(preview).toHaveCSS('touch-action', 'pan-y pinch-zoom');
      await page.getByRole('button', { name: 'Vergrößern' }).tap();
      await expect(page.getByTestId('zoom-level')).toHaveText('125%');
      // Gezoomt: Gesten gehören dem Pan, nicht dem Seiten-Scroll.
      await expect(preview).toHaveCSS('touch-action', 'none');
    });
  });
});
