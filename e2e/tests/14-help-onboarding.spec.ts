import { test, expect } from '@playwright/test';
import { setupBaseMocks } from '../helpers/mock.helpers';

test.describe('Help & Onboarding', () => {
  test.beforeEach(async ({ page }) => {
    await setupBaseMocks(page);
  });

  test('Help page shows getting-started, features and FAQ', async ({ page }) => {
    await page.goto('/hilfe');
    await expect(page.getByRole('heading', { name: 'Hilfe & Erste Schritte' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'In drei Schritten' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Funktionen im Überblick' })).toBeVisible();
    await expect(page.getByText('Wie füge ich ein Dokument hinzu?')).toBeVisible();
  });

  test('Help feature card links into the app', async ({ page }) => {
    await page.goto('/hilfe');
    await page.locator('main').getByRole('link', { name: /Upload/ }).first().click();
    await expect(page).toHaveURL(/\/upload/);
  });

  test('Onboarding does not show once seen', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Willkommen bei Zettelwirtschaft' })).toHaveCount(0);
  });

  test('Onboarding shows on first run and can be navigated and dismissed', async ({ page }) => {
    // Tour als ungesehen markieren (überschreibt setupBaseMocks-Init-Script)
    await page.addInitScript(() => {
      try { localStorage.removeItem('zw_onboarding_v1_done'); } catch { /* ignore */ }
    });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Willkommen bei Zettelwirtschaft' })).toBeVisible();
    await page.getByRole('button', { name: 'Weiter' }).click();
    await expect(page.getByRole('heading', { name: 'Dokumente erfassen' })).toBeVisible();
    await page.getByRole('button', { name: 'Tour schließen' }).click();
    await expect(page.getByRole('heading', { name: 'Dokumente erfassen' })).toHaveCount(0);
  });

  test('Restart tour from Help reopens the wizard', async ({ page }) => {
    await page.goto('/hilfe');
    await page.getByRole('button', { name: 'Tour erneut starten' }).click();
    await expect(page.getByRole('heading', { name: 'Willkommen bei Zettelwirtschaft' })).toBeVisible();
  });

  test('Onboarding last-step CTA navigates to upload', async ({ page }) => {
    await page.addInitScript(() => {
      try { localStorage.removeItem('zw_onboarding_v1_done'); } catch { /* ignore */ }
    });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Willkommen bei Zettelwirtschaft' })).toBeVisible();
    for (let i = 0; i < 4; i++) {
      await page.getByRole('button', { name: 'Weiter' }).click();
    }
    await page.getByRole('button', { name: 'Erstes Dokument hochladen' }).click();
    await expect(page).toHaveURL(/\/upload/);
  });
});
