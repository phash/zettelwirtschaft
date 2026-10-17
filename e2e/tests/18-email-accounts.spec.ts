import { test, expect, Page } from '@playwright/test';
import {
  setupSettingsMocks,
  setupEmailMocks,
  MOCK_EMAIL_ACCOUNTS,
  MOCK_EMAIL_HISTORY,
} from '../helpers/mock.helpers';

// E-Mail-Konten in den Einstellungen: Hinweise (nur ungelesene Mails,
// Zeitzone), Anlegen/Bearbeiten/Loeschen, Verbindungstest, Verlauf.
// Manueller Abruf als Hintergrund-Vorgang und "Neu nach Bearbeiten" stehen in
// 32-settings-regressions.spec.ts.

const ACCOUNT = MOCK_EMAIL_ACCOUNTS[0];

const emailCard = (page: Page) =>
  page.locator('.card').filter({ has: page.getByRole('heading', { name: 'E-Mail-Konten', exact: true }) });
const accountRow = (page: Page, name: string) =>
  emailCard(page).locator('.rounded-lg.border').filter({ hasText: name });

test.describe('E-Mail accounts', () => {
  let mocks: Awaited<ReturnType<typeof setupEmailMocks>>;

  test.beforeEach(async ({ page }) => {
    await setupSettingsMocks(page);
    // Eigener, frischer Zustand pro Test (setupSettingsMocks registriert bereits
    // E-Mail-Mocks; die zuletzt registrierten haben Vorrang).
    mocks = await setupEmailMocks(page);
  });

  test('Explains the import rules: unread mails only, CRON timezone', async ({ page }) => {
    await page.goto('/einstellungen');
    const rules = page.getByTestId('email-import-rules');
    await expect(rules).toContainText('Importiert werden nur ungelesene E-Mails');
    await expect(rules).toContainText('Pro Abruf höchstens 50 E-Mails.');
    await expect(rules).toContainText('Zeitpläne (CRON) gelten in der Zeitzone Europe/Berlin.');
  });

  test('Lists accounts with schedule, timezone and last fetch', async ({ page }) => {
    await page.goto('/einstellungen');
    const row = accountRow(page, ACCOUNT.name);
    await expect(row).toContainText('Aktiv');
    await expect(row).toContainText('Zeitgesteuert');
    await expect(row).toContainText(`${ACCOUNT.username} @ ${ACCOUNT.imap_host}:${ACCOUNT.imap_port}`);
    await expect(row).toContainText(`Zeitplan: ${ACCOUNT.cron_expression} (Europe/Berlin)`);
    await expect(row).toContainText('Letzter Abruf:');
  });

  test('Create account: validation first, then POST and the new account is listed', async ({ page }) => {
    await page.goto('/einstellungen');
    await emailCard(page).getByRole('button', { name: '+ Neues Konto' }).click();
    await expect(page.getByText('Neues E-Mail-Konto')).toBeVisible();

    // Leeres Formular wird clientseitig abgefangen
    await page.getByRole('button', { name: 'Konto hinzufügen' }).click();
    await expect(page.getByText('Bitte einen Anzeigenamen eingeben.')).toBeVisible();
    await expect(page.getByText('Bitte das Passwort eingeben.')).toBeVisible();
    expect(mocks.writes).toEqual([]);

    await page.getByLabel('Anzeigename').fill('  Familie  ');
    await page.getByLabel('IMAP-Server').fill('imap.familie.example');
    await page.getByLabel('Benutzername').fill('familie@example.org');
    await page.getByLabel('Passwort / App-Passwort').fill('geheim');
    await page.getByRole('button', { name: 'Konto hinzufügen' }).click();

    await expect.poll(() => mocks.writes.length).toBe(1);
    const write = mocks.writes[0];
    expect(write.method).toBe('POST');
    expect(write.body).toMatchObject({
      name: 'Familie',
      imap_host: 'imap.familie.example',
      username: 'familie@example.org',
      password: 'geheim',
      imap_port: 993,
      schedule_type: 'MANUAL',
      cron_expression: null,
    });
    expect(write.body).not.toHaveProperty('is_active');
    await expect(page.getByText('Konto hinzugefügt.')).toBeVisible();
    await expect(accountRow(page, 'Familie')).toContainText('Manuell');
    await expect(page.getByText('Neues E-Mail-Konto')).toHaveCount(0);
  });

  test('CRON schedule requires an expression and shows the timezone', async ({ page }) => {
    await page.goto('/einstellungen');
    await emailCard(page).getByRole('button', { name: '+ Neues Konto' }).click();
    await page.getByLabel('Anzeigename').fill('Zeitplan');
    await page.getByLabel('IMAP-Server').fill('imap.example.org');
    await page.getByLabel('Benutzername').fill('a@example.org');
    await page.getByLabel('Passwort / App-Passwort').fill('x');
    await page.getByRole('radio', { name: /Zeitgesteuert/ }).check();

    await expect(page.getByTestId('cron-help')).toContainText('Uhrzeiten gelten in der Zeitzone Europe/Berlin.');
    await page.getByRole('button', { name: 'Konto hinzufügen' }).click();
    await expect(page.getByText('Für den Zeitplan wird ein CRON-Ausdruck benötigt.')).toBeVisible();
    expect(mocks.writes).toEqual([]);

    await page.getByLabel('CRON-Ausdruck').fill('0 8 * * *');
    await page.getByRole('button', { name: 'Konto hinzufügen' }).click();
    await expect.poll(() => mocks.writes.length).toBe(1);
    expect(mocks.writes[0].body).toMatchObject({ schedule_type: 'CRON', cron_expression: '0 8 * * *' });
  });

  test('Server validation error is shown and the form stays open', async ({ page }) => {
    await page.route('**/api/email/accounts', (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      return route.fulfill({ status: 422, json: { detail: 'Ungültiger CRON-Ausdruck' } });
    });
    await page.goto('/einstellungen');
    await emailCard(page).getByRole('button', { name: '+ Neues Konto' }).click();
    await page.getByLabel('Anzeigename').fill('Kaputt');
    await page.getByLabel('IMAP-Server').fill('imap.example.org');
    await page.getByLabel('Benutzername').fill('a@example.org');
    await page.getByLabel('Passwort / App-Passwort').fill('x');
    await page.getByRole('button', { name: 'Konto hinzufügen' }).click();

    await expect(page.getByText('Ungültiger CRON-Ausdruck')).toBeVisible();
    await expect(page.getByText('Neues E-Mail-Konto')).toBeVisible();
  });

  test('Edit account sends PUT without an unchanged password', async ({ page }) => {
    await page.goto('/einstellungen');
    await accountRow(page, ACCOUNT.name).getByRole('button', { name: 'Bearbeiten' }).click();
    await expect(page.getByText('E-Mail-Konto bearbeiten')).toBeVisible();
    await expect(page.getByLabel('Anzeigename')).toHaveValue(ACCOUNT.name);

    await page.getByLabel('Anzeigename').fill('Rechnungen neu');
    await emailCard(page).getByRole('button', { name: 'Speichern', exact: true }).click();

    await expect.poll(() => mocks.writes.length).toBe(1);
    expect(mocks.writes[0]).toMatchObject({ method: 'PUT', path: `/api/email/accounts/${ACCOUNT.id}` });
    expect(mocks.writes[0].body).toMatchObject({ name: 'Rechnungen neu', is_active: true });
    expect(mocks.writes[0].body).not.toHaveProperty('password');
    await expect(page.getByText('Konto aktualisiert.')).toBeVisible();
    await expect(accountRow(page, 'Rechnungen neu')).toBeVisible();
  });

  test('Changing the server requires re-entering the password (SEC-DATA-02)', async ({ page }) => {
    await page.goto('/einstellungen');
    await accountRow(page, ACCOUNT.name).getByRole('button', { name: 'Bearbeiten' }).click();
    await page.getByLabel('IMAP-Server').fill('imap.anderer-anbieter.de');
    await emailCard(page).getByRole('button', { name: 'Speichern', exact: true }).click();

    await expect(page.getByText('Bei Änderung von Server, Port, SSL oder Benutzername bitte das Passwort erneut eingeben.')).toBeVisible();
    expect(mocks.writes).toEqual([]);

    await page.getByLabel('Neues Passwort (leer = unverändert)').fill('neues-passwort');
    await emailCard(page).getByRole('button', { name: 'Speichern', exact: true }).click();
    await expect.poll(() => mocks.writes.length).toBe(1);
    expect(mocks.writes[0].body).toMatchObject({ imap_host: 'imap.anderer-anbieter.de', password: 'neues-passwort' });
  });

  test('Delete account after confirmation', async ({ page }) => {
    await page.goto('/einstellungen');
    await accountRow(page, ACCOUNT.name).getByRole('button', { name: 'Löschen' }).click();

    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Bereits importierte Dokumente bleiben erhalten.');
    expect(mocks.writes).toEqual([]);
    await dialog.getByRole('button', { name: 'Löschen' }).click();

    await expect.poll(() => mocks.writes).toEqual([
      { method: 'DELETE', path: `/api/email/accounts/${ACCOUNT.id}`, body: null },
    ]);
    await expect(page.getByText('Konto gelöscht.')).toBeVisible();
    await expect(page.getByText('Keine E-Mail-Konten konfiguriert.', { exact: false })).toBeVisible();
  });

  test('Connection test reports success and failure', async ({ page }) => {
    let succeed = true;
    await page.route('**/api/email/accounts/*/test', (route) =>
      route.fulfill({ json: succeed ? { success: true } : { success: false, error: 'Anmeldung fehlgeschlagen' } })
    );
    await page.goto('/einstellungen');
    const testBtn = accountRow(page, ACCOUNT.name).getByRole('button', { name: 'Testen' });

    await testBtn.click();
    await expect(page.getByText(`Verbindung zu ${ACCOUNT.name} erfolgreich!`)).toBeVisible();

    succeed = false;
    await testBtn.click();
    await expect(page.getByText('Verbindung fehlgeschlagen: Anmeldung fehlgeschlagen')).toBeVisible();
  });

  test('History toggles open with processed mails and closes again', async ({ page }) => {
    await page.goto('/einstellungen');
    const row = accountRow(page, ACCOUNT.name);

    await row.getByRole('button', { name: 'Verlauf', exact: true }).click();
    for (const item of MOCK_EMAIL_HISTORY) {
      await expect(row).toContainText(item.subject!);
    }
    await expect(row).toContainText('Relevant');
    await expect(row).toContainText('Irrelevant');

    await row.getByRole('button', { name: 'Verlauf schließen' }).click();
    await expect(row).not.toContainText(MOCK_EMAIL_HISTORY[0].subject!);
  });

  test('Empty history shows a hint', async ({ page }) => {
    await page.route('**/api/email/accounts/*/history*', (route) => route.fulfill({ json: [] }));
    await page.goto('/einstellungen');
    await accountRow(page, ACCOUNT.name).getByRole('button', { name: 'Verlauf', exact: true }).click();
    await expect(page.getByText('Keine verarbeiteten E-Mails vorhanden.')).toBeVisible();
  });

  test('Account load error is reported', async ({ page }) => {
    await page.route('**/api/email/accounts', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } })
    );
    await page.goto('/einstellungen');
    await expect(page.getByText('E-Mail-Konten konnten nicht geladen werden.')).toBeVisible();
  });
});
