import { test, expect, Page } from '@playwright/test';
import {
  setupReviewMocks,
  setupDashboardMocks,
  setupNotificationMocks,
  setupSettingsMocks,
  setupSplitEditorMocks,
  makeSplitBatch,
  makeSplitBatchList,
  makeSplitListItem,
  makeSplitPages,
  mockSplitSettings,
  MOCK_REVIEW_STATS,
  MOCK_SPLIT_BATCH,
  TINY_PNG,
} from '../helpers/mock.helpers';
import type { NotificationResponse, ReviewStats } from '../helpers/api-types';

// Aufteilen von Stapel-Scans (Spezifikation 12, Etappe 2): automatische
// Pruefung -> Abschnitt in "Prüfen", Benachrichtigung, Badge, Einstellungen,
// Leerseiten-Etiketten im Editor. API vollstaendig gemockt.

async function mockSplitList(page: Page, items = [
  makeSplitListItem(),
  makeSplitListItem({ id: 'split-2', original_filename: 'brief.pdf', job_source: 'EMAIL', page_count: 3, proposed_parts: 1, uncertain: 1 }),
]) {
  const requests: string[] = [];
  await page.route('**/api/split?*', (route) => {
    requests.push(route.request().url());
    return route.fulfill({ json: makeSplitBatchList(items) });
  });
  await page.route('**/api/split/*/pages/*/*', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: TINY_PNG })
  );
  return requests;
}

test.describe('Split stacks in review', () => {
  test('Review page lists stacks waiting for a split', async ({ page }) => {
    await setupReviewMocks(page);
    const requests = await mockSplitList(page);
    await page.goto('/pruefen');

    const section = page.getByTestId('review-split-batches');
    await expect(section.getByRole('heading', { name: 'Stapel zum Aufteilen (2)' })).toBeVisible();
    await expect.poll(() => new URL(requests[0]).searchParams.get('status')).toBe('PROPOSED');

    const first = page.getByTestId('review-split-split-1');
    await expect(first).toContainText('„scan_0427.pdf“');
    await expect(first).toContainText('44 Seiten · Watch-Ordner');
    await expect(first).toContainText('Vorschlag: 2 Dokumente');
    await expect(page.getByTestId('review-split-split-2')).toContainText('Möglicherweise mehrere Dokumente');

    // Rückfragen bleiben darunter erreichbar
    await expect(page.getByTestId('review-counter')).toHaveText('Noch 2 Dokumente offen');

    await setupSplitEditorMocks(page);
    await first.click();
    await expect(page).toHaveURL(/\/aufteilen\/split-1$/);
    await expect(page.getByTestId('split-editor')).toBeVisible();
  });

  test('No section without waiting stacks', async ({ page }) => {
    await setupReviewMocks(page);
    await page.goto('/pruefen');
    await expect(page.getByTestId('review-counter')).toBeVisible();
    await expect(page.getByTestId('review-split-batches')).toHaveCount(0);
  });

  test('Badge counts documents and stacks', async ({ page, isMobile }) => {
    test.skip(!!isMobile, 'Badge in der Sidebar nur auf dem Desktop');
    await setupDashboardMocks(page);
    await page.route('**/api/review/stats', (route) =>
      route.fulfill({ json: { ...MOCK_REVIEW_STATS, open_documents: 1, split_batches: 2 } satisfies ReviewStats })
    );
    await page.goto('/');
    await expect(page.getByTestId('review-badge').first()).toHaveText(/^3\s*offen$/);
  });

  test('SPLIT_NEEDED notification leads to the review page', async ({ page }) => {
    await setupDashboardMocks(page);
    await setupReviewMocks(page);
    const note: NotificationResponse = {
      id: 'notif-split',
      type: 'SPLIT_NEEDED',
      title: 'Stapel aufteilen',
      message: '„scan_0427.pdf“ enthält vermutlich 2 Dokumente – bitte prüfen.',
      is_read: false,
      created_at: '2026-10-18T09:05:00Z',
      document_id: null,
    };
    await setupNotificationMocks(page, [note]);
    await mockSplitList(page, [makeSplitListItem()]);
    await page.goto('/');

    await page.getByRole('button', { name: /Benachrichtigungen/ }).click();
    await page.getByRole('button', { name: 'Stapel aufteilen – Stapel prüfen' }).click();
    await expect(page).toHaveURL(/\/pruefen$/);
    await expect(page.getByTestId('review-split-batches')).toBeVisible();
  });
});

test.describe('Split settings', () => {
  test('Automatic check can be switched off and the minimum changed', async ({ page }) => {
    await setupSettingsMocks(page);
    const puts = await mockSplitSettings(page);
    await page.goto('/einstellungen');

    const card = page.getByTestId('split-settings');
    await expect(card.getByRole('heading', { name: 'Verarbeitung' })).toBeVisible();
    const toggle = card.getByTestId('split-auto-enabled');
    await expect(toggle).toBeChecked();
    await expect(card.getByTestId('split-min-pages')).toHaveValue('3');

    await card.getByTestId('split-min-pages').fill('5');
    await card.getByRole('button', { name: 'Mindestseitenzahl speichern' }).click();
    await expect.poll(() => puts.at(-1)).toEqual({ auto_enabled: true, min_pages: 5, use_llm: true });

    await toggle.uncheck();
    await expect.poll(() => puts.at(-1)).toEqual({ auto_enabled: false, min_pages: 5, use_llm: true });
    await expect(card.getByTestId('split-min-pages')).toBeDisabled();
  });

  test('Invalid minimum is rejected without request', async ({ page }) => {
    await setupSettingsMocks(page);
    const puts = await mockSplitSettings(page);
    await page.goto('/einstellungen');

    const card = page.getByTestId('split-settings');
    await expect(card.getByTestId('split-min-pages')).toHaveValue('3');
    await card.getByTestId('split-min-pages').fill('1');
    await card.getByRole('button', { name: 'Mindestseitenzahl speichern' }).click();
    await expect(page.getByText('Die Mindestseitenzahl muss zwischen 2 und 100 liegen.')).toBeVisible();
    expect(puts).toHaveLength(0);
  });

  test('Switch saves the stored minimum, not an unsaved input', async ({ page }) => {
    await setupSettingsMocks(page);
    const puts = await mockSplitSettings(page);
    await page.goto('/einstellungen');

    const card = page.getByTestId('split-settings');
    await expect(card.getByTestId('split-min-pages')).toHaveValue('3');
    await card.getByTestId('split-min-pages').fill('1');
    await card.getByTestId('split-auto-enabled').uncheck();
    await expect.poll(() => puts.at(-1)).toEqual({ auto_enabled: false, min_pages: 3, use_llm: true });
  });

  test('KI check can be switched off independently', async ({ page }) => {
    await setupSettingsMocks(page);
    const puts = await mockSplitSettings(page, { auto_enabled: false, min_pages: 4, use_llm: true, max_pages: 100 });
    await page.goto('/einstellungen');

    const card = page.getByTestId('split-settings');
    const llm = card.getByTestId('split-use-llm');
    await expect(llm).toBeChecked();
    await expect(llm).toBeEnabled();  // auch bei abgeschalteter Automatik (gilt für „Dokument aufteilen“)
    await expect(card).toContainText('Unsichere Stellen zusätzlich per KI prüfen');
    await llm.uncheck();
    await expect.poll(() => puts.at(-1)).toEqual({ auto_enabled: false, min_pages: 4, use_llm: false });
    await expect(llm).not.toBeChecked();
  });

  test('Switch reverts when saving fails', async ({ page }) => {
    await setupSettingsMocks(page);
    await page.route('**/api/system/split-settings', (route) =>
      route.request().method() === 'PUT'
        ? route.fulfill({ status: 500, json: { detail: 'Interner Fehler' } })
        : route.fallback()
    );
    await page.goto('/einstellungen');

    const toggle = page.getByTestId('split-settings').getByTestId('split-auto-enabled');
    await expect(toggle).toBeChecked();
    await toggle.click();
    await expect(page.getByText('Interner Fehler')).toBeVisible();
    await expect(toggle).toBeChecked();
  });
});

test.describe('Split editor proposal details', () => {
  test('Blank pages are labelled and uncertain spots are announced', async ({ page }) => {
    const pages = makeSplitPages(6, [3, 4]).map((p) =>
      p.n === 3 ? { ...p, blank_kind: 'back' as const } : p.n === 4 ? { ...p, blank_kind: 'separator' as const } : p
    );
    await setupSplitEditorMocks(page, makeSplitBatch({
      pages,
      boundaries: [
        { after_page: 1, decision: 'uncertain', score: -0.5, p: 0.38, reasons: ['Anschrift auf Seite 2'], llm: false },
        { after_page: 4, decision: 'cut', score: 5.5, p: 0.996, reasons: ['Leerseite als Trennblatt?'], llm: false },
      ],
    }));
    await page.goto(`/aufteilen/${MOCK_SPLIT_BATCH.id}`);

    await expect(page.getByTestId('split-page-3')).toContainText('Leere Rückseite');
    await expect(page.getByTestId('split-page-4')).toContainText('Trennblatt?');
    await expect(page.getByTestId('split-uncertain-hint')).toContainText('Eine Stelle ist unsicher');
    // Begründungen auch ohne Tooltip (Touch-Geräte) sichtbar
    await expect(page.getByTestId('split-uncertain-list')).toHaveText('Nach Seite 1: Anschrift auf Seite 2');
    await expect(page.getByTestId('split-marker-1')).toHaveAttribute('title', 'Unsicher: Schnitt nach Seite 1? – Anschrift auf Seite 2');

    await page.getByTestId('split-marker-1').click();
    await expect(page.getByTestId('split-marker-1')).toHaveAttribute('data-state', 'cut');
    await expect(page.getByTestId('split-uncertain-hint')).toHaveCount(0);
    await expect(page.getByTestId('split-uncertain-list')).toHaveCount(0);
  });
});
