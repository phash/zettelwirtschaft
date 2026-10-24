import { test, expect, Page } from '@playwright/test';
import {
  setupDashboardMocks,
  setupDocumentsMocks,
  makeMockRun,
  makeMockRunPreview,
  MOCK_DOCUMENTS,
  setupDocumentDetailMocks,
  setupNotificationMocks,
  setupReviewMocks,
  makeMockTask,
  makeMockProposal,
  mockBackgroundTaskPolling,
  MOCK_DOCUMENT_DETAIL,
  MOCK_DOCUMENT_WITH_QUESTIONS,
} from '../helpers/mock.helpers';
import type { ApplyRequest, DocumentResponse, ProposalListResponse, ReanalyzeRequest, RunResponse } from '../helpers/api-types';

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

  test('detail: ungespeicherte Eingaben sperren Übernehmen und Verwerfen', async ({ page }) => {
    await page.route(`**/api/documents/${DOC.id}`, (route) =>
      route.request().method() === 'GET' ? route.fulfill({ json: { ...DOC, has_proposal: true } }) : route.fallback(),
    );
    await page.route(`**/api/reanalysis/proposals/${DOC.id}`, (route) => route.fulfill({ json: makeMockProposal() }));

    await page.goto(DOC_URL);
    const diff = page.getByTestId('proposal-diff');
    const buttons = ['Auswahl übernehmen', 'Alles übernehmen', 'Verwerfen'].map((name) => diff.getByRole('button', { name }));
    for (const button of buttons) await expect(button).toBeEnabled();
    await expect(diff.getByTestId('proposal-blocked')).toHaveCount(0);

    const title = page.getByLabel('Titel');
    await title.fill('Eigener Titel');
    for (const button of buttons) await expect(button).toBeDisabled();
    await expect(diff.getByTestId('proposal-blocked')).toHaveText('Bitte erst speichern oder Änderungen verwerfen.');

    // Eingabe zurückgenommen -> wieder frei.
    await title.fill(DOC.title);
    for (const button of buttons) await expect(button).toBeEnabled();
    await expect(diff.getByTestId('proposal-blocked')).toHaveCount(0);
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

// ------------------------------------------------------------------
// Massenlauf aus der Dokumentliste
// ------------------------------------------------------------------

test.describe('Massenlauf in der Dokumentliste', () => {
  const [DOC_A, DOC_B] = MOCK_DOCUMENTS.items;
  const POLL_MS = 5000;
  type RunBody = { document_ids: string[] | null; outdated: boolean; with_ocr: boolean };

  /** Mockt Vorschau + Start; gibt die empfangenen Bodies zurück. */
  async function mockRunEndpoints(page: Page, opts: { preview?: (body: RunBody) => object; started?: RunResponse } = {}) {
    const previews: RunBody[] = [];
    const starts: RunBody[] = [];
    await page.route('**/api/reanalysis/runs/preview', (route) => {
      const body = route.request().postDataJSON() as RunBody;
      previews.push(body);
      return route.fulfill({ json: opts.preview ? opts.preview(body) : makeMockRunPreview({ eligible: 2 }) });
    });
    await page.route('**/api/reanalysis/runs', (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      starts.push(route.request().postDataJSON() as RunBody);
      return route.fulfill({ status: 201, json: opts.started ?? makeMockRun({ total: 2, done: 0, proposed: 0 }) });
    });
    return { previews, starts };
  }

  /** Mockt GET /runs/current mit einem veränderlichen Lauf. */
  async function mockCurrentRun(page: Page, initial: RunResponse | null) {
    const state = { run: initial, polls: 0 };
    await page.route('**/api/reanalysis/runs/current', (route) => {
      state.polls += 1;
      return state.run
        ? route.fulfill({ json: state.run })
        : route.fulfill({ status: 404, json: { detail: 'Kein Lauf.' } });
    });
    return state;
  }

  test.beforeEach(async ({ page }) => {
    await setupDocumentsMocks(page);
  });

  test('liste: Auswahl zeigt Aktionsleiste und startet Lauf mit IDs', async ({ page }) => {
    const { previews, starts } = await mockRunEndpoints(page);
    await page.goto('/dokumente');
    await expect(page.getByTestId('selection-bar')).toHaveCount(0);

    await page.getByRole('checkbox', { name: `${DOC_A.title} auswählen` }).check();
    await page.getByRole('checkbox', { name: `${DOC_B.title} auswählen` }).check();
    const bar = page.getByTestId('selection-bar');
    await expect(bar).toContainText('2 ausgewählt');
    await expect(bar.getByRole('button', { name: 'Auswahl aufheben' })).toBeVisible();

    await bar.getByRole('button', { name: 'Neu analysieren' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('reanalyze-preview')).toContainText('2 Dokumente');
    expect(previews).toEqual([{ document_ids: [DOC_A.id, DOC_B.id], outdated: false, with_ocr: false }]);

    await dialog.getByRole('button', { name: 'Neu analysieren' }).click();
    await expect.poll(() => starts).toEqual([{ document_ids: [DOC_A.id, DOC_B.id], outdated: false, with_ocr: false }]);

    // Auswahl geleert, Laufstatus sofort sichtbar
    await expect(page.getByTestId('selection-bar')).toHaveCount(0);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page.getByTestId('reanalysis-run-status')).toContainText('Neu-Analyse: 0 von 2');
  });

  test('liste: Auswahl aufheben leert die Auswahl', async ({ page }) => {
    await mockRunEndpoints(page);
    await page.goto('/dokumente');
    const box = page.getByRole('checkbox', { name: `${DOC_A.title} auswählen` });
    await box.check();
    await page.getByTestId('selection-bar').getByRole('button', { name: 'Auswahl aufheben' }).click();
    await expect(page.getByTestId('selection-bar')).toHaveCount(0);
    await expect(box).not.toBeChecked();
  });

  test('liste: Alle auf dieser Seite wählt alle Zeilen (Desktop)', async ({ page, isMobile }) => {
    test.skip(isMobile, 'Tabellenkopf nur auf dem Desktop');
    await page.goto('/dokumente');
    const all = page.getByRole('checkbox', { name: 'Alle auf dieser Seite auswählen' });
    await all.check();
    await expect(page.getByTestId('selection-bar')).toContainText(`${MOCK_DOCUMENTS.items.length} ausgewählt`);
    await all.uncheck();
    await expect(page.getByTestId('selection-bar')).toHaveCount(0);
  });

  test('liste: Filterwechsel leert die Auswahl', async ({ page }) => {
    await page.goto('/dokumente');
    await page.getByRole('checkbox', { name: `${DOC_A.title} auswählen` }).check();
    await expect(page.getByTestId('selection-bar')).toBeVisible();
    await page.getByLabel('Typ').selectOption('RECHNUNG');
    await expect(page.getByTestId('selection-bar')).toHaveCount(0);
  });

  test('liste: Mit aktuellem Modell zeigt Vorschau-Zahlen und OCR lädt neu', async ({ page }) => {
    const { previews, starts } = await mockRunEndpoints(page, {
      preview: (body) => body.with_ocr
        ? makeMockRunPreview({ eligible: 9, no_text_excluded: 0, reviewed_excluded: 3, model: MODEL })
        : makeMockRunPreview({ eligible: 5, no_text_excluded: 2, reviewed_excluded: 3, model: MODEL }),
    });
    await page.goto('/dokumente');

    await page.getByRole('button', { name: 'Mit aktuellem Modell neu analysieren …' }).click();
    const dialog = page.getByRole('dialog');
    // Der Titel nennt das aktuelle Modell (aus der Vorschau).
    await expect(dialog.getByRole('heading')).toHaveText(`Alle, die nicht mit ${MODEL} analysiert wurden`);
    const preview = dialog.getByTestId('reanalyze-preview');
    await expect(preview).toContainText('5 Dokumente, ca. 3 Min.');
    await expect(preview).toContainText('3 freigegebene bleiben unverändert');
    await expect(preview).toContainText('2 ohne erkannten Text');
    expect(previews).toEqual([{ document_ids: null, outdated: true, with_ocr: false }]);

    await dialog.getByLabel('Text neu erkennen (OCR) — dauert länger').check();
    await expect(preview).toContainText('9 Dokumente');
    await expect(preview).not.toContainText('ohne erkannten Text');
    expect(previews.at(-1)).toEqual({ document_ids: null, outdated: true, with_ocr: true });

    await dialog.getByRole('button', { name: 'Neu analysieren' }).click();
    await expect.poll(() => starts).toEqual([{ document_ids: null, outdated: true, with_ocr: true }]);
  });

  test('liste: eligible 0 deaktiviert Start', async ({ page }) => {
    const { starts } = await mockRunEndpoints(page, {
      preview: () => makeMockRunPreview({ eligible: 0, reviewed_excluded: 4 }),
    });
    await page.goto('/dokumente');
    await page.getByRole('button', { name: 'Mit aktuellem Modell neu analysieren …' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByTestId('reanalyze-preview')).toContainText('0 Dokumente');
    await expect(dialog.getByRole('button', { name: 'Neu analysieren' })).toBeDisabled();
    expect(starts).toEqual([]);
  });

  test('liste: laufender Lauf (409) zeigt Hinweis und dessen Status', async ({ page }) => {
    const active = makeMockRun({ id: 'run-active', total: 7, done: 3 });
    await page.route('**/api/reanalysis/runs/preview', (route) => route.fulfill({ json: makeMockRunPreview() }));
    await page.route('**/api/reanalysis/runs', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 409, json: { detail: 'Es läuft bereits eine Neu-Analyse.', run: active } })
        : route.fallback(),
    );
    await page.goto('/dokumente');
    await page.getByRole('button', { name: 'Mit aktuellem Modell neu analysieren …' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Neu analysieren' }).click();

    await expect(page.getByText('Es läuft bereits eine Neu-Analyse.')).toBeVisible();
    await expect(page.getByTestId('reanalysis-run-status')).toContainText('Neu-Analyse: 3 von 7');
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });

  test('liste: Laufstatus pausieren/fortsetzen', async ({ page }) => {
    await page.clock.install();
    const state = await mockCurrentRun(page, makeMockRun({ total: 4, done: 1 }));
    const actions: string[] = [];
    await page.route(/\/api\/reanalysis\/runs\/run-1\/(pause|resume|cancel)$/, (route) => {
      const action = route.request().url().split('/').pop() as string;
      actions.push(action);
      const status = action === 'pause' ? 'PAUSED' : action === 'resume' ? 'RUNNING' : 'CANCELLED';
      state.run = makeMockRun({
        total: 4, done: 1, status,
        finished_at: action === 'cancel' ? new Date().toISOString() : null,
      });
      return route.fulfill({ json: state.run });
    });
    await page.goto('/dokumente');

    const status = page.getByTestId('reanalysis-run-status');
    await expect(status).toContainText('Neu-Analyse: 1 von 4');

    // Polling alle 5 s übernimmt den Fortschritt
    state.run = makeMockRun({ total: 4, done: 2 });
    await page.clock.runFor(POLL_MS);
    await expect(status).toContainText('Neu-Analyse: 2 von 4');

    await status.getByRole('button', { name: 'Pausieren' }).click();
    await expect(status).toContainText('pausiert');
    await status.getByRole('button', { name: 'Fortsetzen' }).click();
    await expect(status.getByRole('button', { name: 'Pausieren' })).toBeVisible();
    expect(actions).toEqual(['pause', 'resume']);

    // Abbrechen fragt nach
    await status.getByRole('button', { name: 'Abbrechen' }).click();
    const confirm = page.getByRole('alertdialog');
    await expect(confirm).toContainText('Neu-Analyse abbrechen?');
    await confirm.getByRole('button', { name: 'Lauf abbrechen' }).click();
    await expect.poll(() => actions).toEqual(['pause', 'resume', 'cancel']);
    await expect(status).toContainText('Neu-Analyse abgebrochen');

    // Endzustand: kein weiteres Polling
    const polls = state.polls;
    await page.clock.runFor(POLL_MS * 3);
    expect(state.polls).toBe(polls);
  });

  test('liste: wartet auf KI wird angezeigt', async ({ page }) => {
    await mockCurrentRun(page, makeMockRun({ status: 'WAITING_AI', last_error: 'Ollama ist nicht erreichbar.' }));
    await page.goto('/dokumente');
    const status = page.getByTestId('reanalysis-run-status');
    await expect(status).toContainText('wartet auf KI');
    await expect(status).toContainText('Ollama ist nicht erreichbar.');
  });

  test('liste: fertiger Lauf zeigt Zusammenfassung, Link und Fehler', async ({ page }) => {
    await mockCurrentRun(page, makeMockRun({
      status: 'DONE', total: 4, done: 4, proposed: 2, unchanged: 1, failed: 1,
      finished_at: new Date().toISOString(),
      failures: [{ document_id: 'doc-x', title: 'Defekte Rechnung', error: 'Zeitüberschreitung' }],
    }));
    await page.goto('/dokumente');
    const status = page.getByTestId('reanalysis-run-status');
    await expect(status).toContainText('Neu-Analyse abgeschlossen');
    await expect(status).toContainText('2 mit Vorschlag');
    await expect(status.getByRole('link', { name: 'Vorschläge prüfen' })).toHaveAttribute('href', '/pruefen?tab=vorschlaege');
    await status.getByText('Fehler anzeigen').click();
    await expect(status).toContainText('Defekte Rechnung');
    await expect(status).toContainText('Zeitüberschreitung');
  });

  test('liste: alter beendeter Lauf bleibt ausgeblendet', async ({ page }) => {
    const state = await mockCurrentRun(page, makeMockRun({
      status: 'DONE', total: 4, done: 4,
      finished_at: new Date(Date.now() - 25 * 3600 * 1000).toISOString(),
    }));
    await page.goto('/dokumente');
    await expect(page.getByRole('link', { name: `Dokument ${DOC_A.title} öffnen` })).toBeVisible();
    await expect.poll(() => state.polls).toBeGreaterThan(0);
    await expect(page.getByTestId('reanalysis-run-status')).toHaveCount(0);
  });

  test('mobile: Karten-Checkbox wählt aus', async ({ page, isMobile }) => {
    test.skip(!isMobile, 'Karten nur auf dem Smartphone');
    await page.goto('/dokumente');
    const card = page.getByTestId('document-card').filter({ hasText: DOC_A.title });
    await card.getByRole('checkbox', { name: `${DOC_A.title} auswählen` }).check();
    await expect(page.getByTestId('selection-bar')).toContainText('1 ausgewählt');
    // Steuer-Checkbox bleibt eigenständig
    await expect(card.getByRole('checkbox', { name: `${DOC_A.title} steuerrelevant` })).toBeChecked();
  });
});
