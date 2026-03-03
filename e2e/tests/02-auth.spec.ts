import { test, expect } from '@playwright/test';
import { setupPinAuthMocks, setupBaseMocks, MOCK_AUTH_STATUS } from '../helpers/mock.helpers';

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

    const pinInput = page.locator('#pin');
    const submitBtn = page.getByRole('button', { name: /anmelden/i });

    await pinInput.fill('1234');
    await expect(submitBtn).toBeEnabled();
  });

  test('Successful login redirects to dashboard', async ({ page }) => {
    await setupPinAuthMocks(page);

    // Mock dashboard endpoints for after redirect
    await page.route('**/api/stats', (route) =>
      route.fulfill({ json: { total_documents: 0, documents_this_month: 0, pending_reviews: 0, expiring_warranties_30d: 0, processing_jobs: 0 } })
    );
    await page.route('**/api/jobs**', (route) =>
      route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } })
    );
    await page.route('**/api/documents?*', (route) =>
      route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } })
    );
    await page.route('**/api/documents', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      return route.continue();
    });
    await page.route('**/api/email/stats', (route) =>
      route.fulfill({ json: { total_accounts: 0, active_accounts: 0, total_processed: 0, relevant_count: 0, irrelevant_count: 0, failed_count: 0 } })
    );
    await page.route('**/api/filing-scopes', (route) =>
      route.fulfill({ json: [] })
    );

    // After login, auth status should return authenticated
    await page.route('**/api/auth/login', async (route) => {
      // Override auth status to authenticated after login
      await page.route('**/api/auth/status', (r) =>
        r.fulfill({ json: { pin_enabled: true, authenticated: true } })
      );
      return route.fulfill({ json: { success: true, message: 'OK' } });
    });

    await page.goto('/pin');
    await page.locator('#pin').fill('1234');

    await page.getByRole('button', { name: /anmelden/i }).click();
    await expect(page).toHaveURL('/', { timeout: 10000 });
  });

  test('Failed login shows error message', async ({ page }) => {
    await setupPinAuthMocks(page);
    await page.route('**/api/auth/login', (route) =>
      route.fulfill({ status: 401, json: { detail: 'Falsche PIN' } })
    );

    await page.goto('/pin');
    await page.locator('#pin').fill('0000');
    await page.getByRole('button', { name: /anmelden/i }).click();

    // Error message should appear
    await expect(page.locator('text=Falsche PIN').first()).toBeVisible();
  });

  test('Redirect preserves original target URL', async ({ page }) => {
    await setupPinAuthMocks(page);
    await page.goto('/dokumente');
    await expect(page).toHaveURL(/\/pin\?redirect=.*dokumente/);
  });

  test('No redirect when PIN is disabled', async ({ page }) => {
    await setupBaseMocks(page);
    // setupBaseMocks uses MOCK_AUTH_STATUS (pin_enabled: false)
    // Need dashboard mocks too
    await page.route('**/api/stats', (route) =>
      route.fulfill({ json: { total_documents: 0, this_month: 0, pending_reviews: 0, expiring_warranties: 0, recent_documents: [] } })
    );
    await page.route('**/api/jobs/queue-status', (route) =>
      route.fulfill({ json: { is_paused: false, active_jobs: 0, pending_jobs: 0 } })
    );
    await page.route('**/api/jobs*', (route) =>
      route.fulfill({ json: { jobs: [], total: 0 } })
    );
    await page.route('**/api/email/stats', (route) =>
      route.fulfill({ json: { total_accounts: 0, active_accounts: 0, total_processed: 0, relevant_count: 0, irrelevant_count: 0, failed_count: 0 } })
    );

    await page.goto('/');
    await expect(page).toHaveURL('/');
  });
});
