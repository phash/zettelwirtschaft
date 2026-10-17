import { test, expect, Page } from '@playwright/test';
import { setupBaseMocks, makeMockTask, mockBackgroundTaskPolling, MOCK_SYSTEM_HEALTH } from '../helpers/mock.helpers';
import type { TaskResponse } from '../helpers/api-types';

// Regressionstests System-Seite: Ordner je Betriebsmodus, Vektor-Rebuild-
// Status, Backup/E-Mail-Abruf als Hintergrund-Vorgang, E-Mail-Formular,
// Validierungsfehler, Health-Anzeige und Polling-Intervall.

const ACCOUNT = {
  id: 7,
  name: 'Rechnungs-Postfach',
  imap_host: 'imap.example.org',
  imap_port: 993,
  use_ssl: true,
  username: 'rechnungen@example.org',
  folder_inbox: 'INBOX',
  folder_processed: 'Verarbeitet',
  schedule_type: 'CRON',
  cron_expression: '0 8 * * *',
  is_active: true,
  last_checked_at: null,
  last_error: null,
  filing_scope_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

interface SettingsState {
  settingsPuts: Record<string, unknown>[];
  accountPuts: Record<string, unknown>[];
  backupsLoads: number;
}

async function setupSettings(page: Page, opts: { folder?: Record<string, unknown>; health?: Record<string, unknown> } = {}) {
  await setupBaseMocks(page);
  const state: SettingsState = { settingsPuts: [], accountPuts: [], backupsLoads: 0 };
  const folder = opts.folder ?? { watch_dir: '/app/data/watch', export_dir: '', watch_dir_host: '', export_dir_host: '', deployment_mode: 'docker' };
  await page.route('**/api/system/settings', (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      state.settingsPuts.push(body);
      return route.fulfill({ json: { ...folder, ...body, restart_required: false } });
    }
    return route.fulfill({ json: folder });
  });
  if (opts.health) {
    await page.route('**/api/system/health', (route) => route.fulfill({ json: opts.health }));
  }
  await page.route('**/api/system/backups', (route) => {
    state.backupsLoads += 1;
    return route.fulfill({ json: { backups: [] } });
  });
  await page.route('**/api/email/settings', (route) =>
    route.fulfill({ json: { timezone: 'Europe/Berlin', unseen_only: true, max_per_run: 25, idle_interval_minutes: 5 } })
  );
  await page.route('**/api/email/accounts', (route) => route.fulfill({ json: [ACCOUNT] }));
  await page.route('**/api/email/accounts/7', (route) => {
    if (route.request().method() === 'PUT') {
      state.accountPuts.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ json: ACCOUNT });
    }
    return route.fallback();
  });
  return state;
}

// Vorgang bleibt RUNNING, bis der Test release() aufruft. So haengt die
// Pruefung der Fortschrittsanzeige nicht an der Poll-Taktung (mit
// mockBackgroundTaskPolling ist RUNNING nur ein Poll-Intervall lang sichtbar
// — unter Last zu kurz, der Test wurde flaky).
async function mockGatedTask(page: Page, taskId: string, running: Partial<TaskResponse>, final: Partial<TaskResponse>) {
  let released = false;
  await page.route(`**/api/tasks/${taskId}`, (route) =>
    route.fulfill({ json: makeMockTask({ id: taskId, ...(released ? final : running) }) })
  );
  return () => { released = true; };
}

test.describe('Settings regressions', () => {
  test('native mode edits the real watch folder, without container hints', async ({ page }) => {
    const state = await setupSettings(page, {
      folder: { watch_dir: '', export_dir: '', watch_dir_host: '', export_dir_host: '', deployment_mode: 'native' },
    });
    await page.goto('/einstellungen');

    const watch = page.getByLabel('Eingangsordner (Watch-Ordner)');
    await watch.fill('  "D:\\Scans\\Eingang"  ');
    await watch.blur();
    await expect(watch).toHaveValue('D:\\Scans\\Eingang');
    await expect(page.getByText('Container-Pfad')).toHaveCount(0);
    await expect(page.getByTestId('folder-advanced')).toHaveCount(0);

    await page.getByRole('button', { name: 'Speichern', exact: true }).first().click();
    await expect.poll(() => state.settingsPuts.length).toBe(1);
    expect(state.settingsPuts[0]).toMatchObject({ watch_dir: 'D:\\Scans\\Eingang', watch_dir_host: '' });
    expect(state.settingsPuts[0]).not.toHaveProperty('deployment_mode');
    await expect(page.getByText('Neustart erforderlich')).toHaveCount(0);

    // Zuruecksetzen = Backend-Standard (leer), kein hart codierter Docker-Pfad.
    await page.getByRole('button', { name: 'Auf Standard zurücksetzen' }).click();
    await expect(watch).toHaveValue('');
  });

  test('docker mode strips quotes from typed host paths on save', async ({ page }) => {
    const state = await setupSettings(page);
    await page.goto('/einstellungen');

    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill('"C:\\Users\\Public"');
    await page.getByRole('button', { name: 'Speichern', exact: true }).first().click();
    await expect.poll(() => state.settingsPuts.length).toBe(1);
    expect(state.settingsPuts[0].watch_dir_host).toBe('C:\\Users\\Public');
  });

  test('vector rebuild runs as background task and reports a failed run', async ({ page }) => {
    await setupSettings(page);
    await page.route('**/api/system/maintenance/rebuild-vectors', (route) =>
      route.fulfill({ status: 202, json: makeMockTask({ id: 'task-vec', kind: 'rebuild_vectors', key: 'global' }) })
    );
    const release = await mockGatedTask(
      page, 'task-vec',
      { status: 'RUNNING', kind: 'rebuild_vectors', key: 'global', progress_text: '1 von 3 Dokumenten verarbeitet', progress_percent: 33 },
      {
        status: 'FAILED', kind: 'rebuild_vectors', key: 'global',
        error: 'Es wurden keine Abschnitte eingebettet. Ist das Embedding-Modell in Ollama verfügbar?',
      },
    );
    await page.goto('/einstellungen');

    await page.getByRole('button', { name: 'Vektor-Index aufbauen' }).click();
    await expect(page.getByTestId('vector-progress')).toContainText('1 von 3 Dokumenten verarbeitet');
    release();
    await expect(page.getByText(/keine Abschnitte eingebettet/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole('button', { name: 'Vektor-Index aufbauen' })).toBeEnabled();
  });

  test('vector rebuild 409 attaches to the running rebuild instead of showing an error', async ({ page }) => {
    await setupSettings(page);
    const running = makeMockTask({ id: 'task-vec', kind: 'rebuild_vectors', key: 'global', status: 'RUNNING' });
    await page.route('**/api/system/maintenance/rebuild-vectors', (route) =>
      route.fulfill({ status: 409, json: { detail: 'Der Vektor-Index wird bereits neu aufgebaut.', task: running } })
    );
    await mockBackgroundTaskPolling(page, 'task-vec', [
      { status: 'SUCCEEDED', kind: 'rebuild_vectors', key: 'global', result: { total: 2, processed: 2, chunks: 5, failed: 0 } },
    ]);
    await page.goto('/einstellungen');
    await page.getByRole('button', { name: 'Vektor-Index aufbauen' }).click();
    await expect(page.getByText('Vektor-Index aufgebaut: 5 Abschnitte aus 2 Dokumenten.')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Ist ChromaDB erreichbar/)).toHaveCount(0);
  });

  test('a rebuild still running (e.g. initial vectorization) is resumed on open', async ({ page }) => {
    await setupSettings(page);
    const running = makeMockTask({
      id: 'task-init', kind: 'rebuild_vectors', key: 'global', status: 'RUNNING', progress_text: '4 von 10 Dokumenten verarbeitet',
    });
    await page.route('**/api/tasks?*', (route) => {
      const params = new URL(route.request().url()).searchParams;
      return route.fulfill({ json: params.get('kind') === 'rebuild_vectors' ? [running] : [] });
    });
    await mockBackgroundTaskPolling(page, 'task-init', [
      { status: 'RUNNING', kind: 'rebuild_vectors', key: 'global', progress_text: '4 von 10 Dokumenten verarbeitet' },
    ]);
    await page.goto('/einstellungen');
    await expect(page.getByTestId('vector-progress')).toContainText('4 von 10 Dokumenten verarbeitet');
    await expect(page.getByRole('button', { name: 'Vektorisiere...' })).toBeDisabled();
  });

  test('full backup runs as background task with progress and reloads the list', async ({ page }) => {
    const state = await setupSettings(page);
    await page.route('**/api/system/backup?*', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 202, json: makeMockTask({ id: 'task-bk', kind: 'backup', key: 'global' }) })
        : route.fallback()
    );
    const release = await mockGatedTask(
      page, 'task-bk',
      { status: 'RUNNING', kind: 'backup', key: 'global', progress_text: 'Sichere Dokumente …', progress_percent: 40 },
      { status: 'SUCCEEDED', kind: 'backup', key: 'global', result: { filename: 'backup_full.zip', is_full: true, size_bytes: 2048, message: 'Vollbackup erstellt' } },
    );
    await page.goto('/einstellungen');
    const loadsBefore = state.backupsLoads;

    await page.getByRole('button', { name: 'Vollbackup' }).click();
    await expect(page.getByTestId('backup-progress')).toContainText('Sichere Dokumente');
    release();
    await expect(page.getByText('Vollbackup erstellt (backup_full.zip).')).toBeVisible({ timeout: 10_000 });
    expect(state.backupsLoads).toBeGreaterThan(loadsBefore);
  });

  test('manual e-mail fetch is polled and reports skipped mails', async ({ page }) => {
    await setupSettings(page);
    await page.route('**/api/email/accounts/7/fetch', (route) =>
      route.fulfill({ status: 202, json: makeMockTask({ id: 'task-mail', kind: 'email_fetch', key: '7' }) })
    );
    const release = await mockGatedTask(
      page, 'task-mail',
      { status: 'RUNNING', kind: 'email_fetch', key: '7', progress_text: 'Prüfe E-Mail 1 von 3 …' },
      { status: 'SUCCEEDED', kind: 'email_fetch', key: '7', result: { total: 3, relevant: 1, irrelevant: 1, skipped: 1, failed: 0, remaining: 0, error: null } },
    );
    await page.goto('/einstellungen');

    await page.getByRole('button', { name: 'Jetzt abrufen' }).click();
    await expect(page.getByTestId('email-fetch-progress-7')).toContainText('Prüfe E-Mail 1 von 3');
    release();
    await expect(page.getByText('Rechnungs-Postfach: 3 E-Mails geprüft: 1 relevant, 1 irrelevant, 1 bereits bekannt')).toBeVisible({ timeout: 10_000 });
  });

  test('failed e-mail fetch shows the task error', async ({ page }) => {
    await setupSettings(page);
    await page.route('**/api/email/accounts/7/fetch', (route) =>
      route.fulfill({ status: 202, json: makeMockTask({ id: 'task-mail', kind: 'email_fetch', key: '7' }) })
    );
    await mockBackgroundTaskPolling(page, 'task-mail', [
      { status: 'FAILED', kind: 'email_fetch', key: '7', error: 'Anmeldung fehlgeschlagen' },
    ]);
    await page.goto('/einstellungen');
    await page.getByRole('button', { name: 'Jetzt abrufen' }).click();
    await expect(page.getByText('Rechnungs-Postfach: Anmeldung fehlgeschlagen')).toBeVisible({ timeout: 10_000 });
  });

  test('e-mail form: new account after edit starts empty; edit can deactivate', async ({ page }) => {
    const state = await setupSettings(page);
    await page.goto('/einstellungen');

    await expect(page.getByTestId('email-import-rules')).toContainText('ungelesene');
    await page.getByRole('button', { name: 'Bearbeiten' }).last().click();
    await expect(page.getByLabel('Anzeigename')).toHaveValue('Rechnungs-Postfach');
    await expect(page.getByTestId('cron-help')).toContainText('Europe/Berlin');

    await page.getByTestId('email-active-toggle').uncheck();
    await page.getByRole('button', { name: 'Speichern', exact: true }).last().click();
    await expect.poll(() => state.accountPuts.length).toBe(1);
    expect(state.accountPuts[0].is_active).toBe(false);

    await page.getByRole('button', { name: 'Bearbeiten' }).last().click();
    await page.getByRole('button', { name: '+ Neues Konto' }).click();
    await expect(page.getByText('Neues E-Mail-Konto')).toBeVisible();
    await expect(page.getByLabel('Anzeigename')).toHaveValue('');
    await expect(page.getByLabel('IMAP-Server')).toHaveValue('');

    // Pflichtfelder werden clientseitig geprueft (kein 422-JSON im Toast).
    await page.getByRole('button', { name: 'Konto hinzufügen' }).click();
    await expect(page.getByText('Bitte einen Anzeigenamen eingeben.')).toBeVisible();
  });

  test('filing scope: empty name is caught client-side, server 422 shown in German', async ({ page }) => {
    await setupSettings(page);
    await page.route('**/api/filing-scopes', (route) => {
      if (route.request().method() === 'POST') {
        return route.fulfill({
          status: 422,
          json: { detail: [{ type: 'string_too_long', loc: ['body', 'name'], msg: 'too long', ctx: { max_length: 200 } }] },
        });
      }
      return route.fallback();
    });
    await page.goto('/einstellungen');

    await page.getByRole('button', { name: '+ Neuer Bereich' }).click();
    await page.getByRole('button', { name: 'Speichern', exact: true }).nth(1).click();
    await expect(page.getByText('Bitte einen Namen eingeben.')).toBeVisible();

    await page.locator('#scope-name').fill('x'.repeat(10));
    await page.getByRole('button', { name: 'Speichern', exact: true }).nth(1).click();
    await expect(page.getByText('Bitte Eingaben prüfen: Name darf höchstens 200 Zeichen haben.')).toBeVisible();
    await expect(page.getByText('string_too_long')).toHaveCount(0);
  });

  test('health: German component labels, configured models, no "vunknown"', async ({ page }) => {
    await setupSettings(page, {
      health: {
        ...MOCK_SYSTEM_HEALTH,
        status: 'warning',
        app_version: 'unknown',
        deployment_mode: 'native',
        components: {
          database: { status: 'ok' },
          ollama: {
            status: 'warning',
            message: 'Modell qwen2.5:7b-instruct nicht installiert',
            configured_models: [
              { name: 'qwen2.5:7b-instruct', purpose: 'llm', available: false, downloading: false },
              { name: 'bge-m3', purpose: 'embedding', available: true, downloading: false },
            ],
            // Altes Feld einer frueheren Backend-Version: wird nicht mehr gelistet.
            models: Array.from({ length: 26 }, (_, i) => `modell-${i}:latest`),
          },
          chromadb: { status: 'ok', vectors: 12 },
        },
      },
    });
    await page.goto('/einstellungen');

    await expect(page.getByTestId('component-database')).toContainText('Datenbank');
    await expect(page.getByTestId('component-chromadb')).toContainText('Vektor-Datenbank');
    const models = page.getByTestId('configured-models');
    await expect(models).toContainText('qwen2.5:7b-instruct');
    await expect(models).toContainText('fehlt');
    await expect(models).toContainText('bge-m3');
    await expect(page.getByText('modell-3:latest')).toHaveCount(0);
    await expect(page.getByText('vunknown')).toHaveCount(0);
    await expect(page.getByTestId('app-version')).toHaveCount(0);
  });

  test('health is polled every 60 s, not every 10 s', async ({ page }) => {
    await page.clock.install();
    await setupSettings(page);
    let healthCalls = 0;
    await page.route('**/api/system/health', (route) => {
      healthCalls += 1;
      return route.fulfill({ json: MOCK_SYSTEM_HEALTH });
    });
    await page.goto('/einstellungen');
    await expect(page.getByText('betriebsbereit')).toBeVisible();
    const initial = healthCalls;

    await page.clock.runFor(15_000);
    expect(healthCalls).toBe(initial);
    await page.clock.runFor(50_000);
    await expect.poll(() => healthCalls).toBe(initial + 1);
  });
});
