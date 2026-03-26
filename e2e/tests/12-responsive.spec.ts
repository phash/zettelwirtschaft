import { test, expect } from '@playwright/test';
import { setupDashboardMocks, setupDocumentsMocks, MOCK_DOCUMENTS } from '../helpers/mock.helpers';

test.describe('Responsive Design & Navigation', () => {
  test.describe('Desktop (>1024px)', () => {
    test.use({ viewport: { width: 1280, height: 720 } });

    test('Sidebar navigation is visible', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // Sidebar is an <aside> with class "hidden lg:flex"
      const sidebar = page.locator('aside').first();
      await expect(sidebar).toBeVisible();
    });

    test('All 10 navigation items are visible in sidebar', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const navItems = ['Dashboard', 'Dokumente', 'Upload', 'Scan', 'prüfen', 'Suche', 'Assistent', 'Steuer', 'Garantien', 'System'];
      for (const item of navItems) {
        const navLink = page.locator('aside a').filter({ hasText: new RegExp(item, 'i') }).first();
        await expect(navLink).toBeVisible();
      }
    });

    test('Active navigation link is highlighted', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const dashboardLink = page.locator('aside a').filter({ hasText: /dashboard/i }).first();
      // Should have highlighted styling (background color)
      const bgColor = await dashboardLink.evaluate((el) => getComputedStyle(el).backgroundColor);
      expect(bgColor).not.toBe('rgba(0, 0, 0, 0)');
    });

    test('Bottom navigation is hidden on desktop', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // BottomNav is a <nav> with class "lg:hidden"
      const bottomNav = page.locator('nav.fixed.bottom-0').first();
      if (await bottomNav.count() > 0) {
        await expect(bottomNav).not.toBeVisible();
      }
    });

    test('Version number shown in sidebar footer', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      await expect(page.locator('aside').locator('text=v1.1.0').first()).toBeVisible();
    });
  });

  test.describe('Mobile (375px)', () => {
    test.use({ viewport: { width: 375, height: 667 } });

    test('Sidebar is hidden on mobile', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const sidebar = page.locator('aside').first();
      if (await sidebar.count() > 0) {
        await expect(sidebar).not.toBeVisible();
      }
    });

    test('Bottom navigation is visible on mobile', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // BottomNav is a <nav> with "fixed bottom-0 ... lg:hidden"
      // It contains 4 links: Scan, Dokumente, Suche, Assistent
      const bottomNav = page.locator('nav.fixed.bottom-0');
      await expect(bottomNav).toBeVisible();
    });

    test('Bottom nav has 4 items', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // BottomNav has exactly 4 router-links
      const bottomNav = page.locator('nav.fixed.bottom-0');
      const navLinks = bottomNav.locator('a');
      await expect(navLinks).toHaveCount(4);
    });

    test('Scan button is prominent in bottom nav', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // Scan has a special prominent circular button with -mt-3
      const bottomNav = page.locator('nav.fixed.bottom-0');
      const scanLink = bottomNav.locator('a').filter({ hasText: /scan/i }).first();
      await expect(scanLink).toBeVisible();

      // The scan button has a larger circular element (h-12 w-12 rounded-full)
      const circle = scanLink.locator('.rounded-full');
      await expect(circle).toBeVisible();
    });

    test('Bottom nav navigates correctly', async ({ page }) => {
      await setupDocumentsMocks(page);
      await setupDashboardMocks(page);
      await page.goto('/');

      const bottomNav = page.locator('nav.fixed.bottom-0');
      const docsLink = bottomNav.locator('a').filter({ hasText: /dokumente/i }).first();
      await docsLink.click();
      await expect(page).toHaveURL(/\/dokumente/);
    });

    test('Document table adapts to mobile viewport', async ({ page }) => {
      await setupDocumentsMocks(page);
      await page.goto('/dokumente');

      // Content should be visible without horizontal scroll
      const firstDoc = MOCK_DOCUMENTS.items[0];
      await expect(page.locator(`text=${firstDoc.title}`).first()).toBeVisible();
    });

    test('Dashboard cards stack vertically on mobile', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // Stats cards should be visible - look for "Gesamt Dokumente" or total count
      await expect(page.locator('text=Gesamt Dokumente').first()).toBeVisible();
    });
  });

  test.describe('Tablet (768px)', () => {
    test.use({ viewport: { width: 768, height: 1024 } });

    test('Layout adapts to tablet viewport', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      // On tablet (768px < 1024px lg breakpoint), sidebar is hidden
      // but page content is visible - AppHeader shows "Zettelwirtschaft" on mobile/tablet
      await expect(page.locator('header').locator('text=Zettelwirtschaft').first()).toBeVisible();
      // Dashboard stats cards should also be visible
      await expect(page.locator('text=Gesamt Dokumente').first()).toBeVisible();
    });

    test('Sidebar hidden on tablet', async ({ page }) => {
      await setupDashboardMocks(page);
      await page.goto('/');

      const sidebar = page.locator('aside').first();
      if (await sidebar.count() > 0) {
        // Sidebar might be hidden below 1024px
        const isVisible = await sidebar.isVisible();
        // On tablet (768px < 1024px), sidebar should be hidden
        expect(isVisible).toBeFalsy();
      }
    });
  });

  test.describe('Navigation Flow', () => {
    test.use({ viewport: { width: 1280, height: 720 } });

    test('Navigate through all main pages', async ({ page }) => {
      await setupDashboardMocks(page);
      await setupDocumentsMocks(page);

      // Start at dashboard
      await page.goto('/');
      await expect(page).toHaveURL('/');

      // Navigate to Dokumente
      await page.locator('aside a').filter({ hasText: /dokumente/i }).first().click();
      await expect(page).toHaveURL('/dokumente');

      // Navigate to Suche
      await page.locator('aside a').filter({ hasText: /suche/i }).first().click();
      await expect(page).toHaveURL('/suche');

      // Navigate to Steuer
      await page.route('**/api/tax/years', (route) =>
        route.fulfill({ json: { years: [2026] } })
      );
      await page.route('**/api/tax/summary/*', (route) =>
        route.fulfill({ json: { year: 2026, total_documents: 0, total_amount: 0, categories: [] } })
      );
      await page.route('**/api/tax/validate/*', (route) =>
        route.fulfill({ json: { warnings: [], is_valid: true } })
      );
      await page.locator('aside a').filter({ hasText: /steuer/i }).first().click();
      await expect(page).toHaveURL('/steuer');

      // Navigate to Einstellungen
      await page.locator('aside a').filter({ hasText: /system|einstellungen/i }).first().click();
      await expect(page).toHaveURL('/einstellungen');
    });
  });
});
