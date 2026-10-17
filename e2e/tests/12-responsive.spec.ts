import { test, expect, Page } from '@playwright/test';
import {
  setupDashboardMocks,
  setupDocumentsMocks,
  setupSearchMocks,
  setupTaxMocks,
  setupWarrantyMocks,
  setupSettingsMocks,
  setupReviewMocks,
  setupChatMocks,
  MOCK_STATS,
} from '../helpers/mock.helpers';

// Mehr-Menue und Header-Logo auf dem Smartphone: 20-app-shell.spec.ts;
// Dokument-Karten statt Tabelle: 15-documents-upload-dashboard.spec.ts.

const SIDEBAR_ITEMS = ['Dashboard', 'Dokumente', 'Upload', 'Scan', 'Zu prüfen', 'Suche', 'KI-Assistent', 'Steuer', 'Garantien', 'System', 'Hilfe'];
const BOTTOM_ITEMS = ['Scan', 'Dokumente', 'Suche', 'Assistent'];

async function setupAllPageMocks(page: Page) {
  await setupDashboardMocks(page);
  await setupDocumentsMocks(page);
  await setupSearchMocks(page);
  await setupTaxMocks(page);
  await setupWarrantyMocks(page);
  await setupSettingsMocks(page);
  await setupReviewMocks(page);
  await setupChatMocks(page);
}

const sidebar = (page: Page) => page.locator('aside').first();
// Name beginnt mit dem Label; "Zu prüfen" traegt ggf. ein Badge ("2 offen").
const sidebarLink = (page: Page, label: string) =>
  page.getByRole('navigation', { name: 'Hauptnavigation' }).getByRole('link', { name: new RegExp(`^${label}(\\s|$)`) });
const bottomNav = (page: Page) => page.getByRole('navigation', { name: 'Navigation' }).and(page.locator('.fixed.bottom-0'));

test.describe('Responsive Design & Navigation', () => {
  test.describe('Desktop (>1024px)', () => {
    test.use({ viewport: { width: 1280, height: 720 } });

    test('Sidebar shows every navigation item', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      await expect(sidebar(page)).toBeVisible();
      for (const item of SIDEBAR_ITEMS) {
        await expect(sidebarLink(page, item)).toBeVisible();
      }
    });

    test('Active navigation link is highlighted', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const dashboardLink = sidebarLink(page, 'Dashboard');
      const bgColor = await dashboardLink.evaluate((el) => getComputedStyle(el).backgroundColor);
      expect(bgColor).not.toBe('rgba(0, 0, 0, 0)');
      const docsBg = await sidebarLink(page, 'Dokumente')
        .evaluate((el) => getComputedStyle(el).backgroundColor);
      expect(docsBg).not.toBe(bgColor);
    });

    test('Bottom navigation is hidden on desktop', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');
      await expect(bottomNav(page)).toBeHidden();
    });

    test('Version number shown in sidebar footer', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');
      await expect(sidebar(page).getByText(/^v\d+\.\d+/)).toBeVisible();
    });
  });

  test.describe('Mobile (375px)', () => {
    test.use({ viewport: { width: 375, height: 667 } });

    test('Sidebar is hidden, bottom navigation is visible', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');
      await expect(sidebar(page)).toBeHidden();
      await expect(bottomNav(page)).toBeVisible();
    });

    test('Bottom nav has the four main pages plus "Mehr"', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const nav = bottomNav(page);
      await expect(nav.getByRole('link')).toHaveCount(BOTTOM_ITEMS.length);
      for (const item of BOTTOM_ITEMS) {
        await expect(nav.getByRole('link', { name: item })).toBeVisible();
      }
      await expect(nav.getByRole('button', { name: 'Mehr' })).toBeVisible();
    });

    test('Scan button is prominent in bottom nav', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const scanLink = bottomNav(page).getByRole('link', { name: 'Scan' });
      await expect(scanLink.locator('.rounded-full')).toBeVisible();
      const scanBox = await scanLink.locator('.rounded-full').boundingBox();
      const docsBox = await bottomNav(page).getByRole('link', { name: 'Dokumente' }).boundingBox();
      expect(scanBox!.height).toBeGreaterThan(docsBox!.height / 2);
    });

    test('Bottom nav navigates correctly', async ({ page }) => {
      await setupDashboardMocks(page);
      await setupDocumentsMocks(page);
      await page.goto('/');

      await bottomNav(page).getByRole('link', { name: 'Dokumente' }).click();
      await expect(page).toHaveURL(/\/dokumente/);
    });

    test('Dashboard cards are stacked in one column', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const cards = page.getByTestId('stat-cards').locator('> div');
      await expect(cards.first()).toContainText(String(MOCK_STATS.total_documents));
      const a = await cards.nth(0).boundingBox();
      const b = await cards.nth(1).boundingBox();
      expect(b!.y).toBeGreaterThan(a!.y);
      expect(Math.round(b!.x)).toBe(Math.round(a!.x));
    });

    for (const path of ['/', '/dokumente', '/suche', '/pruefen', '/assistent', '/steuer', '/garantien', '/einstellungen', '/hilfe']) {
      test(`No horizontal scrolling on ${path}`, async ({ page }) => {
        await setupAllPageMocks(page);
        await page.goto(path);
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();

        const overflow = await page.evaluate(() => {
          const main = document.querySelector('main');
          return {
            page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            main: main ? main.scrollWidth - main.clientWidth : 0,
          };
        });
        expect(overflow).toEqual({ page: 0, main: 0 });
      });
    }
  });

  test.describe('Tablet (768px)', () => {
    test.use({ viewport: { width: 768, height: 1024 } });

    test('Layout adapts to tablet viewport', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // Unterhalb von lg (1024px) zeigt der Header den App-Namen, die Sidebar ist weg
      await expect(page.locator('header').getByText('Zettelwirtschaft')).toBeVisible();
      await expect(sidebar(page)).toBeHidden();
      await expect(bottomNav(page)).toBeVisible();
      await expect(page.getByText('Gesamt Dokumente')).toBeVisible();
    });
  });

  test.describe('Navigation Flow', () => {
    test.use({ viewport: { width: 1280, height: 720 } });

    test('Navigate through all main pages via sidebar', async ({ page }) => {
      await setupAllPageMocks(page);
      await page.goto('/');

      for (const [label, url] of [
        ['Dokumente', '/dokumente'],
        ['Suche', '/suche'],
        ['Steuer', '/steuer'],
        ['Garantien', '/garantien'],
        ['System', '/einstellungen'],
        ['Hilfe', '/hilfe'],
        ['Dashboard', '/'],
      ] as const) {
        await sidebarLink(page, label).click();
        await expect(page).toHaveURL(url);
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
      }
    });
  });
});
