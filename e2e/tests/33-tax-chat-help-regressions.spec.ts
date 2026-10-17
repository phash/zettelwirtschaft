import { test, expect, Page } from '@playwright/test';
import { setupBaseMocks, setupDashboardMocks, showOnboardingTour } from '../helpers/mock.helpers';

// Regressionstests Steuer (Fremdwährung, doppeltes Laden, Singular),
// Garantie-Badges, Chat (Löschen nur mit Bestätigung, Abbruch-Hinweis,
// ältere Nachrichten), Hilfe-Texte und Onboarding-Tastatur/Fokus.

async function setupTax(page: Page, summary: Record<string, unknown>, validation: Record<string, unknown>) {
  await setupBaseMocks(page);
  const calls = { summary: 0, validate: 0 };
  // Aktuelles Jahr fehlt bewusst -> loadYears() korrigiert selectedYear.
  await page.route('**/api/tax/years', (route) => route.fulfill({ json: { years: [2025, 2024] } }));
  await page.route('**/api/tax/summary/*', (route) => {
    calls.summary += 1;
    return route.fulfill({ json: summary });
  });
  await page.route('**/api/tax/validate/*', (route) => {
    calls.validate += 1;
    return route.fulfill({ json: validation });
  });
  return calls;
}

const SUMMARY = {
  year: 2025,
  total_documents: 3,
  total_amount: 200,
  categories: [{ category: 'Werbungskosten', label: 'Werbungskosten', document_count: 1, total_amount: 200 }],
  warnings: [],
  non_eur_documents: [
    { id: 'doc-usd', title: 'Software-Lizenz USA', amount: 99, currency: 'USD', document_date: '2025-04-01' },
  ],
};

test.describe('Tax regressions', () => {
  test('non-EUR documents are listed and excluded from the EUR total', async ({ page }) => {
    await setupTax(page, SUMMARY, { year: 2025, total_documents: 3, warnings: ['Dokument "X" hat keinen Betrag'], ready: true });
    await page.goto('/steuer');

    const warning = page.getByTestId('tax-non-eur-warning');
    await expect(warning).toContainText('1 Beleg in Fremdwährung ist nicht im Gesamtbetrag enthalten');
    await expect(warning.getByRole('link', { name: 'Software-Lizenz USA' })).toHaveAttribute('href', '/dokumente/doc-usd');
    await expect(warning).toContainText('99,00 $');
    await expect(page.getByText('Gesamtbetrag (nur EUR)')).toBeVisible();
    // Singular statt "1 Hinweise".
    await expect(page.getByText('1 Hinweis vor dem Export')).toBeVisible();
  });

  test('summary and validation load once on open, even when the year is corrected', async ({ page }) => {
    await page.clock.install();
    const calls = await setupTax(page, { ...SUMMARY, non_eur_documents: [] }, { year: 2025, total_documents: 3, warnings: [], ready: true });
    await page.goto('/steuer');
    await expect(page.getByRole('heading', { name: 'Werbungskosten' })).toBeVisible();
    // Alle Timer ablaufen lassen: kein zweiter Lade-Durchgang.
    await page.clock.runFor(2_000);
    expect(calls).toEqual({ summary: 1, validate: 1 });
    await expect(page.getByText('Gesamtbetrag (nur EUR)')).toHaveCount(0);
  });
});

test.describe('Warranty regressions', () => {
  test('badge uses singular and "öffnen" in the accessible name', async ({ page }) => {
    await setupBaseMocks(page);
    const w = {
      id: 'w1', document_id: 'doc-1', document_title: 'Kaufbeleg', product_name: 'Toaster',
      purchase_date: '2024-01-01', warranty_end_date: '2026-10-18', warranty_duration_months: 24,
      warranty_type: 'LEGAL', retailer: null, days_remaining: 1, is_expired: false,
    };
    await page.route('**/api/warranties?*', (route) => route.fulfill({ json: [w] }));
    await page.route('**/api/warranties', (route) => route.fulfill({ json: [w] }));
    await page.route('**/api/warranties/stats', (route) =>
      route.fulfill({ json: { total: 1, active: 1, expiring_soon: 1, expired: 0 } })
    );
    await page.goto('/garantien');
    await expect(page.getByText('1 Tag', { exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Garantie Toaster öffnen' })).toBeVisible();
  });
});

function message(id: string, role: string, content: string) {
  return { id, role, content, sources: [], filing_scope_id: null, created_at: '2026-03-01T10:00:00Z' };
}

test.describe('Chat regressions', () => {
  test('clearing the history requires confirmation', async ({ page }) => {
    await setupBaseMocks(page);
    let deletes = 0;
    await page.route('**/api/chat/history*', (route) => {
      if (route.request().method() === 'DELETE') {
        deletes += 1;
        return route.fulfill({ json: { message: 'Chatverlauf gelöscht' } });
      }
      return route.fulfill({ json: { messages: [message('m1', 'user', 'Hallo?')], total: 1 } });
    });
    await page.goto('/assistent');

    await page.getByRole('button', { name: 'Verlauf löschen' }).click();
    expect(deletes).toBe(0);
    await page.getByRole('button', { name: 'Abbrechen' }).click();
    await expect(page.getByText('Hallo?')).toBeVisible();

    await page.getByRole('button', { name: 'Verlauf löschen' }).click();
    await page.getByRole('button', { name: 'Löschen', exact: true }).click();
    await expect.poll(() => deletes).toBe(1);
  });

  test('cancelled question explains that the answer may still arrive and offers a reload', async ({ page }) => {
    await setupBaseMocks(page);
    let historyLoads = 0;
    await page.route('**/api/chat/history*', (route) => {
      historyLoads += 1;
      return route.fulfill({ json: { messages: [], total: 0 } });
    });
    // Antwort bleibt aus, bis der Nutzer abbricht.
    await page.route('**/api/chat', () => new Promise(() => {}));
    await page.goto('/assistent');

    await page.getByLabel('Frage an den Assistenten').fill('Wie viel Strom?');
    await page.getByRole('button', { name: 'Frage abschicken' }).click();
    await page.getByRole('button', { name: 'Anfrage abbrechen' }).click();

    await expect(page.getByText(/kann sie später im Verlauf erscheinen/)).toBeVisible();
    const before = historyLoads;
    await page.getByRole('button', { name: 'Verlauf neu laden' }).click();
    await expect.poll(() => historyLoads).toBe(before + 1);
  });

  test('older messages are loaded with offset', async ({ page }) => {
    await setupBaseMocks(page);
    const offsets: string[] = [];
    await page.route('**/api/chat/history*', (route) => {
      const url = new URL(route.request().url());
      const offset = url.searchParams.get('offset') ?? '0';
      offsets.push(offset);
      const messages = offset === '0'
        ? [message('new', 'assistant', 'Neueste Antwort')]
        : [message('old', 'user', 'Uralte Frage')];
      return route.fulfill({ json: { messages, total: 2 } });
    });
    await page.goto('/assistent');

    await expect(page.getByText('Neueste Antwort')).toBeVisible();
    await page.getByRole('button', { name: 'Ältere Nachrichten laden' }).click();
    await expect(page.getByText('Uralte Frage')).toBeVisible();
    expect(offsets).toEqual(['0', '1']);
    await expect(page.getByRole('button', { name: 'Ältere Nachrichten laden' })).toHaveCount(0);
  });
});

test.describe('Help & onboarding regressions', () => {
  test('help FAQ describes the real backup and tax features', async ({ page }) => {
    await setupBaseMocks(page);
    await page.goto('/hilfe');

    await page.getByText('Wie sichere ich meine Daten?').click();
    await expect(page.getByText(/„Vollbackup“ zusätzlich alle Originaldokumente/)).toBeVisible();
    await page.getByText('Wie exportiere ich Steuerbelege?').click();
    await expect(page.getByText(/Kategorien aus/)).toHaveCount(0);
    await page.getByText('Wie schütze ich den Zugang?').click();
    await expect(page.getByText(/standardmäßig per PIN geschützt/)).toBeVisible();
    await expect(page.getByText(/Dateien per Drag & Drop hinzufügen \(PDF, JPG, PNG, TIFF, BMP\)/)).toBeVisible();
  });

  test('ArrowRight on the last step does not dismiss the tour; app behind is inert', async ({ page }) => {
    await setupDashboardMocks(page);
    await showOnboardingTour(page);
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Willkommen bei Zettelwirtschaft' })).toBeVisible();
    await expect(page.locator('#app')).toHaveAttribute('inert', '');

    for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('heading', { name: 'Bereit loszulegen?' })).toBeVisible();
    const done = await page.evaluate(() => localStorage.getItem('zw_onboarding_v1_done'));
    expect(done).toBeNull();

    await page.keyboard.press('Escape');
    await expect(page.getByRole('heading', { name: 'Bereit loszulegen?' })).toHaveCount(0);
    await expect(page.locator('#app')).not.toHaveAttribute('inert', '');
  });
});
