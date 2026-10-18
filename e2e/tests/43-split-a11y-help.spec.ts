import { test, expect, Page } from '@playwright/test';
import {
  setupDashboardMocks,
  setupSplitEditorMocks,
  makeSplitBatch,
  MOCK_SPLIT_BATCH,
} from '../helpers/mock.helpers';

// Aufteilen von Stapel-Scans (Spezifikation 12, Etappe 4): Barrierefreiheit des
// Editors (Ansagen, Tastatur-Hilfe, lesbare Bedienelemente) und Hilfe-Eintrag.
// API vollstaendig gemockt.

const EDITOR_URL = `/aufteilen/${MOCK_SPLIT_BATCH.id}`;
const marker = (page: Page, n: number) => page.getByTestId(`split-marker-${n}`);

test.describe('Split editor accessibility', () => {
  test('Announces the current split and explains the transition markers', async ({ page }) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);

    const summary = page.getByTestId('split-summary');
    await expect(summary).toHaveAttribute('aria-live', 'polite');
    await expect(summary).toHaveText('Aufteilung in 1 Dokument: Teil 1 · Seiten 1–6');
    await expect(marker(page, 2)).toHaveAccessibleDescription(/Pfeiltasten .* Leertaste/);

    await marker(page, 2).click();
    await expect(summary).toHaveText('Aufteilung in 2 Dokumente: Teil 1 · Seiten 1–2, Teil 2 · Seiten 3–6');
  });

  test('An omitted page keeps its controls readable and named', async ({ page }) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);
    const tile = page.getByTestId('split-page-4');

    await tile.getByRole('button', { name: 'Seite weglassen (Seite 4)' }).click();
    const restore = tile.getByRole('button', { name: 'Wieder aufnehmen (Seite 4)' });
    await expect(restore).toBeVisible();
    // Die Beschriftung wechselt mit dem Zustand -> kein aria-pressed dazu,
    // sonst hiesse es "Wieder aufnehmen, gedrückt" (APG: Toggle-Label bleibt fix).
    await expect(restore).not.toHaveAttribute('aria-pressed');
    // Nur das Vorschaubild wird ausgegraut, nicht die Bedienelemente
    const effectiveOpacity = (el: Element) => {
      let opacity = 1;
      for (let node: Element | null = el; node && node.tagName !== 'LI'; node = node.parentElement) {
        opacity *= Number(getComputedStyle(node).opacity);
      }
      return opacity;
    };
    expect(await restore.evaluate(effectiveOpacity)).toBe(1);
    await expect(tile.getByRole('button', { name: 'Seite 4 groß anzeigen' })).toBeVisible();
    expect(await tile.getByRole('button', { name: 'Seite 4 groß anzeigen' }).evaluate(effectiveOpacity)).toBe(1);
    await expect.poll(() => tile.getByRole('img', { name: 'Seite 4' }).evaluate(effectiveOpacity)).toBeLessThan(1);
  });

  test('Editor with long reasons and all hints fits the viewport (no horizontal scrolling)', async ({ page }) => {
    await setupSplitEditorMocks(page, makeSplitBatch({
      duplex: true,
      duplex_conf: 0.95,
      partial_from: 5,
      original_filename: 'sehr_langer_dateiname_vom_einzugsscanner_ohne_leerzeichen_000427.pdf',
      boundaries: [
        { after_page: 2, decision: 'cut', p: 0.99, reasons: ['Seitenzähler beginnt neu: Seite 1'], llm: false },
        {
          after_page: 4, decision: 'uncertain', p: 0.5, llm: true,
          reasons: ['Neues Dokument innerhalb von Seite 2/4? – bitte prüfen', 'Anschrift und Anrede am Seitenanfang'],
        },
      ],
    }));
    await page.goto(EDITOR_URL);
    await expect(page.getByTestId('split-uncertain-list')).toBeVisible();
    await expect(page.getByTestId('split-partial')).toBeVisible();

    const overflow = await page.evaluate(() => {
      const main = document.querySelector('main');
      return {
        page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        main: main ? main.scrollWidth - main.clientWidth : 0,
      };
    });
    expect(overflow).toEqual({ page: 0, main: 0 });
  });

  test('"No cut" transitions stay findable on touch devices (no hover there)', async ({ page }, testInfo) => {
    await setupSplitEditorMocks(page);
    await page.goto(EDITOR_URL);
    await expect(marker(page, 3)).toHaveAttribute('data-state', 'none');

    const { coarse, background } = await marker(page, 3).evaluate((el) => ({
      coarse: window.matchMedia('(pointer: coarse)').matches,
      background: getComputedStyle(el.querySelector('span') as Element).backgroundColor,
    }));
    // Sonst prueft der Test auf dem Mobile-Projekt nur den Maus-Fall
    expect(coarse).toBe(testInfo.project.name === 'mobile');
    const transparent = background === 'transparent' || background === 'rgba(0, 0, 0, 0)';
    // Touch (Pixel 5): feine Linie sichtbar; Maus: erst beim Hovern
    expect(transparent).toBe(!coarse);
  });

  test('Detection progress is a live status message', async ({ page }) => {
    const detecting = makeSplitBatch({ status: 'DETECTING', page_count: 44, pages_done: 16, pages: [] });
    await setupSplitEditorMocks(page, detecting);
    await page.goto(EDITOR_URL);
    await expect(page.getByTestId('split-detecting').getByRole('status')).toHaveText('Seite 17 von 44 wird erkannt …');
  });
});

test.describe('Help', () => {
  test('FAQ explains what happens with multi-document scans', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.goto('/hilfe');
    await page.getByText('Mein Scanner legt mehrere Dokumente in eine PDF – was passiert?').click();
    const answer = page.getByText(/Stapel zum Aufteilen/);
    await expect(answer).toBeVisible();
    await expect(answer).toContainText('Ohne deine Bestätigung wird nichts aufgeteilt');
    await expect(answer).toContainText('„Dokument aufteilen“');
  });
});
