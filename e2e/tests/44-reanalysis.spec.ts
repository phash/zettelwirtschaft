import { test, expect, Page } from '@playwright/test';
import {
  setupDocumentDetailMocks,
  setupReviewMocks,
  makeMockTask,
  makeMockProposal,
  mockBackgroundTaskPolling,
  MOCK_DOCUMENT_DETAIL,
  MOCK_DOCUMENT_WITH_QUESTIONS,
} from '../helpers/mock.helpers';
import type { ApplyRequest, DocumentResponse, ReanalyzeRequest } from '../helpers/api-types';

// Neu analysieren (Issue #39): Knopf + Dialog am Dokument, Änderungsvorschlag
// mit Diff, Übernahme nur der angehakten Felder; Re-Analyse in der Prüfung.

const DOC = MOCK_DOCUMENT_DETAIL;
const DOC_URL = `/dokumente/${DOC.id}`;
const MODEL = 'gemma4:12b-it-qat';

/** Start-Endpoint (202) + Polling eines Neu-Analyse-Vorgangs. */
async function mockReanalyzeTask(
  page: Page,
  docId: string,
  result: Record<string, unknown>,
  endpoint = `**/api/review/documents/${docId}/reanalyze`,
) {
  await page.route(endpoint, (route) =>
    route.fulfill({ status: 202, json: makeMockTask({ id: 'task-re', kind: 'reanalyze', key: docId }) }),
  );
  await mockBackgroundTaskPolling(page, 'task-re', [
    { kind: 'reanalyze', key: docId, status: 'SUCCEEDED', result },
  ]);
}

test.describe('Neu analysieren am Dokument', () => {
  test.beforeEach(async ({ page }) => {
    await setupDocumentDetailMocks(page);
  });

  test('detail: Neu analysieren mit OCR sendet with_ocr=true', async ({ page }) => {
    await mockReanalyzeTask(page, DOC.id, { message: 'Keine Änderungen.', status: 'unchanged', changes: 0, ocr_note: null });
    await page.goto(DOC_URL);

    await page.getByRole('button', { name: 'Neu analysieren' }).click();
    const dialog = page.getByRole('dialog');
    const checkbox = dialog.getByLabel('Text neu erkennen (OCR) — dauert länger');
    await expect(checkbox).not.toBeChecked();
    await checkbox.check();

    const request = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/review/documents/${DOC.id}/reanalyze`));
    await dialog.getByRole('button', { name: 'Neu analysieren' }).click();
    expect(((await request).postDataJSON()) as ReanalyzeRequest).toEqual({ with_ocr: true });
  });

  test('detail: Neu analysieren ohne OCR sendet with_ocr=false', async ({ page }) => {
    await mockReanalyzeTask(page, DOC.id, { message: 'Keine Änderungen.', status: 'unchanged', changes: 0, ocr_note: null });
    await page.goto(DOC_URL);

    await page.getByRole('button', { name: 'Neu analysieren' }).click();
    const request = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/review/documents/${DOC.id}/reanalyze`));
    await page.getByRole('dialog').getByRole('button', { name: 'Neu analysieren' }).click();
    expect(((await request).postDataJSON()) as ReanalyzeRequest).toEqual({ with_ocr: false });
  });

  test('detail: Vorschlag zeigt alt → neu und übernimmt nur angehakte Felder', async ({ page }) => {
    let current: DocumentResponse & Record<string, unknown> = { ...DOC, has_proposal: true, analysis_model: MODEL };
    await page.route(`**/api/documents/${DOC.id}`, (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: current }) : route.fallback(),
    );
    await page.route(`**/api/reanalysis/proposals/${DOC.id}`, (route) =>
      route.fulfill({ json: makeMockProposal() }),
    );
    const bodies: ApplyRequest[] = [];
    await page.route(`**/api/reanalysis/proposals/${DOC.id}/apply`, (route) => {
      bodies.push(route.request().postDataJSON());
      current = { ...current, has_proposal: false, issuer: 'Stadtwerke München GmbH' };
      return route.fulfill({ json: current });
    });

    await page.goto(DOC_URL);

    // Hat das Dokument einen offenen Vorschlag, steht der Diff direkt da.
    const diff = page.getByTestId('proposal-diff');
    const amountRow = diff.getByTestId('proposal-row-amount');
    await expect(amountRow).toContainText('89,50');
    await expect(amountRow).toContainText('98,50');
    await expect(diff.getByTestId('proposal-row-document_date')).toContainText('—');
    await expect(diff.getByTestId('proposal-row-document_date')).toContainText('20.1.2026');
    await expect(diff.getByRole('checkbox')).toHaveCount(3);
    for (const box of await diff.getByRole('checkbox').all()) await expect(box).toBeChecked();

    await diff.getByRole('checkbox', { name: 'Betrag übernehmen' }).uncheck();
    await diff.getByRole('button', { name: 'Auswahl übernehmen' }).click();

    await expect.poll(() => bodies).toEqual([{ fields: ['issuer', 'document_date'] }]);
    await expect(page.getByText('Änderungen übernommen.')).toBeVisible();
    await expect(page.getByTestId('proposal-diff')).toHaveCount(0);
    await expect(page.getByLabel('Aussteller')).toHaveValue('Stadtwerke München GmbH');
  });

  test('detail: Alles übernehmen sendet fields=null, Verwerfen verwirft', async ({ page }) => {
    let current: DocumentResponse & Record<string, unknown> = { ...DOC, has_proposal: true };
    await page.route(`**/api/documents/${DOC.id}`, (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: current }) : route.fallback(),
    );
    await page.route(`**/api/reanalysis/proposals/${DOC.id}`, (route) => route.fulfill({ json: makeMockProposal() }));
    const applied: ApplyRequest[] = [];
    await page.route(`**/api/reanalysis/proposals/${DOC.id}/apply`, (route) => {
      applied.push(route.request().postDataJSON());
      current = { ...current, has_proposal: false };
      return route.fulfill({ json: current });
    });
    let discards = 0;
    await page.route(`**/api/reanalysis/proposals/${DOC.id}/discard`, (route) => {
      discards += 1;
      current = { ...current, has_proposal: false };
      return route.fulfill({ status: 204, body: '' });
    });

    await page.goto(DOC_URL);
    await page.getByRole('button', { name: 'Alles übernehmen' }).click();
    await expect.poll(() => applied).toEqual([{ fields: null }]);
    await expect(page.getByTestId('proposal-diff')).toHaveCount(0);

    // Zweiter Vorschlag: verwerfen.
    current = { ...current, has_proposal: true };
    await mockReanalyzeTask(page, DOC.id, { message: 'Änderungsvorschlag erstellt.', status: 'proposed', changes: 3, ocr_note: null });
    await page.getByRole('button', { name: 'Neu analysieren' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Neu analysieren' }).click();
    await expect(page.getByTestId('proposal-diff')).toBeVisible({ timeout: 10_000 });
    await page.getByRole('button', { name: 'Verwerfen' }).click();
    await expect.poll(() => discards).toBe(1);
    await expect(page.getByTestId('proposal-diff')).toHaveCount(0);
  });

  test('detail: Konflikt beim Übernehmen zeigt die Meldung des Servers', async ({ page }) => {
    await page.route(`**/api/documents/${DOC.id}`, (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: { ...DOC, has_proposal: true } }) : route.fallback(),
    );
    await page.route(`**/api/reanalysis/proposals/${DOC.id}`, (route) => route.fulfill({ json: makeMockProposal() }));
    await page.route(`**/api/reanalysis/proposals/${DOC.id}/apply`, (route) =>
      route.fulfill({ status: 409, json: { detail: 'Das Dokument wurde inzwischen geändert.' } }),
    );

    await page.goto(DOC_URL);
    await page.getByRole('button', { name: 'Alles übernehmen' }).click();
    await expect(page.getByText('Das Dokument wurde inzwischen geändert.')).toBeVisible();
  });

  test('detail: unverändert zeigt Toast mit Modell', async ({ page }) => {
    await page.route(`**/api/documents/${DOC.id}`, (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: { ...DOC, analysis_model: MODEL } }) : route.fallback(),
    );
    await mockReanalyzeTask(page, DOC.id, { message: 'Keine Änderungen.', status: 'unchanged', changes: 0, ocr_note: null });
    await page.goto(DOC_URL);

    await page.getByRole('button', { name: 'Neu analysieren' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Neu analysieren' }).click();

    await expect(page.getByText(`Keine Änderungen — mit ${MODEL} geprüft`)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('proposal-diff')).toHaveCount(0);
    await expect(page.getByText(`Modell: ${MODEL}`)).toBeVisible();
  });

  test('detail: ohne bekanntes Modell steht "Modell: unbekannt"', async ({ page }) => {
    await page.goto(DOC_URL);
    await expect(page.getByText('Modell: unbekannt')).toBeVisible();
  });

  test('detail: verworfener OCR-Text zeigt Hinweis', async ({ page }) => {
    await page.route(`**/api/documents/${DOC.id}`, (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: { ...DOC, has_proposal: true } }) : route.fallback(),
    );
    await page.route(`**/api/reanalysis/proposals/${DOC.id}`, (route) =>
      route.fulfill({
        json: makeMockProposal({
          ocr_note: 'discarded_worse',
          changes: [{ field: 'issuer', label: 'Aussteller', old: 'A', new: 'B' }],
        }),
      }),
    );
    await page.goto(DOC_URL);
    await expect(page.getByTestId('proposal-ocr-note')).toHaveText('Neu erkannter Text war schlechter und wurde verworfen.');
  });

  test('detail: neuer Text zeigt Konfidenz und aufklappbare Vorschau', async ({ page }) => {
    await page.route(`**/api/documents/${DOC.id}`, (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: { ...DOC, has_proposal: true } }) : route.fallback(),
    );
    await page.route(`**/api/reanalysis/proposals/${DOC.id}`, (route) =>
      route.fulfill({
        json: makeMockProposal({
          with_ocr: true,
          changes: [{
            field: 'ocr_text',
            label: 'Erkannter Text',
            old: { text: 'Alter Text', confidence: 0.62 },
            new: { text: 'Neuer Text', confidence: 0.88 },
          }],
        }),
      }),
    );
    await page.goto(DOC_URL);

    const row = page.getByTestId('proposal-row-ocr_text');
    await expect(row).toContainText('Erkannter Text (Konfidenz 62 % → 88 %)');
    await expect(page.getByTestId('proposal-ocr-new')).toHaveCount(0);
    await row.getByRole('button', { name: 'Vorschau anzeigen' }).click();
    await expect(page.getByTestId('proposal-ocr-old')).toHaveText('Alter Text');
    await expect(page.getByTestId('proposal-ocr-new')).toHaveText('Neuer Text');
  });
});

test.describe('Neu analysieren in der Prüfung', () => {
  test('review: Re-Analyse nach KI-Ausfall zeigt Vorschlag', async ({ page }) => {
    const failed: DocumentResponse = {
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
    await setupReviewMocks(page, [failed]);
    const id = failed.id;
    const requests: ReanalyzeRequest[] = [];
    await page.route(`**/api/review/documents/${id}/reanalyze`, (route) => {
      requests.push(route.request().postDataJSON());
      return route.fulfill({ status: 202, json: makeMockTask({ id: 'task-re', kind: 'reanalyze', key: id }) });
    });
    await mockBackgroundTaskPolling(page, 'task-re', [
      { kind: 'reanalyze', key: id, status: 'SUCCEEDED', result: { message: 'Änderungsvorschlag erstellt.', status: 'proposed', changes: 3, ocr_note: null } },
    ]);
    await page.route(`**/api/reanalysis/proposals/${id}`, (route) =>
      route.fulfill({ json: makeMockProposal({ document_id: id, document_title: failed.title }) }),
    );
    const applied: ApplyRequest[] = [];
    await page.route(`**/api/reanalysis/proposals/${id}/apply`, (route) => {
      applied.push(route.request().postDataJSON());
      return route.fulfill({ json: failed });
    });

    await page.goto('/pruefen');
    await page.getByTestId('reanalysis-panel').getByRole('button', { name: 'Erneut analysieren' }).click();

    const diff = page.getByTestId('proposal-diff');
    await expect(diff).toBeVisible({ timeout: 10_000 });
    await expect(diff.getByTestId('proposal-row-amount')).toContainText('98,50');
    expect(requests).toEqual([{ with_ocr: false }]);

    await diff.getByRole('button', { name: 'Alles übernehmen' }).click();
    await expect.poll(() => applied).toEqual([{ fields: null }]);
    await expect(page.getByText('Änderungen übernommen.')).toBeVisible();
  });
});
