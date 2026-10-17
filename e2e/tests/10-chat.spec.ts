import { test, expect, Page } from '@playwright/test';
import {
  setupChatMocks,
  MOCK_CHAT_RESPONSE,
  MOCK_CHAT_HISTORY,
  MOCK_CHAT_HISTORY_EMPTY,
} from '../helpers/mock.helpers';

const chatInput = (page: Page) => page.getByRole('textbox', { name: 'Frage an den Assistenten' });
const sendButton = (page: Page) => page.getByRole('button', { name: 'Frage abschicken' });

async function emptyHistory(page: Page) {
  await page.route('**/api/chat/history*', (route) => {
    if (route.request().method() === 'GET') return route.fulfill({ json: MOCK_CHAT_HISTORY_EMPTY });
    return route.fallback();
  });
}

/** Naechster POST /api/chat; liefert den Request-Body. */
async function nextQuestion(page: Page, action: () => Promise<unknown>) {
  const [req] = await Promise.all([
    page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/chat'),
    action(),
  ]);
  return req.postDataJSON() as { question: string; filing_scope_id?: string | null };
}

test.describe('KI-Assistent (Chat)', () => {
  test.beforeEach(async ({ page }) => {
    await setupChatMocks(page);
  });

  test('Shows chat input and disabled send button when empty', async ({ page }) => {
    await page.goto('/assistent');
    await expect(chatInput(page)).toBeVisible();
    await expect(chatInput(page)).toHaveValue('');
    await expect(sendButton(page)).toBeDisabled();

    await chatInput(page).fill('Frage');
    await expect(sendButton(page)).toBeEnabled();
  });

  test('Shows example questions when chat is empty', async ({ page }) => {
    await emptyHistory(page);
    await page.goto('/assistent');

    await expect(page.getByRole('heading', { name: 'Frag deine Dokumente' })).toBeVisible();
    await expect(page.getByRole('button', { name: /\?$/ })).toHaveCount(4);
  });

  test('Send a question and receive answer with source links', async ({ page }) => {
    await emptyHistory(page);
    await page.goto('/assistent');

    await chatInput(page).fill('Was war meine letzte Stromrechnung?');
    const body = await nextQuestion(page, () => chatInput(page).press('Enter'));
    expect(body.question).toBe('Was war meine letzte Stromrechnung?');

    // Frage erscheint sofort als eigene Nachricht, Eingabe wird geleert
    await expect(page.getByText('Was war meine letzte Stromrechnung?')).toBeVisible();
    await expect(chatInput(page)).toHaveValue('');
    await expect(page.getByText(MOCK_CHAT_RESPONSE.answer)).toBeVisible();

    const source = MOCK_CHAT_RESPONSE.sources[0];
    await expect(page.getByRole('link', { name: `1. ${source.title}` })).toHaveAttribute('href', `/dokumente/${source.document_id}`);
  });

  test('Click example question sends it', async ({ page }) => {
    await emptyHistory(page);
    await page.goto('/assistent');

    const example = 'Wann läuft die Garantie für die Waschmaschine ab?';
    const body = await nextQuestion(page, () => page.getByRole('button', { name: example }).click());
    expect(body.question).toBe(example);
    await expect(page.getByText(MOCK_CHAT_RESPONSE.answer)).toBeVisible();
  });

  test('Scope filter is included in chat request', async ({ page }) => {
    await emptyHistory(page);
    await page.goto('/assistent');

    const scopeSelect = page.getByLabel('Ablagebereich für die Frage');
    await expect(scopeSelect.locator('option', { hasText: 'Alle Bereiche' })).toHaveCount(1);

    await chatInput(page).fill('Ohne Bereich');
    const all = await nextQuestion(page, () => chatInput(page).press('Enter'));
    expect(all.filing_scope_id ?? null).toBeNull();
    await expect(page.getByText(MOCK_CHAT_RESPONSE.answer)).toBeVisible();

    await scopeSelect.selectOption({ label: 'Praxis' });
    await chatInput(page).fill('Mit Bereich');
    const scoped = await nextQuestion(page, () => chatInput(page).press('Enter'));
    expect(scoped.filing_scope_id).toBe('scope-praxis');
  });

  test('Chat history loads previous messages', async ({ page }) => {
    await page.goto('/assistent');
    for (const msg of MOCK_CHAT_HISTORY.messages) {
      await expect(page.getByText(msg.content)).toBeVisible();
    }
  });

  test('Clear history asks for confirmation and empties the chat', async ({ page }) => {
    let deletes = 0;
    await page.route('**/api/chat/history*', (route) => {
      if (route.request().method() === 'DELETE') {
        deletes += 1;
        return route.fulfill({ json: { message: 'Gelöscht' } });
      }
      return route.fallback();
    });
    await page.goto('/assistent');

    await page.getByRole('button', { name: 'Verlauf löschen' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Chatverlauf löschen');
    expect(deletes).toBe(0);
    await dialog.getByRole('button', { name: 'Löschen' }).click();

    await expect(page.getByText('Chatverlauf gelöscht.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Frag deine Dokumente' })).toBeVisible();
    expect(deletes).toBe(1);
  });

  test('Running question can be cancelled', async ({ page }) => {
    await emptyHistory(page);
    // Antwort kommt nie — der Nutzer bricht ab.
    await page.route('**/api/chat', () => new Promise<void>(() => {}));
    await page.goto('/assistent');

    await chatInput(page).fill('Dauert lange');
    await chatInput(page).press('Enter');
    await expect(chatInput(page)).toBeDisabled();
    await page.getByRole('button', { name: 'Anfrage abbrechen' }).click();

    await expect(page.getByRole('alert').filter({ hasText: 'Anfrage abgebrochen.' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Verlauf neu laden' })).toBeVisible();
    await expect(chatInput(page)).toBeEnabled();
    await expect(page.getByRole('button', { name: 'Anfrage abbrechen' })).toHaveCount(0);
  });

  test('Unavailable assistant (503) shows the server message', async ({ page }) => {
    await emptyHistory(page);
    await page.route('**/api/chat', (route) =>
      route.fulfill({ status: 503, json: { detail: 'Vektor-Datenbank nicht erreichbar' } })
    );
    await page.goto('/assistent');

    await chatInput(page).fill('Frage');
    await chatInput(page).press('Enter');
    await expect(page.getByRole('alert').filter({ hasText: 'Vektor-Datenbank nicht erreichbar' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Verlauf neu laden' })).toHaveCount(0);
  });

  test('Input has max length constraint', async ({ page }) => {
    await page.goto('/assistent');
    await expect(chatInput(page)).toHaveAttribute('maxlength', '2000');
  });
});
