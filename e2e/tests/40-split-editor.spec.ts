import { test, expect, Page } from '@playwright/test';
import * as path from 'path';
import {
  setupSplitEditorMocks,
  setupDocumentDetailMocks,
  setupUploadMocks,
  makeSplitBatch,
  makeJob,
  MOCK_SPLIT_BATCH,
  MOCK_DOCUMENT_DETAIL,
} from '../helpers/mock.helpers';
import type { SplitConfirmRequest, SplitConfirmResponse, SplitRequestResponse } from '../helpers/api-types';

// Aufteilen von Stapel-Scans (Spezifikation 12, Etappe 1): Editor, "Dokument
// aufteilen" in der Detailansicht, Upload-Option. API vollstaendig gemockt.

const EDITOR_URL = `/aufteilen/${MOCK_SPLIT_BATCH.id}`;
const TEST_FILES_DIR = path.resolve(__dirname, '../fixtures/test-files');

const marker = (page: Page, n: number) => page.getByTestId(`split-marker-${n}`);
const partLabels = (page: Page) => page.getByTestId('split-part-label');

test.describe('Split editor', () => {
  test('Shows the batch and toggles cuts between pages', async ({ page }) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);

    await expect(page.getByTestId('split-header')).toContainText('„scan_0427.pdf“ · Upload · 6 Seiten');
    await expect(page.getByTestId('split-page-4')).toContainText('Leerseite');
    await expect(partLabels(page)).toHaveText(['Teil 1 · Seiten 1–6']);
    await expect(page.getByRole('button', { name: 'In 1 Dokument aufteilen' })).toBeVisible();

    await marker(page, 2).click();
    await expect(marker(page, 2)).toHaveAttribute('data-state', 'cut');
    await expect(marker(page, 2)).toHaveAttribute('aria-pressed', 'true');
    await expect(partLabels(page)).toHaveText(['Teil 1 · Seiten 1–2', 'Teil 2 · Seiten 3–6']);
    await expect(page.getByRole('button', { name: 'In 2 Dokumente aufteilen' })).toBeVisible();

    await marker(page, 2).click();
    await expect(marker(page, 2)).toHaveAttribute('data-state', 'none');
    await expect(partLabels(page)).toHaveText(['Teil 1 · Seiten 1–6']);
  });

  test('Uncertain transition shows its reason and can be confirmed', async ({ page }) => {
    const reason = 'Neues Dokument innerhalb von Seite 2/4? – bitte prüfen';
    await setupSplitEditorMocks(page, makeSplitBatch({
      boundaries: [
        { after_page: 2, decision: 'cut', p: 0.99, reasons: ['Seitenzähler beginnt neu: Seite 1'], llm: false },
        { after_page: 4, decision: 'uncertain', p: 0.5, reasons: [reason], llm: true },
      ],
    }));
    await page.goto(EDITOR_URL);

    await expect(marker(page, 2)).toHaveAttribute('data-state', 'cut');  // Vorschlag gesetzt
    await expect(marker(page, 4)).toHaveAttribute('data-state', 'uncertain');  // nur hervorgehoben
    expect(await marker(page, 4).getAttribute('title')).toContain(reason);
    await expect(partLabels(page)).toHaveText(['Teil 1 · Seiten 1–2', 'Teil 2 · Seiten 3–6']);

    await marker(page, 4).click();
    await expect(marker(page, 4)).toHaveAttribute('data-state', 'cut');
    await expect(partLabels(page)).toHaveText(['Teil 1 · Seiten 1–2', 'Teil 2 · Seiten 3–4', 'Teil 3 · Seiten 5–6']);
  });

  test('Duplex: a cut inside a sheet asks first', async ({ page }) => {
    await setupSplitEditorMocks(page, makeSplitBatch({ duplex: true, duplex_conf: 0.95 }));
    await page.goto(EDITOR_URL);
    await expect(page.getByText('Duplex erkannt: 3 Blätter')).toBeVisible();

    await marker(page, 1).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Dieser Schnitt trennt Vorder- und Rückseite eines Blatts. Trotzdem schneiden?');
    await dialog.getByRole('button', { name: 'Abbrechen' }).click();
    await expect(marker(page, 1)).toHaveAttribute('data-state', 'none');

    await marker(page, 1).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Trotzdem schneiden' }).click();
    await expect(marker(page, 1)).toHaveAttribute('data-state', 'cut');

    await marker(page, 2).click();  // zwischen zwei Blaettern: keine Rueckfrage
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    await expect(marker(page, 2)).toHaveAttribute('data-state', 'cut');
  });

  test('Keyboard: arrow keys move between transitions, space toggles', async ({ page }) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);
    await marker(page, 1).focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowRight');
    await expect(marker(page, 3)).toBeFocused();
    await page.keyboard.press('Space');
    await expect(marker(page, 3)).toHaveAttribute('data-state', 'cut');
    await page.keyboard.press('ArrowLeft');
    await expect(marker(page, 2)).toBeFocused();
  });

  test('Omitting a page and confirming sends the split', async ({ page }) => {
    const bodies: SplitConfirmRequest[] = [];
    const splitting = makeSplitBatch({
      status: 'SPLITTING',
      version: 2,
      excluded: [4],
      parts: [
        { index: 1, pages: [1, 2, 3], job_id: 'job-301', job_status: 'COMPLETED', document_id: 'doc-301', error_message: null, error_code: null },
        { index: 2, pages: [5, 6], job_id: 'job-302', job_status: 'PENDING', document_id: null, error_message: null, error_code: null },
      ],
    });
    await setupSplitEditorMocks(page, MOCK_SPLIT_BATCH, [MOCK_SPLIT_BATCH, splitting]);
    await page.route(`**/api/split/${MOCK_SPLIT_BATCH.id}/confirm`, (route) => {
      bodies.push(route.request().postDataJSON() as SplitConfirmRequest);
      return route.fulfill({ status: 202, json: { batch_id: MOCK_SPLIT_BATCH.id, job_ids: ['job-301', 'job-302'] } satisfies SplitConfirmResponse });
    });
    await page.goto(EDITOR_URL);

    await marker(page, 3).click();
    const blankPage = page.getByTestId('split-page-4');
    await blankPage.getByRole('button', { name: 'Seite weglassen' }).click();
    await expect(blankPage).toContainText('Weggelassen');
    await expect(partLabels(page)).toHaveText(['Teil 1 · Seiten 1–3', 'Teil 2 · Seiten 5–6']);

    // rueckgaengig machbar
    await blankPage.getByRole('button', { name: 'Wieder aufnehmen' }).click();
    await expect(partLabels(page)).toHaveText(['Teil 1 · Seiten 1–3', 'Teil 2 · Seiten 4–6']);
    await blankPage.getByRole('button', { name: 'Seite weglassen' }).click();

    await page.getByRole('button', { name: 'In 2 Dokumente aufteilen' }).click();
    await expect.poll(() => bodies).toEqual([
      { version: 1, parts: [{ pages: [1, 2, 3] }, { pages: [5, 6] }], excluded: [4] },
    ]);
    await expect(page.getByText('Wird in 2 Dokumente aufgeteilt.')).toBeVisible();
    const result = page.getByTestId('split-result');
    await expect(result).toContainText('Teil 1 · Seiten 1–3');
    await expect(result.getByRole('link', { name: 'Anzeigen' })).toHaveAttribute('href', '/dokumente/doc-301');
  });

  test('A part made only of blank pages blocks confirming', async ({ page }) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);
    await marker(page, 3).click();
    await marker(page, 4).click();
    await expect(page.getByTestId('split-error')).toHaveText('Teil 2 besteht nur aus Leerseiten.');
    await expect(page.getByRole('button', { name: 'In 3 Dokumente aufteilen' })).toBeDisabled();
  });

  test('Conflict (409) asks to reload', async ({ page }) => {
    let gets = 0;
    await setupSplitEditorMocks(page);
    await page.route(`**/api/split/${MOCK_SPLIT_BATCH.id}`, (route) => {
      gets += 1;
      return route.fallback();
    });
    await page.route(`**/api/split/${MOCK_SPLIT_BATCH.id}/confirm`, (route) =>
      route.fulfill({ status: 409, json: { detail: 'Der Stapel wurde inzwischen geändert. Bitte neu laden.' } })
    );
    await page.goto(EDITOR_URL);
    await page.getByRole('button', { name: 'In 1 Dokument aufteilen' }).click();

    const alert = page.getByRole('alert').filter({ hasText: 'Der Stapel wurde inzwischen geändert.' });
    await expect(alert).toBeVisible();
    await expect(page.getByRole('button', { name: 'In 1 Dokument aufteilen' })).toBeDisabled();
    const before = gets;
    await alert.getByRole('button', { name: 'Neu laden' }).click();
    await expect.poll(() => gets).toBe(before + 1);
    await expect(alert).toHaveCount(0);
  });

  test('Shows detection progress until the editor is ready', async ({ page }) => {
    await page.clock.install();
    const detecting = makeSplitBatch({ status: 'DETECTING', page_count: 44, pages_done: 16, pages: [] });
    const ready = makeSplitBatch({ page_count: 44, pages: [] });
    ready.pages = Array.from({ length: 44 }, (_, i) => ({ ...MOCK_SPLIT_BATCH.pages[0], n: i + 1 }));
    await setupSplitEditorMocks(page, MOCK_SPLIT_BATCH, [detecting, ready]);
    await page.goto(EDITOR_URL);

    await expect(page.getByTestId('split-detecting')).toContainText('Seite 17 von 44 wird erkannt …');
    await expect(page.getByTestId('split-grid')).toHaveCount(0);  // Editor gesperrt

    await page.clock.runFor(2100);
    await expect(page.getByTestId('split-grid')).toBeVisible();
    await expect(page.getByTestId('split-detecting')).toHaveCount(0);
  });

  test('Discard asks for confirmation and deletes the batch', async ({ page }) => {
    const bodies: unknown[] = [];
    await setupSplitEditorMocks(page);
    await page.route(`**/api/split/${MOCK_SPLIT_BATCH.id}/discard`, (route) => {
      bodies.push(route.request().postDataJSON());
      return route.fulfill({ json: makeSplitBatch({ status: 'DISCARDED' }) });
    });
    await page.goto(EDITOR_URL);

    await page.getByRole('button', { name: 'Verwerfen' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Die Datei wird gelöscht');
    await dialog.getByRole('button', { name: 'Verwerfen' }).click();
    await expect.poll(() => bodies).toEqual([{ version: 1 }]);
    await expect(page).toHaveURL(/\/$/);
  });

  test('Manual split: keep whole returns to the document, no discard', async ({ page }) => {
    const manual = makeSplitBatch({
      origin: 'MANUAL', job_source: 'SPLIT_REQUEST', source_document_id: MOCK_DOCUMENT_DETAIL.id,
    });
    await setupDocumentDetailMocks(page);
    await setupSplitEditorMocks(page, manual);
    await page.route(`**/api/split/${manual.id}/keep-whole`, (route) =>
      route.fulfill({ json: { ...manual, status: 'KEPT_WHOLE' } })
    );
    await page.goto(EDITOR_URL);
    await expect(page.getByTestId('split-header')).toContainText('· Dokument ·');
    await expect(page.getByRole('button', { name: 'Verwerfen' })).toHaveCount(0);

    await page.getByRole('button', { name: 'Als ein Dokument behalten' }).click();
    await expect(page).toHaveURL(new RegExp(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}$`));
    await expect(page.getByRole('heading', { level: 1, name: MOCK_DOCUMENT_DETAIL.title })).toBeVisible();
  });

  test('Missing stack file: no conflict banner, only discarding is offered', async ({ page }) => {
    const manual = makeSplitBatch({
      origin: 'MANUAL', job_source: 'SPLIT_REQUEST', source_document_id: MOCK_DOCUMENT_DETAIL.id,
    });
    const missingText = 'Die Stapeldatei ist nicht mehr vorhanden – der Stapel kann nur noch verworfen werden.';
    const missing = makeSplitBatch({ ...manual, error_code: 'SPLIT_SOURCE_MISSING', error_message: missingText });
    await setupSplitEditorMocks(page, manual, [manual, missing]);
    await page.route(`**/api/split/${manual.id}/confirm`, (route) =>
      route.fulfill({ status: 409, json: { detail: missingText, code: 'SPLIT_SOURCE_MISSING' } })
    );
    await page.goto(EDITOR_URL);
    await expect(page.getByRole('button', { name: 'Verwerfen' })).toHaveCount(0);  // MANUAL

    await page.getByRole('button', { name: 'In 1 Dokument aufteilen' }).click();
    await expect(page.getByTestId('split-source-missing')).toContainText(missingText);
    await expect(page.getByText('Der Stapel wurde inzwischen geändert.')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'In 1 Dokument aufteilen' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Als ein Dokument behalten' })).toBeDisabled();

    await page.getByRole('button', { name: 'Verwerfen' }).click();
    // MANUAL: nur der Stapel wird verworfen, nicht "die Datei"
    await expect(page.getByRole('alertdialog')).toContainText('Das Originaldokument bleibt unverändert.');
  });

  test('Large preview pages through the batch', async ({ page }) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);
    await page.getByRole('button', { name: 'Seite 2 groß anzeigen' }).click();

    const preview = page.getByTestId('split-preview');
    await expect(preview).toContainText('Seite 2 von 6');
    await expect(preview.locator('img')).toHaveAttribute('src', `/api/split/${MOCK_SPLIT_BATCH.id}/pages/2/preview`);
    await page.keyboard.press('ArrowRight');
    await expect(preview).toContainText('Seite 3 von 6');
    await page.keyboard.press('Escape');
    await expect(preview).toHaveCount(0);
  });

  test('Layout: two columns and a fixed action bar on mobile', async ({ page }, testInfo) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);
    const box = async (n: number) => (await page.getByTestId(`split-page-${n}`).boundingBox())!;
    const [p1, p2, p3] = [await box(1), await box(2), await box(3)];
    const position = await page.getByTestId('split-actions').evaluate((el) => getComputedStyle(el).position);
    if (testInfo.project.name === 'mobile') {
      expect(p2.y).toBeCloseTo(p1.y, 0);
      expect(p3.y).toBeGreaterThan(p1.y + p1.height / 2);  // 2 Spalten
      expect(position).toBe('fixed');
      // Kein Aktions-Button wird von der Bottom-Navigation verdeckt — auch nicht
      // vom hervorstehenden Scan-Button (oberste Kante aller Nav-Elemente).
      const navTop = await page.getByRole('navigation', { name: 'Navigation' }).filter({ visible: true }).evaluate(
        (nav) => Math.min(...[nav, ...nav.querySelectorAll('*')].map((el) => el.getBoundingClientRect().top))
      );
      const actions = page.getByTestId('split-actions');
      for (const name of ['In 1 Dokument aufteilen', 'Als ein Dokument behalten', 'Verwerfen']) {
        const button = (await actions.getByRole('button', { name }).boundingBox())!;
        expect(button.y + button.height).toBeLessThanOrEqual(navTop);
      }
      // Die letzte Seite laesst sich ganz ueber die Leiste scrollen.
      await page.locator('main').evaluate((main) => main.scrollTo(0, main.scrollHeight));
      const bar = (await actions.boundingBox())!;
      const last = (await page.getByTestId('split-page-6').boundingBox())!;
      expect(last.y + last.height).toBeLessThanOrEqual(bar.y);
    } else {
      expect(p3.y).toBeCloseTo(p1.y, 0);  // mehrere Seiten pro Zeile
      expect(position).toBe('static');
    }
  });
});

test.describe('Split entry points', () => {
  test('Document detail: "Dokument aufteilen" opens the editor after confirming', async ({ page }) => {
    let requested = 0;
    await setupDocumentDetailMocks(page);
    await setupSplitEditorMocks(page, makeSplitBatch({ status: 'DETECTING', pages_done: 0, pages: [] }));
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}/split`, (route) => {
      requested += 1;
      return route.fulfill({ status: 202, json: { batch_id: MOCK_SPLIT_BATCH.id, job_id: 'job-300' } satisfies SplitRequestResponse });
    });
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    await page.getByRole('button', { name: 'Dokument aufteilen' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Ablagebereich „Privat“');
    await expect(dialog).toContainText('manuell vergebenen Tags');
    await expect(dialog).toContainText('erst nach der Archivierung aller Teile als gelöscht markiert');
    await dialog.getByRole('button', { name: 'Aufteilen' }).click();

    await expect(page).toHaveURL(new RegExp(`/aufteilen/${MOCK_SPLIT_BATCH.id}$`));
    expect(requested).toBe(1);
    await expect(page.getByTestId('split-detecting')).toContainText('Seite 1 von 6 wird erkannt');
  });

  test('Document detail: rejected split shows the reason', async ({ page }) => {
    await setupDocumentDetailMocks(page);
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}/split`, (route) =>
      route.fulfill({ status: 422, json: { detail: 'Das Dokument hat nur eine Seite.' } })
    );
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    await page.getByRole('button', { name: 'Dokument aufteilen' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Aufteilen' }).click();
    await expect(page.getByText('Das Dokument hat nur eine Seite.')).toBeVisible();
  });

  test('Document detail: no split for images, provenance for parts', async ({ page }) => {
    const part = {
      ...MOCK_DOCUMENT_DETAIL,
      id: 'doc-part',
      split_batch_id: MOCK_SPLIT_BATCH.id,
      split_part_index: 2,
      split_part_count: 3,
      split_source_filename: 'scan_0427.pdf',
      source_pages: '3-44',
      can_undo_split: false,
    };
    await setupDocumentDetailMocks(page, part);
    await page.goto('/dokumente/doc-part');
    const origin = page.getByTestId('split-origin');
    await expect(origin).toContainText('Teil 2 von 3 aus „scan_0427.pdf“ (Seiten 3–44)');
    await expect(origin.getByRole('link', { name: 'Zum Stapel' })).toHaveAttribute('href', EDITOR_URL);

    const image = { ...MOCK_DOCUMENT_DETAIL, id: 'doc-img', file_type: 'jpg' };
    await setupDocumentDetailMocks(page, image);
    await page.goto('/dokumente/doc-img');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Dokument aufteilen' })).toHaveCount(0);
  });

  test('Upload option sends split_check and links to the editor', async ({ page }) => {
    await page.clock.install();
    await setupUploadMocks(page);
    let uploadBody = '';
    await page.route('**/api/documents/upload', (route) => {
      uploadBody = route.request().postData() ?? '';
      return route.fulfill({
        json: { uploaded: [{ job_id: 'job-100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'OK' }], rejected: [] },
      });
    });
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({
        json: makeJob({ id: 'job-100', original_filename: 'rechnung.pdf', status: 'AWAITING_SPLIT', split_batch_id: MOCK_SPLIT_BATCH.id, split_role: 'SOURCE' }),
      })
    );
    await page.goto('/upload');

    await page.getByLabel('Enthält mehrere Dokumente – vor der Analyse aufteilen').check();
    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));
    await expect.poll(() => uploadBody).toContain('name="split_check"');
    expect(uploadBody).toMatch(/name="split_check"\r\n\r\ntrue/);

    await page.clock.runFor(3100);
    const row = page.getByTestId('upload-job');
    await expect(row).toContainText('Wartet auf Aufteilung');
    await expect(row.getByRole('link', { name: 'Aufteilen' })).toHaveAttribute('href', EDITOR_URL);
  });

  test('Upload without the option sends no split_check', async ({ page }) => {
    await setupUploadMocks(page);
    let uploadBody = '';
    await page.route('**/api/documents/upload', (route) => {
      uploadBody = route.request().postData() ?? '';
      return route.fulfill({ json: { uploaded: [], rejected: [] } });
    });
    await page.goto('/upload');
    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));
    await expect.poll(() => uploadBody).toContain('rechnung.pdf');
    expect(uploadBody).not.toContain('split_check');
  });
});
