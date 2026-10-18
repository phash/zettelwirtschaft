import { test, expect, Page } from '@playwright/test';
import { setupBaseMocks, MOCK_SYSTEM_HEALTH } from '../helpers/mock.helpers';
import { TEST_SMB_PASSWORD } from '../helpers/credentials';
import type {
  FolderSettings,
  FolderSettingsUpdate,
  SystemHealthResponse,
  TestFolderRequest,
  TestFolderResponse,
} from '../helpers/api-types';

// Netzwerkordner (UNC + Zugangsdaten), Netzlaufwerk-Vorschlag, Scanner-
// Freigabe und Erreichbarkeit in der Health-Anzeige (planung/13).

const UNC = '\\\\nas\\scans\\eingang';

const NATIVE_FOLDERS = {
  watch_dir: '',
  export_dir: '',
  watch_dir_host: '',
  export_dir_host: '',
  restart_required: false,
  deployment_mode: 'native',
  watch_dir_net_user: '',
  export_dir_net_user: '',
  watch_dir_net_password_set: false,
  export_dir_net_password_set: false,
  watch_poll_interval: 30,
  network_folders_supported: true,
  scanner_share: { configured: false, exists: false, unc_path: '', user: '', path: '', matches_watch_dir: false },
} satisfies FolderSettings;

interface State {
  puts: FolderSettingsUpdate[];
  tests: TestFolderRequest[];
}

interface HttpError {
  status: number;
  detail: string;
}

async function setup(
  page: Page,
  opts: {
    folder?: FolderSettings;
    test?: (body: TestFolderRequest) => TestFolderResponse | HttpError;
    put?: (body: FolderSettingsUpdate) => HttpError | null;
    health?: SystemHealthResponse;
  } = {},
): Promise<State> {
  await setupBaseMocks(page);
  const state: State = { puts: [], tests: [] };
  let folder: FolderSettings = opts.folder ?? NATIVE_FOLDERS;
  await page.route('**/api/system/settings', (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON() as FolderSettingsUpdate;
      state.puts.push(body);
      const error = opts.put?.(body);
      if (error) return route.fulfill({ status: error.status, json: { detail: error.detail } });
      const network = body.watch_dir.startsWith('\\\\');
      const exportNetwork = body.export_dir.startsWith('\\\\');
      folder = {
        ...folder,
        watch_dir: body.watch_dir,
        export_dir: body.export_dir,
        watch_dir_net_user: body.watch_dir_net_user ?? '',
        export_dir_net_user: body.export_dir_net_user ?? '',
        watch_dir_net_password_set: network && (folder.watch_dir_net_password_set || !!body.watch_dir_net_password),
        export_dir_net_password_set: exportNetwork && (folder.export_dir_net_password_set || !!body.export_dir_net_password),
        watch_poll_interval: body.watch_poll_interval ?? folder.watch_poll_interval,
      } satisfies FolderSettings;
      return route.fulfill({ json: folder });
    }
    return route.fulfill({ json: folder });
  });
  await page.route('**/api/system/settings/test-folder', (route) => {
    const body = route.request().postDataJSON() as TestFolderRequest;
    state.tests.push(body);
    const result = opts.test
      ? opts.test(body)
      : { ok: true, kind: 'unc', message: 'Verbindung erfolgreich – Ordner lesbar und beschreibbar.', suggested_paths: [] } satisfies TestFolderResponse;
    if ('status' in result) return route.fulfill({ status: result.status, json: { detail: result.detail } });
    return route.fulfill({ json: result });
  });
  if (opts.health) {
    await page.route('**/api/system/health', (route) => route.fulfill({ json: opts.health }));
  }
  await page.route('**/api/system/backups', (route) => route.fulfill({ json: { backups: [] } }));
  await page.route('**/api/email/settings', (route) =>
    route.fulfill({ json: { timezone: 'Europe/Berlin', unseen_only: true, max_per_run: 25, idle_interval_minutes: 5 } })
  );
  await page.route('**/api/email/accounts', (route) => route.fulfill({ json: [] }));
  return state;
}

test.describe('Netzwerkordner', () => {
  test('UNC-Pfad blendet Zugangsdaten ein, Test und Speichern senden sie', async ({ page }) => {
    const state = await setup(page);
    await page.goto('/einstellungen');

    await expect(page.getByTestId('net-credentials-watch')).toHaveCount(0);
    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill(UNC);
    const box = page.getByTestId('net-credentials-watch');
    await expect(box).toBeVisible();
    await box.getByLabel('Benutzer der Freigabe').fill('scanner');
    await box.getByLabel('Passwort').fill(TEST_SMB_PASSWORD);
    await box.getByLabel('Prüfintervall (Sekunden)').fill('60');

    await page.getByTestId('test-folder-watch').click();
    await expect(page.getByTestId('test-result-watch')).toContainText('Verbindung erfolgreich');
    expect(state.tests[0]).toEqual({ target: 'watch', path: UNC, net_user: 'scanner', net_password: TEST_SMB_PASSWORD });

    await page.getByRole('button', { name: 'Speichern', exact: true }).first().click();
    await expect.poll(() => state.puts.length).toBe(1);
    expect(state.puts[0]).toMatchObject({
      watch_dir: UNC, watch_dir_net_user: 'scanner', watch_dir_net_password: TEST_SMB_PASSWORD, watch_poll_interval: 60,
    });
    expect(state.puts[0]).not.toHaveProperty('scanner_share');
    expect(state.puts[0]).not.toHaveProperty('watch_dir_net_password_set');
    // Nach dem Speichern ist das Passwortfeld wieder leer (nie vorbefuellt)
    await expect(box.getByLabel('Passwort')).toHaveValue('');
    await expect(box.getByLabel('Passwort')).toHaveAttribute('placeholder', 'gespeichert – leer lassen für unverändert');
  });

  test('gespeichertes Passwort wird nie vorbefüllt und unverändert nicht gesendet', async ({ page }) => {
    const state = await setup(page, {
      folder: { ...NATIVE_FOLDERS, watch_dir: UNC, watch_dir_net_user: 'scanner', watch_dir_net_password_set: true },
    });
    await page.goto('/einstellungen');
    const password = page.getByTestId('net-credentials-watch').getByLabel('Passwort');
    await expect(password).toHaveValue('');
    await expect(password).toHaveAttribute('placeholder', 'gespeichert – leer lassen für unverändert');
    await page.getByRole('button', { name: 'Speichern', exact: true }).first().click();
    await expect.poll(() => state.puts.length).toBe(1);
    expect(state.puts[0].watch_dir_net_password).toBeNull();
    expect(state.puts[0].watch_dir_net_user).toBe('scanner');
  });

  test('Verbindungstest zeigt den Fehlergrund', async ({ page }) => {
    await setup(page, {
      test: () => ({ ok: false, kind: 'unc', message: 'Netzwerkordner: Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch.', suggested_paths: [] }),
    });
    await page.goto('/einstellungen');
    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill(UNC);
    await page.getByTestId('test-folder-watch').click();
    await expect(page.getByTestId('test-result-watch')).toContainText('Anmeldung fehlgeschlagen');
  });

  test('Netzlaufwerk: Vorschlag übernehmen setzt den UNC-Pfad', async ({ page }) => {
    const state = await setup(page, {
      test: (body) => body.path.startsWith('V:')
        ? {
          ok: false, kind: 'network_drive',
          message: 'Laufwerk V: ist ein Netzlaufwerk. Der Hintergrunddienst sieht verbundene Netzlaufwerke nicht.',
          suggested_paths: [UNC],
        }
        : { ok: true, kind: 'unc', message: 'Verbindung erfolgreich – Ordner lesbar und beschreibbar.', suggested_paths: [] },
    });
    await page.goto('/einstellungen');
    const watch = page.getByLabel('Eingangsordner (Watch-Ordner)');
    await watch.fill('V:\\eingang');
    await page.getByTestId('test-folder-watch').click();
    await expect(page.getByTestId('test-result-watch')).toContainText('Netzlaufwerk');
    await page.getByTestId('use-suggestion-watch').click();
    await expect(watch).toHaveValue(UNC);
    await expect(page.getByTestId('net-credentials-watch')).toBeVisible();
    expect(state.tests[0]).toEqual({ target: 'watch', path: 'V:\\eingang' });
  });

  test('Scanner-Freigabe eingerichtet: UNC-Pfad und Benutzer, kein Passwort', async ({ page }) => {
    await setup(page, {
      folder: {
        ...NATIVE_FOLDERS,
        scanner_share: {
          configured: true, exists: true, unc_path: '\\\\PC\\Zettelwirtschaft-Eingang', user: 'PC\\zw-scanner',
          path: 'C:\\Daten\\watch', matches_watch_dir: true,
        },
      },
    });
    await page.goto('/einstellungen');
    const share = page.getByTestId('scanner-share');
    await expect(share.getByTestId('scanner-share-unc')).toHaveText('\\\\PC\\Zettelwirtschaft-Eingang');
    await expect(share).toContainText('PC\\zw-scanner');
    await expect(share.getByTestId('scanner-share-mismatch')).toHaveCount(0);
    await expect(share).not.toContainText(TEST_SMB_PASSWORD);
  });

  test('Scanner-Freigabe fehlt: Hinweis auf den Startmenü-Eintrag', async ({ page }) => {
    await setup(page);
    await page.goto('/einstellungen');
    await expect(page.getByTestId('scanner-share-missing')).toContainText('Scanner-Freigabe einrichten');
  });

  test('Scanner-Freigabe zeigt auf einen anderen Ordner: Warnung', async ({ page }) => {
    await setup(page, {
      folder: {
        ...NATIVE_FOLDERS,
        scanner_share: {
          configured: true, exists: true, unc_path: '\\\\PC\\Zettelwirtschaft-Eingang', user: 'PC\\zw-scanner',
          path: 'C:\\Alt', matches_watch_dir: false,
        },
      },
    });
    await page.goto('/einstellungen');
    await expect(page.getByTestId('scanner-share-mismatch')).toContainText('anderen Ordner');
  });

  test('Docker: CIFS-Hinweis, keine Zugangsdaten-Felder, kein Verbindungstest', async ({ page }) => {
    await setup(page, {
      folder: {
        watch_dir: '/app/data/watch', export_dir: '', watch_dir_host: '', export_dir_host: '',
        deployment_mode: 'docker', network_folders_supported: false, scanner_share: null,
      },
    });
    await page.goto('/einstellungen');
    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill(UNC);
    await expect(page.getByTestId('docker-cifs-hint')).toBeVisible();
    await page.getByTestId('docker-cifs-hint').locator('summary').click();
    await expect(page.getByTestId('docker-cifs-hint')).toContainText('credentials=');
    await expect(page.getByTestId('net-credentials-watch')).toHaveCount(0);
    await expect(page.getByTestId('test-folder-watch')).toHaveCount(0);
    await expect(page.getByTestId('scanner-share')).toHaveCount(0);
  });

  test('Speichern scheitert: Fehlermeldung des Backends, eingegebenes Passwort bleibt stehen', async ({ page }) => {
    const detail = 'Watch-Ordner: Netzwerkordner: Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch.';
    const state = await setup(page, { put: () => ({ status: 400, detail }) });
    await page.goto('/einstellungen');
    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill(UNC);
    const box = page.getByTestId('net-credentials-watch');
    await box.getByLabel('Benutzer der Freigabe').fill('scanner');
    await box.getByLabel('Passwort').fill(TEST_SMB_PASSWORD);
    await page.getByRole('button', { name: 'Speichern', exact: true }).first().click();
    await expect.poll(() => state.puts.length).toBe(1);
    await expect(page.getByRole('alert').filter({ hasText: 'Anmeldung fehlgeschlagen' })).toBeVisible();
    await expect(box.getByLabel('Passwort')).toHaveValue(TEST_SMB_PASSWORD);
  });

  test('Zielordner im Netzwerk: Zugangsdaten des Exports werden gesendet', async ({ page }) => {
    const exportUnc = '\\\\nas\\archiv\\belege';
    const state = await setup(page);
    await page.goto('/einstellungen');
    await page.getByLabel('Zielordner für verarbeitete Dokumente').fill(exportUnc);
    const box = page.getByTestId('net-credentials-export');
    await expect(box).toBeVisible();
    await box.getByLabel('Benutzer der Freigabe').fill('archiv');
    await box.getByLabel('Passwort').fill(TEST_SMB_PASSWORD);
    await page.getByRole('button', { name: 'Speichern', exact: true }).first().click();
    await expect.poll(() => state.puts.length).toBe(1);
    expect(state.puts[0]).toMatchObject({
      export_dir: exportUnc, export_dir_net_user: 'archiv', export_dir_net_password: TEST_SMB_PASSWORD,
      watch_dir_net_user: '', watch_dir_net_password: null,
    });
    await expect(box.getByLabel('Passwort')).toHaveAttribute('placeholder', 'gespeichert – leer lassen für unverändert');
  });

  test('Prüfintervall außerhalb 10–600 s: Hinweis statt stillem Verwerfen, kein Speichern', async ({ page }) => {
    const state = await setup(page);
    await page.goto('/einstellungen');
    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill(UNC);
    const box = page.getByTestId('net-credentials-watch');
    await box.getByLabel('Prüfintervall (Sekunden)').fill('5');
    await expect(page.getByTestId('interval-error')).toContainText('zwischen 10 und 600');
    await page.getByRole('button', { name: 'Speichern', exact: true }).first().click();
    await expect(page.getByRole('alert').filter({ hasText: 'Prüfintervall' })).toBeVisible();
    expect(state.puts).toHaveLength(0);
    await box.getByLabel('Prüfintervall (Sekunden)').fill('45');
    await expect(page.getByTestId('interval-error')).toHaveCount(0);
  });

  test('Verbindungstest: Rate-Limit-Meldung wird angezeigt', async ({ page }) => {
    await setup(page, {
      test: () => ({ status: 429, detail: 'Zu viele Verbindungstests. Bitte eine Minute warten und erneut versuchen.' }),
    });
    await page.goto('/einstellungen');
    await page.getByLabel('Eingangsordner (Watch-Ordner)').fill(UNC);
    await page.getByTestId('test-folder-watch').click();
    await expect(page.getByTestId('test-result-watch')).toContainText('Zu viele Verbindungstests');
  });

  test('Ordner prüfen: lokale Ordner und Ersatzlaufwerke (subst) zeigen das Ergebnis', async ({ page }) => {
    const state = await setup(page, {
      test: (body) => body.path.startsWith('S:')
        ? { ok: true, kind: 'subst', message: 'Ersatzlaufwerk S: zeigt auf C:\\Daten\\Scans – gespeichert wird dieser Pfad. Ordner ist gültig.', suggested_paths: [] }
        : { ok: true, kind: 'local', message: 'Ordner ist gültig und wird beim Speichern angelegt.', suggested_paths: [] },
    });
    await page.goto('/einstellungen');
    const watch = page.getByLabel('Eingangsordner (Watch-Ordner)');
    await watch.fill('D:\\Scans\\Eingang');
    await page.getByTestId('test-folder-watch').click();
    await expect(page.getByTestId('test-result-watch')).toContainText('wird beim Speichern angelegt');
    await expect(page.getByTestId('test-folder-watch')).toContainText('Ordner prüfen');
    await watch.fill('S:\\eingang');
    await page.getByTestId('test-folder-watch').click();
    await expect(page.getByTestId('test-result-watch')).toContainText('Ersatzlaufwerk S:');
    expect(state.tests.map((t) => t.path)).toEqual(['D:\\Scans\\Eingang', 'S:\\eingang']);
    expect(state.tests[0]).not.toHaveProperty('net_password');
  });

  test('Systemstatus: Zielordner nennt, wann er zuletzt geprüft wurde', async ({ page }) => {
    const health = {
      ...MOCK_SYSTEM_HEALTH,
      deployment_mode: 'native',
      components: {
        ...MOCK_SYSTEM_HEALTH.components,
        export_folder: {
          status: 'ok', message: 'erreichbar', mode: 'netzwerk', state: 'ok', path: '\\\\nas\\archiv',
          user: 'archiv', reason: null, last_ok_at: '2026-10-18T08:00:00+00:00', next_retry_at: null,
          poll_interval: null, checked_at: '2026-10-18T08:00:00+00:00',
        },
      },
    } satisfies SystemHealthResponse;
    await setup(page, { health });
    await page.goto('/einstellungen');
    await expect(page.getByTestId('folder-checked-export_folder')).toContainText('Zuletzt geprüft');
  });

  test('Systemstatus zeigt „nicht erreichbar: <Grund>“ für den Eingangsordner', async ({ page }) => {
    const health = {
      ...MOCK_SYSTEM_HEALTH,
      status: 'warning',
      deployment_mode: 'native',
      components: {
        ...MOCK_SYSTEM_HEALTH.components,
        watch_folder: {
          status: 'warning',
          message: 'nicht erreichbar: Netzwerkordner: Server nicht gefunden – Name/IP-Adresse prüfen, ist das Gerät eingeschaltet?',
          mode: 'netzwerk',
          state: 'getrennt',
          path: UNC,
          user: 'scanner',
          reason: 'Netzwerkordner: Server nicht gefunden – Name/IP-Adresse prüfen, ist das Gerät eingeschaltet?',
          last_ok_at: '2026-10-18T08:00:00+00:00',
          next_retry_at: '2026-10-18T08:05:00+00:00',
          poll_interval: 30,
        },
      },
    } satisfies SystemHealthResponse;
    await setup(page, { health });
    await page.goto('/einstellungen');
    const comp = page.getByTestId('component-watch_folder');
    await expect(comp).toContainText('Eingangsordner');
    await expect(comp).toContainText('nicht erreichbar: Netzwerkordner: Server nicht gefunden');
    await expect(page.getByTestId('folder-status-watch_folder')).toContainText(UNC);
    await expect(page.getByTestId('folder-status-watch_folder')).toContainText('Nächster Versuch');
  });
});
