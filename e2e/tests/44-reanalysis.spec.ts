import { test, expect, Page } from '@playwright/test';
import {
  setupDashboardMocks,
  setupDocumentDetailMocks,
  setupNotificationMocks,
  setupReviewMocks,
  makeMockTask,
  makeMockProposal,
  mockBackgroundTaskPolling,
  MOCK_DOCUMENT_DETAIL,
  MOCK_DOCUMENT_WITH_QUESTIONS,
} from '../helpers/mock.helpers';
import type { ApplyRequest, DocumentResponse, ProposalListResponse, ReanalyzeRequest } from '../helpers/api-types';

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

test.describe('Änderungsvorschläge auf der Prüfen-Seite', () => {
  const PROPOSALS_URL = /\/api\/reanalysis\/proposals(\?.*)?$/;
  const FIELD_COUNTS = { amount: 2, issuer: 2, document_date: 1 };

  function proposalList(total = 2): ProposalListResponse {
    return {
      items: [
        makeMockProposal({ document_id: 'doc-a', document_title: 'Stromrechnung 2026' }),
        makeMockProposal({
          document_id: 'doc-b',
          document_title: 'Telefonrechnung',
          changes: [{ field: 'amount', label: 'Betrag', old: '10.00', new: '12.00' }],
        }),
      ].slice(0, total),
      total,
      field_counts: FIELD_COUNTS,
    };
  }

  test.beforeEach(async ({ page }) => {
    await setupReviewMocks(page);
  });

  test('pruefen: Tab Änderungsvorschläge zeigt Anzahl und Liste', async ({ page }) => {
    await page.route(PROPOSALS_URL, (route) => route.fulfill({ json: proposalList() }));
    await page.goto('/pruefen');

    const tab = page.getByRole('tab', { name: 'Änderungsvorschläge (2)' });
    await expect(tab).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Rückfragen' })).toHaveAttribute('aria-selected', 'true');

    await tab.click();
    await expect(page).toHaveURL(/\/pruefen\?tab=vorschlaege/);
    await expect(tab).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('link', { name: 'Stromrechnung 2026' })).toHaveAttribute('href', '/dokumente/doc-a');
    await expect(page.getByRole('link', { name: 'Telefonrechnung' })).toBeVisible();
    await expect(page.getByTestId('proposal-diff')).toHaveCount(0);

    await page.getByTestId('proposal-entry-doc-a').getByRole('button', { name: 'Änderungen anzeigen' }).click();
    await expect(page.getByTestId('proposal-diff')).toHaveCount(1);
    await expect(page.getByTestId('proposal-row-amount')).toContainText('98,50');
  });

  test('pruefen: Feldfilter lädt mit field=amount', async ({ page }) => {
    const fields: (string | null)[] = [];
    await page.route(PROPOSALS_URL, (route) => {
      fields.push(new URL(route.request().url()).searchParams.get('field'));
      return route.fulfill({ json: proposalList() });
    });
    await page.goto('/pruefen?tab=vorschlaege');

    const filter = page.getByLabel('Feld');
    await expect(filter.locator('option')).toHaveText(['Alle Felder', 'Betrag (2)', 'Aussteller (2)', 'Datum (1)']);
    await filter.selectOption({ label: 'Betrag (2)' });
    await expect.poll(() => fields.at(-1)).toBe('amount');
  });

  test('pruefen: Alle übernehmen fragt nach und startet Task', async ({ page }) => {
    let applied = false;
    await page.route(PROPOSALS_URL, (route) =>
      route.fulfill({ json: applied ? { items: [], total: 0, field_counts: {} } : proposalList() }),
    );
    const bodies: unknown[] = [];
    await page.route('**/api/reanalysis/proposals/apply-all', (route) => {
      bodies.push(route.request().postDataJSON());
      applied = true;
      return route.fulfill({ status: 202, json: makeMockTask({ id: 'task-all', kind: 'reanalysis_apply_all', key: 'global' }) });
    });
    await mockBackgroundTaskPolling(page, 'task-all', [
      { kind: 'reanalysis_apply_all', key: 'global', status: 'SUCCEEDED', result: { applied: 1, conflicts: 1, errors: 0 } },
    ]);
    await page.goto('/pruefen?tab=vorschlaege');

    await page.getByRole('button', { name: 'Alle übernehmen (2)' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('2 Vorschläge');
    expect(bodies).toEqual([]);
    await dialog.getByRole('button', { name: 'Alle übernehmen' }).click();

    await expect.poll(() => bodies).toEqual([{ field: null }]);
    await expect(page.getByText('1 übernommen, 1 mit Konflikt')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Keine offenen Änderungsvorschläge.')).toBeVisible();
  });

  test('pruefen: Alle verwerfen fragt nach und sendet den Filter', async ({ page }) => {
    await page.route(PROPOSALS_URL, (route) => route.fulfill({ json: proposalList() }));
    const bodies: unknown[] = [];
    await page.route('**/api/reanalysis/proposals/discard-all', (route) => {
      bodies.push(route.request().postDataJSON());
      return route.fulfill({ json: { discarded: 2 } });
    });
    await page.goto('/pruefen?tab=vorschlaege');

    await page.getByLabel('Feld').selectOption({ label: 'Betrag (2)' });
    await page.getByRole('button', { name: 'Alle verwerfen (2)' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Alle verwerfen' }).click();
    await expect.poll(() => bodies).toEqual([{ field: 'amount' }]);
    await expect(page.getByText('2 Vorschläge verworfen.')).toBeVisible();
  });

  test('benachrichtigung: REANALYSIS_DONE öffnet Vorschläge-Tab', async ({ page }) => {
    await setupDashboardMocks(page);
    await setupNotificationMocks(page, [{
      id: 'notif-re',
      type: 'REANALYSIS_DONE',
      title: 'Neu-Analyse abgeschlossen',
      message: '12 Vorschläge warten auf Prüfung.',
      is_read: false,
      created_at: '2026-10-24T08:00:00Z',
      document_id: null,
    }]);
    await page.route(PROPOSALS_URL, (route) => route.fulfill({ json: proposalList() }));
    await page.goto('/');

    await page.getByRole('button', { name: /Benachrichtigungen/ }).click();
    await page.getByRole('button', { name: 'Neu-Analyse abgeschlossen – Vorschläge prüfen' }).click();
    await expect(page).toHaveURL(/\/pruefen\?tab=vorschlaege/);
    await expect(page.getByRole('tab', { name: 'Änderungsvorschläge (2)' })).toHaveAttribute('aria-selected', 'true');
  });
});
