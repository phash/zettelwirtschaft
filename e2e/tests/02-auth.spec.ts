import { test, expect } from '@playwright/test';
import {
  setupPinAuthMocks,
  setupDashboardMocks,
  setupDocumentsMocks,
  setupSearchMocks,
  MOCK_STATS,
} from '../helpers/mock.helpers';

// PIN-Schutz. setupPinAuthMocks bildet die Backend-Middleware nach: ohne
// Sitzung antwortet jede geschuetzte /api/*-Route mit 401, nur /api/health und
// /api/auth/* sind offen. Weitere Faelle (PIN-Seite ohne App-Shell, Abmelden,
// Guard nach Ablauf) stehen in 20-app-shell.spec.ts.

test.describe('Authentication - PIN Login', () => {
  test('Redirects to PIN page when PIN is enabled', async ({ page }) => {
    await setupPinAuthMocks(page);
    await page.goto('/');
    await expect(page).toHaveURL(/\/pin/);
  });

  test('PIN page shows input and submit button', async ({ page }) => {
    await setupPinAuthMocks(page);
    await page.goto('/pin');

    const pinInput = page.locator('#pin');
    await expect(pinInput).toBeVisible();
    await expect(pinInput).toHaveAttribute('type', 'password');
    await expect(pinInput).toHaveAttribute('inputmode', 'numeric');

    const submitBtn = page.getByRole('button', { name: /anmelden/i });
    await expect(submitBtn).toBeVisible();
    await expect(submitBtn).toBeDisabled();
  });

  test('Submit button enables when PIN is entered', async ({ page }) => {
    await setupPinAuthMocks(page);
    await page.goto('/pin');

    await page.locator('#pin').fill('1234');
    await expect(page.getByRole('button', { name: /anmelden/i })).toBeEnabled();
  });

  test('Successful login redirects to dashboard with data', async ({ page }) => {
    const session = await setupPinAuthMocks(page, { dataMocks: setupDashboardMocks });

    await page.goto('/pin');
    await page.locator('#pin').fill('1234');
    await page.getByRole('button', { name: /anmelden/i }).click();

    await expect(page).toHaveURL('/');
    expect(session.loginAttempts).toEqual(['1234']);
    // Nach dem Login liefern die geschuetzten Endpunkte Daten (kein 401 mehr)
    await expect(page.getByText('Gesamt Dokumente')).toBeVisible();
    await expect(page.getByTestId('stat-cards')).toContainText(String(MOCK_STATS.total_documents));
  });

  test('Failed login shows error message and clears the input', async ({ page }) => {
    await setupPinAuthMocks(page);

    await page.goto('/pin');
    await page.locator('#pin').fill('0000');
    await page.getByRole('button', { name: /anmelden/i }).click();

    await expect(page.getByRole('alert')).toHaveText('Falscher PIN');
    await expect(page.locator('#pin')).toHaveValue('');
    await expect(page).toHaveURL(/\/pin/);
  });

  test('Lockout (429) after too many attempts shows the wait time', async ({ page }) => {
    await setupPinAuthMocks(page);
    let attempts = 0;
    await page.route('**/api/auth/login', (route) => {
      attempts += 1;
      if (attempts <= 5) {
        return route.fulfill({ status: 401, json: { success: false, detail: 'Falscher PIN' } });
      }
      return route.fulfill({ status: 429, json: { success: false, detail: 'Zu viele Fehlversuche. Bitte 30s warten.' } });
    });

    await page.goto('/pin');
    for (let i = 0; i < 5; i++) {
      await page.locator('#pin').fill('0000');
      await page.getByRole('button', { name: /anmelden/i }).click();
      await expect(page.getByRole('alert')).toHaveText('Falscher PIN');
    }
    await page.locator('#pin').fill('1234');
    await page.getByRole('button', { name: /anmelden/i }).click();

    await expect(page.getByRole('alert')).toHaveText('Zu viele Fehlversuche. Bitte 30s warten.');
    await expect(page).toHaveURL(/\/pin/);
    expect(attempts).toBe(6);
  });

  test('Redirect preserves original target URL and returns there after login', async ({ page }) => {
    await setupPinAuthMocks(page, { dataMocks: setupDocumentsMocks });
    await page.goto('/dokumente?typ=RECHNUNG');
    await expect(page).toHaveURL(/\/pin\?redirect=/);
    expect(new URL(page.url()).searchParams.get('redirect')).toBe('/dokumente?typ=RECHNUNG');

    await page.locator('#pin').fill('1234');
    await page.getByRole('button', { name: /anmelden/i }).click();
    await expect(page).toHaveURL('/dokumente?typ=RECHNUNG');
  });

  test('Session expiry mid-use: 401 interceptor leads to PIN page exactly once', async ({ page }) => {
    const session = await setupPinAuthMocks(page, { dataMocks: setupSearchMocks, authenticated: true });
    await page.goto('/suche');
    await expect(page.getByText('Stromrechnung Januar 2026').first()).toBeVisible();

    // Sitzung laeuft serverseitig ab; die naechste Suche (ohne Reload) bekommt
    // 401. Mehrere parallele 401 duerfen den redirect-Parameter nicht
    // verschachteln.
    session.expire();
    const search = page.getByRole('searchbox', { name: 'Dokumente durchsuchen' });
    await search.fill('Strom');
    await search.press('Enter');

    await expect(page).toHaveURL(/\/pin\?redirect=/);
    expect(new URL(page.url()).searchParams.get('redirect')).toBe('/suche?q=Strom');

    await page.locator('#pin').fill('1234');
    await page.getByRole('button', { name: /anmelden/i }).click();
    await expect(page).toHaveURL('/suche?q=Strom');
    await expect(page.getByText('Stromrechnung Januar 2026').first()).toBeVisible();
  });

  test('No redirect when PIN is disabled', async ({ page }) => {
    await setupDashboardMocks(page);
    await page.goto('/');
    await expect(page).toHaveURL('/');
    await expect(page.getByTestId('stat-cards')).toContainText(String(MOCK_STATS.total_documents));
  });
});
