import { test, expect, Page } from '@playwright/test';
import { TEST_PIN } from '../helpers/credentials';
import { setupDashboardMocks, setupPinAuthMocks, showOnboardingTour } from '../helpers/mock.helpers';

const WELCOME = 'Willkommen bei Zettelwirtschaft';
const STORAGE_KEY = 'zw_onboarding_v1_done';

const tour = (page: Page) => page.getByRole('dialog').filter({ has: page.locator('#onb-title') });
const tourStoredAsDone = (page: Page) => page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY);

test.describe('Help & Onboarding', () => {
  test.beforeEach(async ({ page }) => {
    await setupDashboardMocks(page);
  });

  test('Help page shows getting-started, features and FAQ', async ({ page }) => {
    await page.goto('/hilfe');
    await expect(page.getByRole('heading', { name: 'Hilfe & Erste Schritte' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'In drei Schritten' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Funktionen im Überblick' })).toBeVisible();
    await expect(page.getByText('Wie füge ich ein Dokument hinzu?')).toBeVisible();
  });

  test('Help is reachable from the header', async ({ page }) => {
    await page.goto('/');
    await page.locator('header').getByRole('link', { name: 'Hilfe' }).click();
    await expect(page).toHaveURL('/hilfe');
  });

  test('Help feature card links into the app', async ({ page }) => {
    await page.goto('/hilfe');
    await page.locator('main').getByRole('link', { name: /Upload/ }).first().click();
    await expect(page).toHaveURL(/\/upload/);
  });

  test('Help does not promise restoring deleted documents', async ({ page }) => {
    await page.goto('/hilfe');
    await expect(page.locator('main')).not.toContainText(/wiederhergestellt|Papierkorb/);
  });

  test('Onboarding does not show once seen', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Gesamt Dokumente')).toBeVisible();
    await expect(tour(page)).toHaveCount(0);
  });

  test('Onboarding shows on first run, can be navigated and dismissed for good', async ({ page }) => {
    await showOnboardingTour(page);
    await page.goto('/');
    await expect(tour(page)).toContainText(WELCOME);
    await expect(tour(page)).toContainText('Schritt 1 von 5');
    // App dahinter ist waehrend der Tour nicht bedienbar
    await expect(page.locator('#app')).toHaveAttribute('inert', '');

    await page.getByRole('button', { name: 'Weiter' }).click();
    await expect(page.getByRole('heading', { name: 'Dokumente erfassen' })).toBeVisible();
    await page.getByRole('button', { name: 'Zurück' }).click();
    await expect(page.getByRole('heading', { name: WELCOME })).toBeVisible();
    await page.getByRole('button', { name: 'Zu Schritt 4' }).click();
    await expect(page.getByRole('heading', { name: 'Finden & nutzen' })).toBeVisible();

    await page.getByRole('button', { name: 'Tour schließen' }).click();
    await expect(tour(page)).toHaveCount(0);
    await expect(page.locator('#app')).not.toHaveAttribute('inert', '');
    expect(await tourStoredAsDone(page)).toBe('1');

    await page.reload();
    await expect(page.getByText('Gesamt Dokumente')).toBeVisible();
    await expect(tour(page)).toHaveCount(0);
  });

  test('Keyboard: arrows navigate, Escape closes and is remembered', async ({ page }) => {
    await showOnboardingTour(page);
    await page.goto('/');
    await expect(tour(page)).toContainText(WELCOME);

    await page.keyboard.press('ArrowRight');
    await expect(tour(page)).toContainText('Schritt 2 von 5');
    await page.keyboard.press('ArrowLeft');
    await expect(tour(page)).toContainText('Schritt 1 von 5');

    await page.keyboard.press('Escape');
    await expect(tour(page)).toHaveCount(0);
    await page.reload();
    await expect(page.getByText('Gesamt Dokumente')).toBeVisible();
    await expect(tour(page)).toHaveCount(0);
  });

  test('Clicking the backdrop does not dismiss the tour', async ({ page }) => {
    await showOnboardingTour(page);
    await page.goto('/');
    await expect(tour(page)).toBeVisible();

    await page.mouse.click(5, 5);
    await expect(tour(page)).toBeVisible();
    expect(await tourStoredAsDone(page)).toBeNull();
  });

  test('"Überspringen" on the first step marks the tour as seen', async ({ page }) => {
    await showOnboardingTour(page);
    await page.goto('/');
    await page.getByRole('button', { name: 'Überspringen' }).click();
    await expect(tour(page)).toHaveCount(0);
    expect(await tourStoredAsDone(page)).toBe('1');
  });

  test('Restart tour from Help reopens the wizard without resetting completion', async ({ page }) => {
    await page.goto('/hilfe');
    await page.getByRole('button', { name: 'Tour erneut starten' }).click();
    await expect(tour(page)).toContainText(WELCOME);
    expect(await tourStoredAsDone(page)).toBe('1');

    // Reload mitten in der erneut gestarteten Tour: sie startet nicht von selbst neu
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Hilfe & Erste Schritte' })).toBeVisible();
    await expect(tour(page)).toHaveCount(0);
  });

  test('Onboarding last-step CTA navigates to upload', async ({ page }) => {
    await showOnboardingTour(page);
    await page.goto('/');
    await expect(tour(page)).toContainText(WELCOME);
    for (let i = 0; i < 4; i++) {
      await page.getByRole('button', { name: 'Weiter' }).click();
    }
    await expect(page.getByRole('button', { name: 'Später' })).toBeVisible();
    await page.getByRole('button', { name: 'Erstes Dokument hochladen' }).click();
    await expect(page).toHaveURL(/\/upload/);
    await expect(tour(page)).toHaveCount(0);
  });
});

test.describe('Onboarding with PIN protection', () => {
  test('Tour waits until after the PIN login', async ({ page }) => {
    await setupPinAuthMocks(page, { dataMocks: setupDashboardMocks });
    await showOnboardingTour(page);

    await page.goto('/');
    await expect(page).toHaveURL(/\/pin/);
    await expect(tour(page)).toHaveCount(0);

    await page.locator('#pin').fill(TEST_PIN);
    await page.getByRole('button', { name: /anmelden/i }).click();
    await expect(tour(page)).toContainText(WELCOME);
  });
});
