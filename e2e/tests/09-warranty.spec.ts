import { test, expect, Page } from '@playwright/test';
import {
  setupWarrantyMocks,
  MOCK_WARRANTIES,
  MOCK_WARRANTY_STATS,
  MOCK_DOCUMENT_WITH_WARRANTY,
} from '../helpers/mock.helpers';

const statCard = (page: Page, label: string) =>
  page.locator('div.grid > .card').filter({ has: page.getByText(label, { exact: true }) });
const warrantyItem = (page: Page, productName: string) =>
  page.getByRole('link', { name: `Garantie ${productName} öffnen` });

test.describe('Warranty Tracker', () => {
  test.beforeEach(async ({ page }) => {
    await setupWarrantyMocks(page);
  });

  test('Shows warranty stats cards with values', async ({ page }) => {
    await page.goto('/garantien');
    await expect(statCard(page, 'Gesamt')).toContainText(String(MOCK_WARRANTY_STATS.total));
    await expect(statCard(page, 'Aktiv')).toContainText(String(MOCK_WARRANTY_STATS.active));
    await expect(statCard(page, 'Bald ablaufend')).toContainText(String(MOCK_WARRANTY_STATS.expiring_soon));
    await expect(statCard(page, 'Abgelaufen')).toContainText(String(MOCK_WARRANTY_STATS.expired));
  });

  test('Shows warranty items with retailer and dates', async ({ page }) => {
    await page.goto('/garantien');
    for (const w of MOCK_WARRANTIES) {
      const item = warrantyItem(page, w.product_name);
      await expect(item).toBeVisible();
      await expect(item).toContainText(w.retailer);
    }
    await expect(warrantyItem(page, 'Dell XPS 15')).toContainText('Kauf: 10.1.2026');
    await expect(warrantyItem(page, 'Dell XPS 15')).toContainText('Ende: 10.1.2028');
  });

  test('Status badges reflect remaining time', async ({ page }) => {
    await page.goto('/garantien');
    await expect(warrantyItem(page, 'Dell XPS 15').locator('.badge')).toHaveText('Aktiv');
    await expect(warrantyItem(page, 'Waschmaschine Bosch').locator('.badge')).toHaveText('60 Tage');
    await expect(warrantyItem(page, 'Staubsauger Dyson').locator('.badge')).toHaveText('Abgelaufen');
  });

  test('Progress bars show elapsed share of the warranty period', async ({ page }) => {
    // Genau die Haelfte der Dell-Garantie (10.01.2026 -> 10.01.2028)
    await page.clock.setFixedTime(new Date('2027-01-10T00:00:00'));
    await page.goto('/garantien');

    const bar = (name: string) => warrantyItem(page, name).locator('[style*="width"]');
    const width = async (name: string) => parseFloat((await bar(name).getAttribute('style'))!.match(/width:\s*([\d.]+)%/)![1]);
    await expect(bar('Dell XPS 15')).toBeVisible();
    expect(Math.round(await width('Dell XPS 15'))).toBe(50);
    expect(await width('Staubsauger Dyson')).toBe(100);
  });

  test('Warranty item navigates to its document', async ({ page }) => {
    await page.route('**/api/documents/*', (route) => route.fulfill({ json: MOCK_DOCUMENT_WITH_WARRANTY }));
    await page.route('**/api/documents/*/file', (route) =>
      route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF') })
    );
    await page.route('**/api/tags', (route) => route.fulfill({ json: [] }));
    await page.goto('/garantien');

    await warrantyItem(page, MOCK_WARRANTIES[0].product_name).click();
    await expect(page).toHaveURL(`/dokumente/${MOCK_WARRANTIES[0].document_id}`);
  });

  for (const [status, label] of [['active', 'Aktiv'], ['expiring', 'Bald ablaufend'], ['expired', 'Abgelaufen']] as const) {
    test(`Status filter "${label}" requests status=${status}`, async ({ page }) => {
      await page.goto('/garantien');
      await expect(warrantyItem(page, MOCK_WARRANTIES[0].product_name)).toBeVisible();

      const [req] = await Promise.all([
        page.waitForRequest((r) => new URL(r.url()).pathname === '/api/warranties'),
        page.getByLabel('Status-Filter').selectOption(status),
      ]);
      expect(new URL(req.url()).searchParams.get('status')).toBe(status);
    });
  }

  test('Filter "Alle" sends no status parameter', async ({ page }) => {
    await page.goto('/garantien');
    await page.getByLabel('Status-Filter').selectOption('expired');
    const [req] = await Promise.all([
      page.waitForRequest((r) => new URL(r.url()).pathname === '/api/warranties'),
      page.getByLabel('Status-Filter').selectOption('all'),
    ]);
    expect(new URL(req.url()).searchParams.has('status')).toBe(false);
  });

  test('Empty state when no warranties', async ({ page }) => {
    await page.route('**/api/warranties?*', (route) => route.fulfill({ json: [] }));
    await page.route('**/api/warranties', (route) => route.fulfill({ json: [] }));
    await page.route('**/api/warranties/stats', (route) =>
      route.fulfill({ json: { total: 0, active: 0, expiring_soon: 0, expired: 0 } })
    );

    await page.goto('/garantien');
    await expect(page.getByText('Keine Garantien vorhanden.')).toBeVisible();
  });

  test('Load error shows a message', async ({ page }) => {
    await page.route('**/api/warranties/stats', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/garantien');
    await expect(page.getByText('Garantiedaten konnten nicht geladen werden.')).toBeVisible();
  });
});
