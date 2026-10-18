import { test, expect } from '@playwright/test';
import {
  setupSplitEditorMocks,
  setupDocumentDetailMocks,
  makeSplitBatch,
  MOCK_SPLIT_BATCH,
  MOCK_DOCUMENT_DETAIL,
} from '../helpers/mock.helpers';
import type { DocumentResponse, SplitUndoResponse } from '../helpers/api-types';

// "Aufteilung rückgängig" und KI-Pass (Spezifikation 12, Etappe 3):
// Teildokument-Detail, Aufteilungs-Editor im Ergebnis-Zustand, KI-Gründe im
// Vorschlag. API vollständig gemockt.

const BATCH_ID = MOCK_SPLIT_BATCH.id;
const UNDO_URL = `**/api/split/${BATCH_ID}/undo`;

const PART = {
  ...MOCK_DOCUMENT_DETAIL,
  id: 'doc-part',
  split_batch_id: BATCH_ID,
  split_part_index: 2,
  split_part_count: 2,
  split_source_filename: 'scan_0427.pdf',
  source_pages: '3-6',
  can_undo_split: true,
  split_undo_blocked_reason: null,
} satisfies DocumentResponse;

const DONE_PARTS = [
  { index: 1, pages: [1, 2], job_id: 'job-1', job_status: 'COMPLETED' as const, document_id: 'doc-a', error_message: null, error_code: null },
  { index: 2, pages: [3, 4, 5, 6], job_id: 'job-2', job_status: 'COMPLETED' as const, document_id: 'doc-part', error_message: null, error_code: null },
];

test.describe('Undo a split', () => {
  test('Part detail: undo sends the batch version and opens the editor again', async ({ page }) => {
    const bodies: unknown[] = [];
    await setupDocumentDetailMocks(page, PART);
    await setupSplitEditorMocks(page, makeSplitBatch({ status: 'DONE', version: 7, parts: DONE_PARTS, can_undo: true }), [
      makeSplitBatch({ status: 'DONE', version: 7, parts: DONE_PARTS, can_undo: true }),
      makeSplitBatch({ status: 'PROPOSED', version: 8, boundaries: [
        { after_page: 2, decision: 'cut', score: 5.5, p: 0.99, reasons: ['Bei der letzten Aufteilung geschnitten'], llm: false },
      ] }),
    ]);
    await page.route(UNDO_URL, (route) => {
      bodies.push(route.request().postDataJSON());
      return route.fulfill({ json: {
        batch_id: BATCH_ID, status: 'PROPOSED', removed_document_ids: ['doc-a', 'doc-part'], restored_document_id: null,
      } satisfies SplitUndoResponse });
    });
    await page.goto('/dokumente/doc-part');

    const undo = page.getByTestId('split-undo');
    await expect(undo).toBeEnabled();
    await undo.click();
    const dialog = page.getByRole('alertdialog');
    // Der Dialog nennt die Folge passend zur Herkunft des Stapels (AUTO)
    await expect(dialog).toContainText('Der Stapel kommt mit der letzten Aufteilung als Vorschlag zurück in den Editor');
    await dialog.getByRole('button', { name: 'Rückgängig machen' }).click();

    await expect(page).toHaveURL(new RegExp(`/aufteilen/${BATCH_ID}$`));
    expect(bodies).toEqual([{ version: 7 }]);
    await expect(page.getByText('Die Aufteilung wurde rückgängig gemacht – bitte neu aufteilen.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'In 2 Dokumente aufteilen' })).toBeVisible();
  });

  test('Part detail: undo is disabled with the reason after editing', async ({ page }) => {
    await setupDocumentDetailMocks(page, {
      ...PART,
      can_undo_split: false,
      split_undo_blocked_reason: 'Ein Teil wurde inzwischen bearbeitet – die Aufteilung kann nicht mehr rückgängig gemacht werden.',
    });
    let requested = 0;
    await page.route(UNDO_URL, (route) => { requested += 1; return route.fallback(); });
    await page.goto('/dokumente/doc-part');

    const undo = page.getByTestId('split-undo');
    await expect(undo).toBeDisabled();
    await expect(page.getByTestId('split-undo-reason')).toHaveText(/Ein Teil wurde inzwischen bearbeitet/);
    await expect(undo).toHaveAccessibleDescription(/Ein Teil wurde inzwischen bearbeitet/);
    expect(requested).toBe(0);
  });

  test('Part detail: manual split restores and opens the original', async ({ page }) => {
    const original = { ...MOCK_DOCUMENT_DETAIL, id: 'doc-orig', title: 'Sammeldokument' };
    await setupDocumentDetailMocks(page, PART);
    await setupDocumentDetailMocks(page, original);
    await page.route(`**/api/split/${BATCH_ID}`, (route) => route.fulfill({
      json: makeSplitBatch({ origin: 'MANUAL', status: 'DONE', version: 3, source_document_id: 'doc-orig', can_undo: true }),
    }));
    await page.route(UNDO_URL, (route) => route.fulfill({ json: {
      batch_id: BATCH_ID, status: 'UNDONE', removed_document_ids: ['doc-a', 'doc-part'], restored_document_id: 'doc-orig',
    } satisfies SplitUndoResponse }));
    await page.goto('/dokumente/doc-part');

    await page.getByTestId('split-undo').click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('das Originaldokument ist danach wieder aktiv');
    await dialog.getByRole('button', { name: 'Rückgängig machen' }).click();
    await expect(page).toHaveURL(/\/dokumente\/doc-orig$/);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Sammeldokument');
  });

  test('Part detail: a refused undo shows the reason and refreshes the state', async ({ page }) => {
    const reason = 'Die Stapeldatei wird nicht mehr aufbewahrt – die Aufteilung kann nicht mehr rückgängig gemacht werden.';
    let detailCalls = 0;
    await setupDocumentDetailMocks(page, PART);
    await page.route('**/api/documents/doc-part', (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      detailCalls += 1;
      return route.fulfill({ json: detailCalls === 1 ? PART : { ...PART, can_undo_split: false, split_undo_blocked_reason: reason } });
    });
    await page.route(`**/api/split/${BATCH_ID}`, (route) => route.fulfill({
      json: makeSplitBatch({ status: 'DONE', version: 2, can_undo: true }),
    }));
    await page.route(UNDO_URL, (route) => route.fulfill({ status: 409, json: { detail: reason } }));
    await page.goto('/dokumente/doc-part');

    await page.getByTestId('split-undo').click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Rückgängig machen' }).click();
    await expect(page.getByText(reason)).toHaveCount(2);  // Meldung + Begründung am Button
    await expect(page.getByTestId('split-undo')).toBeDisabled();
    await expect(page.getByTestId('split-undo-reason')).toHaveText(reason);
    await expect(page).toHaveURL(/\/dokumente\/doc-part$/);
  });

  test('Editor: finished split offers undo, or explains why not', async ({ page }) => {
    const bodies: unknown[] = [];
    const done = makeSplitBatch({ status: 'DONE', version: 4, parts: DONE_PARTS, can_undo: true });
    await setupSplitEditorMocks(page, done, [done, makeSplitBatch({ status: 'PROPOSED', version: 5 })]);
    await page.route(UNDO_URL, (route) => {
      bodies.push(route.request().postDataJSON());
      return route.fulfill({ json: {
        batch_id: BATCH_ID, status: 'PROPOSED', removed_document_ids: ['doc-a', 'doc-part'], restored_document_id: null,
      } satisfies SplitUndoResponse });
    });
    await page.goto(`/aufteilen/${BATCH_ID}`);

    await expect(page.getByTestId('split-result')).toContainText('Aufgeteilt');
    await page.getByTestId('split-editor-undo').click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('mit der letzten Aufteilung als Vorschlag zurück in den Editor');
    await dialog.getByRole('button', { name: 'Rückgängig machen' }).click();

    await expect(page.getByRole('button', { name: /aufteilen$/ })).toBeVisible();  // Editor wieder offen
    expect(bodies).toEqual([{ version: 4 }]);
  });

  test('Editor: blocked undo is disabled with its reason', async ({ page }) => {
    await setupSplitEditorMocks(page, makeSplitBatch({
      status: 'DONE', parts: DONE_PARTS, can_undo: false,
      undo_blocked_reason: 'Ein Teil wurde inzwischen bearbeitet – die Aufteilung kann nicht mehr rückgängig gemacht werden.',
    }));
    await page.goto(`/aufteilen/${BATCH_ID}`);
    await expect(page.getByTestId('split-editor-undo')).toBeDisabled();
    await expect(page.getByTestId('split-result')).toContainText('Ein Teil wurde inzwischen bearbeitet');
  });

  test('Part detail: origin, undo and actions fit the viewport (no horizontal scrolling)', async ({ page }) => {
    await setupDocumentDetailMocks(page, {
      ...PART,
      file_type: 'pdf',
      can_undo_split: false,
      split_undo_blocked_reason: 'Ein Teil wurde inzwischen bearbeitet – die Aufteilung kann nicht mehr rückgängig gemacht werden.',
    });
    await page.goto('/dokumente/doc-part');
    await expect(page.getByRole('button', { name: 'Dokument aufteilen' })).toBeVisible();
    await expect(page.getByTestId('split-undo-reason')).toBeVisible();

    const overflow = await page.evaluate(() => {
      const main = document.querySelector('main');
      return {
        page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        main: main ? main.scrollWidth - main.clientWidth : 0,
      };
    });
    expect(overflow).toEqual({ page: 0, main: 0 });
  });
});

test.describe('KI pass in the proposal', () => {
  test('A document inside a running page counter is flagged, not cut', async ({ page }) => {
    await setupSplitEditorMocks(page, makeSplitBatch({
      llm_used: true,
      boundaries: [
        { after_page: 2, decision: 'cut', score: 5.5, p: 0.996, reasons: ['Seitenzähler beginnt neu: Seite 1'], llm: false },
        {
          after_page: 4, decision: 'uncertain', score: -6, p: 0.002, llm: true,
          reasons: [
            'Seitenzähler läuft weiter (Seite 2 → 3)',
            'Neues Dokument innerhalb von Seite 3/4? – bitte prüfen',
            'KI: neues Dokument (80 %) – Rechnung als Anlage',
          ],
        },
      ],
    }));
    await page.goto(`/aufteilen/${BATCH_ID}`);

    await expect(page.getByTestId('split-llm-used')).toHaveText('KI-geprüft');
    await expect(page.getByTestId('split-uncertain-list')).toContainText('Neues Dokument innerhalb von Seite 3/4? – bitte prüfen');
    await expect(page.getByTestId('split-uncertain-list')).toContainText('KI: neues Dokument (80 %)');
    // Nicht automatisch geschnitten: nur der sichere Schnitt zählt
    await expect(page.getByRole('button', { name: 'In 2 Dokumente aufteilen' })).toBeVisible();
    await page.getByTestId('split-marker-4').click();
    await expect(page.getByRole('button', { name: 'In 3 Dokumente aufteilen' })).toBeVisible();
  });

  test('Unavailable KI check is shown instead of the badge', async ({ page }) => {
    await setupSplitEditorMocks(page, makeSplitBatch({
      llm_unavailable: true,
      boundaries: [{ after_page: 1, decision: 'uncertain', score: -0.5, p: 0.38, reasons: ['Anschrift auf Seite 2', 'KI-Prüfung nicht verfügbar'], llm: false }],
    }));
    await page.goto(`/aufteilen/${BATCH_ID}`);
    await expect(page.getByText('KI-Prüfung nicht verfügbar').first()).toBeVisible();
    await expect(page.getByTestId('split-llm-used')).toHaveCount(0);
  });
});
