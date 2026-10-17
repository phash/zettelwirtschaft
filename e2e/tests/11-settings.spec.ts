import { test, expect, Page } from '@playwright/test';
import {
  setupSettingsMocks,
  MOCK_SYSTEM_HEALTH,
  MOCK_FILING_SCOPES,
  MOCK_BACKUPS,
} from '../helpers/mock.helpers';
import type { SystemHealthResponse } from '../helpers/api-types';

// Weitere System-Faelle (Ordner je Betriebsmodus, Vektor-Rebuild,
// Vollbackup und E-Mail-Abruf als Hintergrund-Vorgang, Modell-Anzeige,
// 60-s-Polling) stehen in 32-settings-regressions.spec.ts; E-Mail-Konten in
// 18-email-accounts.spec.ts.

const card = (page: Page, heading: string) =>
  page.locator('.card').filter({ has: page.getByRole('heading', { name: heading, exact: true }) });

test.describe('Settings', () => {
  test.beforeEach(async ({ page }) => {
    await setupSettingsMocks(page);
  });

  // --- System Status ---

  test('Shows system status as healthy with app version', async ({ page }) => {
    await page.goto('/einstellungen');
    await expect(page.getByText('System betriebsbereit')).toBeVisible();
    await expect(page.getByTestId('app-version')).toHaveText(`v${MOCK_SYSTEM_HEALTH.app_version}`);
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
    await page.route('**/api/system/update-check**', (route) =>
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
    // Veroeffentlichungsdatum deutsch formatiert, nicht als ISO-Rohwert
    await expect(page.getByText('(1.7.2026)')).toBeVisible();
  });

  test('Shows component statuses', async ({ page }) => {
    await page.goto('/einstellungen');
    for (const name of Object.keys(MOCK_SYSTEM_HEALTH.components)) {
      await expect(page.getByTestId(`component-${name}`)).toContainText('OK');
    }
    await expect(page.getByTestId('component-database')).toContainText('Datenbank');
  });

  test('Shows storage information formatted', async ({ page }) => {
    await page.goto('/einstellungen');
    const storage = card(page, 'Speicher');
    await expect(storage).toBeVisible();
    await expect(storage).toContainText('Datenbank2,5 MB');
    await expect(storage).toContainText('Archiv150,0 MB');
    await expect(storage).toContainText('Uploads25,0 MB');
    await expect(storage).toContainText('Festplatte frei50,0 GB');
    await expect(storage).toContainText('206,0 GB / 256,0 GB belegt');
  });

  test('Shows install path', async ({ page }) => {
    await page.goto('/einstellungen');
    await expect(page.getByText(MOCK_SYSTEM_HEALTH.install_path)).toBeVisible();
  });

  test('Shows a warning when a component is unavailable', async ({ page }) => {
    await page.route('**/api/system/health', (route) =>
      route.fulfill({
        json: {
          ...MOCK_SYSTEM_HEALTH,
          status: 'warning',
          components: {
            ...MOCK_SYSTEM_HEALTH.components,
            chromadb: { status: 'warning', message: 'Nicht erreichbar' },
          },
        } satisfies SystemHealthResponse,
      })
    );

    await page.goto('/einstellungen');
    await expect(page.getByText('System eingeschränkt')).toBeVisible();
    await expect(page.getByTestId('component-chromadb')).toContainText('Nicht erreichbar');
    await expect(page.getByTestId('component-chromadb')).toContainText('Die Vektor-Datenbank ist nicht erreichbar.');
  });

  test('Health load error shows a message', async ({ page }) => {
    await page.route('**/api/system/health', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/einstellungen');
    await expect(page.getByText('Systemstatus konnte nicht geladen werden.')).toBeVisible();
  });

  // --- Filing Scopes ---

  test('Shows filing scopes list with default badge', async ({ page }) => {
    await page.goto('/einstellungen');
    const scopes = card(page, 'Ablagebereiche');
    for (const scope of MOCK_FILING_SCOPES) {
      await expect(scopes.getByText(scope.name, { exact: true })).toBeVisible();
    }
    await expect(scopes.getByText('Standard', { exact: true })).toHaveCount(1);
    // Standard-Bereich hat keinen Loeschen-Button
    await expect(scopes.getByRole('button', { name: 'Löschen' })).toHaveCount(1);
  });

  test('Create new filing scope', async ({ page }) => {
    await page.goto('/einstellungen');
    const scopes = card(page, 'Ablagebereiche');
    await scopes.getByRole('button', { name: '+ Neuer Bereich' }).click();
    await page.getByLabel('Name', { exact: true }).fill('Vermietung');
    await page.getByLabel('Schlüsselwörter (kommagetrennt)').fill('Miete, Nebenkosten');

    const [req] = await Promise.all([
      page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/filing-scopes'),
      scopes.getByRole('button', { name: 'Speichern' }).click(),
    ]);
    expect(req.postDataJSON()).toMatchObject({ name: 'Vermietung', keywords: ['Miete', 'Nebenkosten'] });
  });

  test('Delete non-default scope after confirmation', async ({ page }) => {
    const deleted: string[] = [];
    await page.route('**/api/filing-scopes/*', (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      deleted.push(new URL(route.request().url()).pathname);
      return route.fulfill({ status: 204, body: '' });
    });
    await page.goto('/einstellungen');

    await card(page, 'Ablagebereiche').getByRole('button', { name: 'Löschen' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Ablagebereich löschen');
    expect(deleted).toEqual([]);
    await dialog.getByRole('button', { name: 'Löschen' }).click();

    await expect.poll(() => deleted).toEqual(['/api/filing-scopes/scope-praxis']);
  });

  // --- Folder Configuration ---

  test('Shows folder configuration inputs with the deletion rule', async ({ page }) => {
    await page.goto('/einstellungen');
    await expect(page.getByLabel('Eingangsordner (Watch-Ordner)')).toBeVisible();
    await expect(page.getByText('in den Unterordner „verarbeitet“ verschoben – gelöscht wird dort nichts')).toBeVisible();
  });

  test('Save folder configuration sends the entered paths', async ({ page }) => {
    await page.goto('/einstellungen');
    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill('V:\\Zettelwirtschaft\\Watch');

    const folders = card(page, 'Ordner');
    const [req] = await Promise.all([
      page.waitForRequest((r) => r.method() === 'PUT' && new URL(r.url()).pathname === '/api/system/settings'),
      folders.getByRole('button', { name: 'Speichern' }).click(),
    ]);
    expect(req.postDataJSON()).toMatchObject({ watch_dir_host: 'V:\\Zettelwirtschaft\\Watch' });
  });

  // --- Maintenance ---

  for (const [label, endpoint] of [
    ['Datenbank optimieren', 'optimize-db'],
    ['Suchindex neu aufbauen', 'rebuild-index'],
  ] as const) {
    test(`"${label}" calls ${endpoint}`, async ({ page }) => {
      await page.goto('/einstellungen');
      const [req] = await Promise.all([
        page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/api/system/maintenance/${endpoint}`)),
        page.getByRole('button', { name: label }).click(),
      ]);
      expect(req).toBeTruthy();
    });
  }

  test('Failed maintenance action is reported', async ({ page }) => {
    await page.route('**/api/system/maintenance/optimize-db', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/einstellungen');
    await page.getByRole('button', { name: 'Datenbank optimieren' }).click();
    await expect(page.getByText('Interner Serverfehler')).toBeVisible();
  });

  // --- Backups ---

  test('Shows backup list with formatted size and download link', async ({ page }) => {
    await page.goto('/einstellungen');
    const backup = MOCK_BACKUPS.backups[0];
    const backups = card(page, 'Backups');
    await expect(backups).toContainText(backup.filename);
    await expect(backups).toContainText('1,2 MB');
    await expect(backups.getByRole('link', { name: 'Download' })).toHaveAttribute('href', `/api/system/backups/${backup.filename}`);
  });

  test('DB backup is started without the full flag', async ({ page }) => {
    await page.goto('/einstellungen');
    const [req] = await Promise.all([
      page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/system/backup'),
      page.getByRole('button', { name: 'Backup (DB)' }).click(),
    ]);
    expect(new URL(req.url()).searchParams.get('full')).toBe('false');
  });
});
