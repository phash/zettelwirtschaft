import { test, expect, Page } from '@playwright/test';
import {
  setupReviewMocks,
  makeMockTask,
  mockBackgroundTaskPolling,
  MOCK_REVIEW_PENDING,
  MOCK_DOCUMENT_WITH_QUESTIONS,
} from '../helpers/mock.helpers';
import type { DocumentResponse, ReviewPendingResponse } from '../helpers/api-types';

// Weitere Review-Faelle ("Später" inkl. Reload, Ende der Warteschlange,
// erfolgreiche Re-Analyse, formatierte Werte, Touch-Zoom) stehen in
// 31-review-regressions.spec.ts.

const FIRST = MOCK_REVIEW_PENDING.documents[0];
const SECOND = MOCK_REVIEW_PENDING.documents[1];

function allAnswered(doc: DocumentResponse): DocumentResponse {
  return {
    ...doc,
    review_questions: doc.review_questions.map((q) => ({ ...q, answer: 'Beantwortet', is_answered: true })),
  };
}

/** Pending-Liste mit austauschbarem Inhalt (z.B. nach Bestaetigen). */
async function mockPending(page: Page, initial: ReviewPendingResponse) {
  let current = initial;
  let calls = 0;
  await page.route('**/api/review/pending', (route) => {
    calls += 1;
    return route.fulfill({ json: current });
  });
  return {
    set(next: ReviewPendingResponse) { current = next; },
    get calls() { return calls; },
  };
}

test.describe('Review System', () => {
  test.beforeEach(async ({ page }) => {
    await setupReviewMocks(page);
  });

  test('Shows open document count and the first document', async ({ page }) => {
    await page.goto('/pruefen');
    await expect(page.getByTestId('review-counter')).toHaveText('Noch 2 Dokumente offen');
    await expect(page.locator('iframe[title="Vorschau"]')).toHaveAttribute('src', `/api/documents/${FIRST.id}/file`);
  });

  test('Shows recognized data summary', async ({ page }) => {
    await page.goto('/pruefen');
    const box = page.getByTestId('review-confident-fields');
    await expect(box).toContainText('Erkannte Daten');
    await expect(box).toContainText(`Titel: ${FIRST.title}`);
    await expect(box).toContainText('Typ: Sonstiges');
  });

  test('Shows questions with type badges and suggestions', async ({ page }) => {
    await page.goto('/pruefen');
    const [q1, q2] = MOCK_DOCUMENT_WITH_QUESTIONS.review_questions;
    await expect(page.getByText(q1.question)).toBeVisible();
    await expect(page.getByText(q2.question)).toBeVisible();
    await expect(page.getByText('Klassifikation', { exact: true })).toBeVisible();
    await expect(page.getByText('Extraktion', { exact: true })).toBeVisible();
    for (const s of ['Rechnung', 'Quittung', 'Kaufvertrag']) {
      await expect(page.getByRole('button', { name: s, exact: true })).toBeVisible();
    }
    await expect(page.getByText('0/2')).toBeVisible();
  });

  test('Click suggested answer and submit', async ({ page }) => {
    const answers: { id: string; answer: string }[] = [];
    await page.route('**/api/review/questions/*/answer', (route) => {
      answers.push({ id: new URL(route.request().url()).pathname.split('/')[4], answer: route.request().postDataJSON().answer });
      return route.fulfill({ json: { ok: true, all_answered: false } });
    });
    await page.goto('/pruefen');

    await page.getByRole('button', { name: 'Quittung', exact: true }).click();
    await expect(page.getByLabel(MOCK_DOCUMENT_WITH_QUESTIONS.review_questions[0].question)).toHaveValue('Quittung');
    await page.getByRole('button', { name: 'Beantworten' }).first().click();

    await expect.poll(() => answers).toEqual([{ id: 'q-1', answer: 'Quittung' }]);
    await expect(page.getByText('1/2')).toBeVisible();
  });

  test('Free text answer via textarea', async ({ page }) => {
    const answers: string[] = [];
    await page.route('**/api/review/questions/*/answer', (route) => {
      answers.push(route.request().postDataJSON().answer);
      return route.fulfill({ json: { ok: true, all_answered: false } });
    });
    await page.goto('/pruefen');

    const q2 = MOCK_DOCUMENT_WITH_QUESTIONS.review_questions[1];
    await page.getByLabel(q2.question).fill('99,95 EUR');
    await page.getByRole('button', { name: 'Beantworten' }).nth(1).click();

    await expect.poll(() => answers).toEqual(['99,95 EUR']);
  });

  test('"So übernehmen" with open questions asks first, then accepts the AI result', async ({ page }) => {
    const approved: string[] = [];
    await page.route('**/api/review/documents/*/accept', (route) => {
      approved.push(new URL(route.request().url()).pathname);
      return route.fulfill({ json: { ok: true, review_status: 'REVIEWED' } });
    });
    const pending = await mockPending(page, MOCK_REVIEW_PENDING);
    await page.goto('/pruefen');

    await page.getByRole('button', { name: 'So übernehmen' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Die 2 offenen Rückfragen werden nicht mehr gestellt.');
    expect(approved).toEqual([]);

    pending.set({ documents: [SECOND], total: 1 });
    await dialog.getByRole('button', { name: 'So übernehmen' }).click();

    await expect.poll(() => approved).toEqual([`/api/review/documents/${FIRST.id}/accept`]);
    await expect(page.getByText('Erkannte Daten übernommen.')).toBeVisible();
    await expect(page.locator('iframe[title="Vorschau"]')).toHaveAttribute('src', `/api/documents/${SECOND.id}/file`);
    await expect(page.getByTestId('review-counter')).toHaveText('Noch 1 Dokument offen');
  });

  test('After the last document, newly arrived documents are shown (no blank page)', async ({ page }) => {
    const NEW_DOC = { ...SECOND, id: 'doc-9', title: 'Neu eingetroffen' };
    await setupReviewMocks(page, [allAnswered(MOCK_DOCUMENT_WITH_QUESTIONS), { ...MOCK_DOCUMENT_WITH_QUESTIONS, id: 'doc-9', title: 'Neu eingetroffen' }]);
    const pending = await mockPending(page, { documents: [FIRST], total: 1 });
    await page.goto('/pruefen');
    await expect(page.locator('iframe[title="Vorschau"]')).toHaveAttribute('src', `/api/documents/${FIRST.id}/file`);

    pending.set({ documents: [NEW_DOC], total: 1 });
    await page.getByRole('button', { name: 'Bestätigen' }).click();

    await expect(page.locator('iframe[title="Vorschau"]')).toHaveAttribute('src', '/api/documents/doc-9/file');
    await expect(page.getByText('Neu eingetroffen').first()).toBeVisible();
  });

  test('Detail load error shows a retry instead of a blank page', async ({ page }) => {
    let fail = true;
    await page.route(`**/api/review/documents/${FIRST.id}`, (route) => {
      if (fail) return route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } });
      return route.fallback();
    });
    await page.goto('/pruefen');

    const fallback = page.getByTestId('review-load-error');
    await expect(fallback).toContainText('Das Dokument konnte nicht geladen werden.');
    fail = false;
    await fallback.getByRole('button', { name: 'Erneut versuchen' }).click();
    await expect(page.locator('iframe[title="Vorschau"]')).toHaveAttribute('src', `/api/documents/${FIRST.id}/file`);
  });

  test('Empty state when all reviewed', async ({ page }) => {
    await page.route('**/api/review/pending', (route) =>
      route.fulfill({ json: { documents: [], total: 0 } })
    );
    await page.goto('/pruefen');
    await expect(page.getByText('Alles geprüft!')).toBeVisible();
    await expect(page.getByText('Keine Dokumente mit offenen Rückfragen.')).toBeVisible();
  });

  test('Details link points to the document', async ({ page }) => {
    await page.goto('/pruefen');
    await expect(page.getByRole('link', { name: 'Details' })).toHaveAttribute('href', `/dokumente/${FIRST.id}`);
  });
});

test.describe('Review re-analysis (background task)', () => {
  const FAILED_ANALYSIS: DocumentResponse = {
    ...MOCK_DOCUMENT_WITH_QUESTIONS,
    review_questions: [{
      ...MOCK_DOCUMENT_WITH_QUESTIONS.review_questions[0],
      id: 'q-9',
      question: 'Die KI-Analyse ist fehlgeschlagen. Bitte Daten prüfen.',
      question_type: 'analysis_failed',
      suggested_answers: null,
      field_affected: null,
    }],
  };

  test.beforeEach(async ({ page }) => {
    await setupReviewMocks(page, [FAILED_ANALYSIS, { ...MOCK_DOCUMENT_WITH_QUESTIONS, id: SECOND.id, title: SECOND.title }]);
  });

  test('Failed task shows the error and re-enables the button', async ({ page }) => {
    await page.route(`**/api/review/documents/${FIRST.id}/reanalyze`, (route) =>
      route.fulfill({ status: 202, json: makeMockTask({ id: 'task-re', kind: 'reanalyze', key: FIRST.id }) })
    );
    await mockBackgroundTaskPolling(page, 'task-re', [
      { kind: 'reanalyze', key: FIRST.id, status: 'FAILED', error: 'KI-Modell nicht verfügbar' },
    ]);
    await page.goto('/pruefen');

    const panel = page.getByTestId('reanalysis-panel');
    await panel.getByRole('button', { name: 'Erneut analysieren' }).click();
    await expect(page.getByText('KI-Modell nicht verfügbar')).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Erneut analysieren' })).toBeEnabled();
  });

  test('A re-analysis already running (409) is attached to instead of failing', async ({ page }) => {
    const running = makeMockTask({ id: 'task-old', kind: 'reanalyze', key: FIRST.id, status: 'RUNNING', progress_text: 'Läuft bereits …' });
    await page.route(`**/api/review/documents/${FIRST.id}/reanalyze`, (route) =>
      route.fulfill({ status: 409, json: { detail: 'Für dieses Dokument läuft bereits eine Re-Analyse.', task: running } })
    );
    const polls = await mockBackgroundTaskPolling(page, 'task-old', [
      { kind: 'reanalyze', key: FIRST.id, status: 'RUNNING', progress_text: 'Läuft bereits …' },
    ]);
    await page.goto('/pruefen');

    const panel = page.getByTestId('reanalysis-panel');
    await panel.getByRole('button', { name: 'Erneut analysieren' }).click();
    await expect(panel.getByRole('status')).toHaveText('Läuft bereits …');
    await expect.poll(polls).toBeGreaterThan(0);
  });

  test('A running re-analysis is resumed after reload', async ({ page }) => {
    const running = makeMockTask({ id: 'task-run', kind: 'reanalyze', key: FIRST.id, status: 'RUNNING', progress_text: 'Analysiere Dokument …' });
    await page.route('**/api/tasks?*', (route) => {
      const params = new URL(route.request().url()).searchParams;
      const match = params.get('kind') === 'reanalyze' && params.get('key') === FIRST.id;
      return route.fulfill({ json: match ? [running] : [] });
    });
    await mockBackgroundTaskPolling(page, 'task-run', [{ ...running }]);
    await page.goto('/pruefen');

    const panel = page.getByTestId('reanalysis-panel');
    await expect(panel.getByRole('status')).toHaveText('Analysiere Dokument …');
    await expect(panel.getByRole('button', { name: 'Analysiere …' })).toBeDisabled();
  });
});
