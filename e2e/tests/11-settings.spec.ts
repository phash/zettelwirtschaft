import { test, expect } from '@playwright/test';
import {
  setupSettingsMocks,
  MOCK_SYSTEM_HEALTH,
  MOCK_FILING_SCOPES,
  MOCK_EMAIL_ACCOUNTS,
  MOCK_BACKUPS,
} from '../helpers/mock.helpers';

test.describe('Settings', () => {
  test.beforeEach(async ({ page }) => {
    await setupSettingsMocks(page);
  });

  // --- System Status ---

  test('Shows system status as healthy', async ({ page }) => {
    await page.goto('/einstellungen');
    await expect(page.locator('text=betriebsbereit').first()).toBeVisible();
  });

  test('Shows app version', async ({ page }) => {
    await page.goto('/einstellungen');
    // Look for version in the main content area (not sidebar)
    await expect(page.locator('main').locator(`text=v${MOCK_SYSTEM_HEALTH.app_version}`).first()).toBeVisible();
  });

  // --- Update check (opt-in) ---

  test('Shows opt-in update check card, disabled by default', async ({ page }) => {
    await page.goto('/einstellungen');
    await expect(page.getByRole('heading', { name: 'Updates' })).toBeVisible();
    await expect(page.getByText('Automatisch auf Updates prüfen')).toBeVisible();
    await expect(page.getByText('Installiert: 1.4.0')).toBeVisible();
    // No availability banner while the check is disabled
    await expect(page.getByText(/Update verfügbar/)).toHaveCount(0);
  });

  test('Surfaces an available update when the check is enabled', async ({ page }) => {
    await page.route('**/api/system/update-check', (route) =>
      route.fulfill({
        json: {
          enabled: true,
          current_version: '1.4.0',
          latest_version: '1.5.0',
          update_available: true,
          release_url: 'https://zettelwirtschaft.mr-development.de/download.html',
          published_at: '2026-07-01',
          notes: 'Testnotiz',
        },
      })
    );
    await page.goto('/einstellungen');
    await expect(page.getByText(/Update verfügbar: Version 1\.5\.0/)).toBeVisible();
    await expect(page.getByRole('link', { name: /Zum Download/ })).toBeVisible();
  });

  test('Shows component statuses', async ({ page }) => {
    await page.goto('/einstellungen');

    // Backend, SQLite, Ollama, ChromaDB should all show OK
    await expect(page.locator('text=/backend/i').first()).toBeVisible();
    await expect(page.locator('text=/ollama/i').first()).toBeVisible();
  });

  test('Shows storage information', async ({ page }) => {
    await page.goto('/einstellungen');

    // Speicher section heading and labels
    await expect(page.locator('text=Speicher').first()).toBeVisible();
    await expect(page.locator('text=Datenbank').first()).toBeVisible();
  });

  test('Shows install path', async ({ page }) => {
    await page.goto('/einstellungen');
    await expect(page.locator(`text=${MOCK_SYSTEM_HEALTH.install_path}`).first()).toBeVisible();
  });

  // --- Filing Scopes ---

  test('Shows filing scopes list', async ({ page }) => {
    await page.goto('/einstellungen');

    for (const scope of MOCK_FILING_SCOPES) {
      await expect(page.locator(`text=${scope.name}`).first()).toBeVisible();
    }
  });

  test('Default scope shows badge', async ({ page }) => {
    await page.goto('/einstellungen');
    await expect(page.locator('text=/standard/i').first()).toBeVisible();
  });

  test('Create new filing scope', async ({ page }) => {
    let scopeCreated = false;
    await page.route('**/api/filing-scopes', (route) => {
      if (route.request().method() === 'POST') {
        scopeCreated = true;
        const body = JSON.parse(route.request().postData() || '{}');
        return route.fulfill({ json: { id: 3, ...body, slug: 'test' } });
      }
      return route.fulfill({ json: MOCK_FILING_SCOPES });
    });

    await page.goto('/einstellungen');

    // Click new scope button
    const newBtn = page.getByRole('button', { name: /neuer bereich/i }).first();
    await newBtn.click();

    // Fill form
    const nameInput = page.locator('input[placeholder*="Name"], input').filter({ hasText: '' });
    const nameField = page.locator('input').nth(-3); // Find the name input in the form
    // Look for visible input fields in the scope form
    const inputs = page.locator('input[type="text"]');
    const count = await inputs.count();
    if (count > 0) {
      await inputs.first().fill('Testbereich');
    }

    // Save
    const saveBtn = page.getByRole('button', { name: /speichern/i }).last();
    await saveBtn.click();

    await page.waitForTimeout(500);
    expect(scopeCreated).toBeTruthy();
  });

  test('Delete non-default scope', async ({ page }) => {
    let scopeDeleted = false;
    await page.route('**/api/filing-scopes/*', (route) => {
      if (route.request().method() === 'DELETE') {
        scopeDeleted = true;
        return route.fulfill({ json: { message: 'OK' } });
      }
      return route.continue();
    });

    await page.goto('/einstellungen');

    // Find delete link for non-default scope
    const deleteLinks = page.locator('a:has-text("Löschen"), button:has-text("Löschen")');
    const count = await deleteLinks.count();
    if (count > 0) {
      await deleteLinks.first().click();

      // Confirm dialog
      const confirmBtn = page.getByRole('button', { name: /löschen/i }).last();
      if (await confirmBtn.isVisible()) {
        await confirmBtn.click();
      }

      await page.waitForTimeout(500);
      expect(scopeDeleted).toBeTruthy();
    }
  });

  // --- Folder Configuration ---

  test('Shows folder configuration inputs', async ({ page }) => {
    await page.goto('/einstellungen');

    await expect(page.locator('text=/eingangsordner/i').first()).toBeVisible();
  });

  test('Save folder configuration', async ({ page }) => {
    let settingsSaved = false;
    await page.route('**/api/system/settings', (route) => {
      if (route.request().method() === 'PUT') {
        settingsSaved = true;
        return route.fulfill({ json: { message: 'OK' } });
      }
      return route.fulfill({ json: { watch_dir: '/app/data/watch', export_dir: '/app/data/export', watch_dir_host: '', export_dir_host: '' } });
    });

    await page.goto('/einstellungen');

    // Find and modify folder input
    const folderInputs = page.locator('input[class*="mono"], input[style*="mono"]');
    const count = await folderInputs.count();
    if (count > 0) {
      await folderInputs.first().fill('V:\\Zettelwirtschaft\\Watch');
    }

    // Save
    const saveBtn = page.getByRole('button', { name: /speichern/i }).first();
    await saveBtn.click();

    await page.waitForTimeout(500);
    expect(settingsSaved).toBeTruthy();
  });

  // --- Maintenance ---

  test('Optimize database button works', async ({ page }) => {
    let optimizeCalled = false;
    await page.route('**/api/system/maintenance/optimize-db', (route) => {
      optimizeCalled = true;
      return route.fulfill({ json: { message: 'Datenbank optimiert' } });
    });

    await page.goto('/einstellungen');

    const optimizeBtn = page.getByRole('button', { name: /datenbank optimieren/i }).first();
    await optimizeBtn.click();

    await page.waitForTimeout(500);
    expect(optimizeCalled).toBeTruthy();
  });

  test('Rebuild search index button works', async ({ page }) => {
    let rebuildCalled = false;
    await page.route('**/api/system/maintenance/rebuild-index', (route) => {
      rebuildCalled = true;
      return route.fulfill({ json: { message: 'Index neu aufgebaut' } });
    });

    await page.goto('/einstellungen');

    const rebuildBtn = page.getByRole('button', { name: /suchindex/i }).first();
    await rebuildBtn.click();

    await page.waitForTimeout(500);
    expect(rebuildCalled).toBeTruthy();
  });

  test('Rebuild vectors button works', async ({ page }) => {
    let vectorsCalled = false;
    await page.route('**/api/system/maintenance/rebuild-vectors', (route) => {
      vectorsCalled = true;
      return route.fulfill({ json: { message: 'Vektor-Index aufgebaut' } });
    });

    await page.goto('/einstellungen');

    const vectorsBtn = page.getByRole('button', { name: /vektor/i }).first();
    await vectorsBtn.click();

    await page.waitForTimeout(500);
    expect(vectorsCalled).toBeTruthy();
  });

  // --- Backups ---

  test('Create backup button works', async ({ page }) => {
    let backupCreated = false;
    await page.route('**/api/system/backup*', (route) => {
      if (route.request().method() === 'POST') {
        backupCreated = true;
        return route.fulfill({ json: { message: 'Backup erstellt', filename: 'backup_test.zip' } });
      }
      return route.continue();
    });

    await page.goto('/einstellungen');

    // Find backup button - exact text "Backup (DB)"
    const backupBtn = page.getByRole('button', { name: /backup/i }).first();
    await backupBtn.click();

    await page.waitForTimeout(1000);
    expect(backupCreated).toBeTruthy();
  });

  test('Shows backup list', async ({ page }) => {
    await page.goto('/einstellungen');

    const firstBackup = MOCK_BACKUPS.backups[0];
    await expect(page.locator(`text=${firstBackup.filename}`).first()).toBeVisible();
  });

  // --- Email Accounts ---

  test('Shows email accounts section', async ({ page }) => {
    await page.goto('/einstellungen');

    const firstAccount = MOCK_EMAIL_ACCOUNTS[0];
    await expect(page.locator(`text=${firstAccount.name}`).first()).toBeVisible();
  });

  // --- System Health Degraded ---

  test('Shows degraded status when component fails', async ({ page }) => {
    await page.route('**/api/system/health', (route) =>
      route.fulfill({
        json: {
          ...MOCK_SYSTEM_HEALTH,
          status: 'error',
          chromadb: 'error',
          components: {
            ...MOCK_SYSTEM_HEALTH.components,
            chromadb: { status: 'error', message: 'ChromaDB nicht erreichbar' },
          },
        },
      })
    );

    await page.goto('/einstellungen');
    await expect(page.locator('text=eingeschränkt').first()).toBeVisible();
  });

  // --- Auto-refresh ---

  test('System status refreshes automatically', async ({ page }) => {
    let healthCallCount = 0;
    await page.route('**/api/system/health', (route) => {
      healthCallCount++;
      return route.fulfill({ json: MOCK_SYSTEM_HEALTH });
    });

    await page.goto('/einstellungen');
    await page.waitForTimeout(12000); // Wait for at least one auto-refresh (10s interval)

    expect(healthCallCount).toBeGreaterThanOrEqual(2);
  });
});
