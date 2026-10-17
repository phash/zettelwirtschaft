import { test, expect, Page } from '@playwright/test';
import {
  setupTaxMocks,
  MOCK_TAX_SUMMARY,
  MOCK_TAX_SUMMARY_EMPTY,
  MOCK_TAX_YEARS,
  MOCK_TAX_VALIDATION,
  MOCK_TAX_VALIDATION_EMPTY,
} from '../helpers/mock.helpers';

/** StatCard ueber ihre exakte Beschriftung. */
const statCard = (page: Page, label: string) =>
  page.locator('div.grid > .card').filter({ has: page.getByText(label, { exact: true }) });

test.describe('Tax Package', () => {
  test.beforeEach(async ({ page }) => {
    await setupTaxMocks(page);
  });

  test('Shows tax year selector with all years', async ({ page }) => {
    await page.goto('/steuer');
    const yearSelect = page.getByLabel('Steuerjahr');
    await expect(yearSelect).toHaveValue('2026');
    for (const year of MOCK_TAX_YEARS.years) {
      await expect(yearSelect.locator('option', { hasText: String(year) })).toHaveCount(1);
    }
  });

  test('Shows stats cards with correct values', async ({ page }) => {
    await page.goto('/steuer');
    await expect(statCard(page, 'Belege')).toContainText(String(MOCK_TAX_SUMMARY.total_documents));
    await expect(statCard(page, 'Gesamtbetrag')).toContainText('4.580,50');
    await expect(statCard(page, 'Kategorien')).toContainText(String(MOCK_TAX_SUMMARY.categories.length));
  });

  test('Category cards show label, count and amount', async ({ page }) => {
    await page.goto('/steuer');
    const first = MOCK_TAX_SUMMARY.categories[0];
    const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: first.label }) });
    await expect(card).toContainText(`${first.document_count} Belege`);
    await expect(card).toContainText('2.100,00');
    for (const cat of MOCK_TAX_SUMMARY.categories) {
      await expect(page.getByRole('heading', { name: cat.label })).toBeVisible();
    }
  });

  test('Switch year loads summary and validation for that year', async ({ page }) => {
    await page.goto('/steuer');
    await expect(statCard(page, 'Belege')).toBeVisible();

    const [summaryReq, validateReq] = await Promise.all([
      page.waitForRequest((r) => new URL(r.url()).pathname === '/api/tax/summary/2025'),
      page.waitForRequest((r) => new URL(r.url()).pathname === '/api/tax/validate/2025'),
      page.getByLabel('Steuerjahr').selectOption('2025'),
    ]);
    expect(summaryReq).toBeTruthy();
    expect(validateReq).toBeTruthy();
  });

  test('Scope filter is sent with summary requests', async ({ page }) => {
    await page.goto('/steuer');
    const scopeSelect = page.getByLabel('Ablagebereich');
    await expect(scopeSelect.locator('option', { hasText: 'Alle Bereiche' })).toHaveCount(1);

    const req = await Promise.all([
      page.waitForRequest((r) => new URL(r.url()).pathname === '/api/tax/summary/2026'),
      scopeSelect.selectOption({ label: 'Praxis' }),
    ]).then(([r]) => r);
    expect(new URL(req.url()).searchParams.get('filing_scope_id')).toBe('scope-praxis');
  });

  test('Export triggers a download of the ZIP', async ({ page }) => {
    await page.goto('/steuer');
    const exportBtn = page.getByRole('button', { name: 'ZIP exportieren' });
    await expect(exportBtn).toBeEnabled();

    const [req, download] = await Promise.all([
      page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/tax/export'),
      page.waitForEvent('download'),
      exportBtn.click(),
    ]);
    expect(req.postDataJSON()).toEqual({ year: 2026 });
    expect(download.suggestedFilename()).toBe('Steuerpaket_2026.zip');
    await expect(page.getByText('Steuerpaket 2026 heruntergeladen.')).toBeVisible();
  });

  test('Export error is reported', async ({ page }) => {
    await page.route('**/api/tax/export', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/steuer');
    await page.getByRole('button', { name: 'ZIP exportieren' }).click();
    await expect(page.getByText('Export fehlgeschlagen.')).toBeVisible();
  });

  test('Validation warnings are collapsed and expandable', async ({ page }) => {
    await page.goto('/steuer');
    const toggle = page.getByRole('button', { name: `${MOCK_TAX_VALIDATION.warnings.length} Hinweise vor dem Export` });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.getByText(MOCK_TAX_VALIDATION.warnings[0])).toHaveCount(0);

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    for (const warning of MOCK_TAX_VALIDATION.warnings) {
      await expect(page.getByText(warning)).toBeVisible();
    }
  });

  test('Non-EUR documents: EUR total only plus a list of the excluded documents', async ({ page }) => {
    await page.route('**/api/tax/summary/*', (route) =>
      route.fulfill({
        json: {
          ...MOCK_TAX_SUMMARY,
          non_eur_documents: [
            { id: 'doc-usd', title: 'Hotel New York', document_date: '2026-05-02', amount: 420.0, currency: 'USD' },
          ],
        },
      })
    );
    await page.goto('/steuer');

    await expect(statCard(page, 'Gesamtbetrag (nur EUR)')).toContainText('4.580,50');
    const warning = page.getByTestId('tax-non-eur-warning');
    await expect(warning).toContainText('1 Beleg in Fremdwährung ist nicht im Gesamtbetrag enthalten');
    await expect(warning.getByRole('link', { name: 'Hotel New York' })).toHaveAttribute('href', '/dokumente/doc-usd');
    await expect(warning).toContainText('420,00');
    await expect(warning).toContainText('2.5.2026');
  });

  test('Empty state when no tax documents', async ({ page }) => {
    await page.route('**/api/tax/summary/*', (route) => route.fulfill({ json: MOCK_TAX_SUMMARY_EMPTY }));
    await page.route('**/api/tax/validate/*', (route) => route.fulfill({ json: MOCK_TAX_VALIDATION_EMPTY }));

    await page.goto('/steuer');
    await expect(page.getByText('Keine steuerrelevanten Belege für 2026.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'ZIP exportieren' })).toBeDisabled();
  });

  test('Load error shows a message', async ({ page }) => {
    await page.route('**/api/tax/summary/*', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/steuer');
    await expect(page.getByText('Steuerdaten konnten nicht geladen werden.')).toBeVisible();
  });
});
