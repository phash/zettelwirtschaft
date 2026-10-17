import { test, expect } from '@playwright/test';
import { setupDashboardMocks } from '../helpers/mock.helpers';
import * as fs from 'fs';
import * as path from 'path';

// Sidebar zeigt die zur Build-Zeit eingebettete Version (__APP_VERSION__ aus der
// Repo-Datei VERSION) — NICHT die app_version aus /api/system/health. Daher gegen
// dieselbe Single Source of Truth pruefen statt gegen einen Mock-Wert.
const APP_VERSION = fs.readFileSync(path.resolve(__dirname, '../../VERSION'), 'utf-8').trim();

test.describe('Smoke Tests', () => {
  test('App loads and shows dashboard', async ({ page, isMobile }) => {
    await setupDashboardMocks(page);
    await page.goto('/');
    await expect(page).toHaveTitle(/Zettelwirtschaft/);
    if (!isMobile) {
      await expect(page.locator('text=Dashboard').first()).toBeVisible();
    }
  });

  test('Health endpoint is mocked and app loads', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.goto('/');
    // If app loads successfully, health check passed
    await expect(page.locator('text=Gesamt Dokumente').first()).toBeVisible();
  });

  test('Navigation sidebar is visible on desktop', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.goto('/');
    const sidebar = page.locator('nav, aside').first();
    await expect(sidebar).toBeVisible();
    await expect(page.locator('text=Dokumente').first()).toBeVisible();
    await expect(page.locator('text=Upload').first()).toBeVisible();
    await expect(page.locator('text=Suche').first()).toBeVisible();
  });

  test('Sidebar navigation links work', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.goto('/');

    // Navigate to Dokumente
    await page.locator('nav a, aside a').filter({ hasText: 'Dokumente' }).first().click();
    await expect(page).toHaveURL(/\/dokumente/);
  });

  test('Unknown routes redirect to dashboard', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.goto('/nonexistent-route');
    await expect(page).toHaveURL('/');
  });

  test('Version number is displayed', async ({ page, isMobile }) => {
    test.skip(!!isMobile, 'Version only shown in sidebar on desktop');
    await setupDashboardMocks(page);
    await page.goto('/');
    await expect(page.locator(`text=v${APP_VERSION}`).first()).toBeVisible();
  });
});
