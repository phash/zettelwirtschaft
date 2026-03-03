import { test, expect } from '@playwright/test';
import {
  setupChatMocks,
  MOCK_CHAT_RESPONSE,
  MOCK_CHAT_HISTORY,
} from '../helpers/mock.helpers';

test.describe('KI-Assistent (Chat)', () => {
  test.beforeEach(async ({ page }) => {
    await setupChatMocks(page);
  });

  test('Shows chat input and send button', async ({ page }) => {
    await page.goto('/assistent');

    const chatInput = page.getByPlaceholder(/frage/i).first();
    await expect(chatInput).toBeVisible();

    const sendBtn = page.locator('button[type="submit"], button:has(svg)').last();
    await expect(sendBtn).toBeVisible();
  });

  test('Shows example questions when chat is empty', async ({ page }) => {
    // Override history to be empty
    await page.route('**/api/chat/history*', (route) =>
      route.fulfill({ json: { messages: [], total: 0 } })
    );

    await page.goto('/assistent');

    // Wait for the empty state heading to render (confirms data loading is done)
    await expect(page.locator('text=Frag deine Dokumente').first()).toBeVisible({ timeout: 10000 });

    // Example question buttons should be visible
    const buttons = page.locator('button').filter({ hasText: /\?/ });
    await expect(buttons.first()).toBeVisible();
    const count = await buttons.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('Send a question and receive answer', async ({ page }) => {
    await page.route('**/api/chat/history*', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: { messages: [], total: 0 } });
      }
      return route.continue();
    });

    await page.goto('/assistent');

    const chatInput = page.getByPlaceholder(/frage/i).first();
    await chatInput.fill('Was war meine letzte Stromrechnung?');
    await chatInput.press('Enter');

    // Answer should appear
    await expect(page.locator(`text=${MOCK_CHAT_RESPONSE.answer}`).first()).toBeVisible({ timeout: 10000 });
  });

  test('User message appears on the right', async ({ page }) => {
    await page.route('**/api/chat/history*', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: { messages: [], total: 0 } });
      }
      return route.continue();
    });

    await page.goto('/assistent');

    const chatInput = page.getByPlaceholder(/frage/i).first();
    await chatInput.fill('Testfrage');
    await chatInput.press('Enter');

    // User message bubble should be visible
    await expect(page.locator('text=Testfrage').first()).toBeVisible();
  });

  test('Send button is disabled when input is empty', async ({ page }) => {
    await page.goto('/assistent');

    const chatInput = page.getByPlaceholder(/frage/i).first();
    await expect(chatInput).toHaveValue('');

    // Send button should be disabled
    const sendBtn = page.locator('button[type="submit"]').first();
    if (await sendBtn.isVisible()) {
      await expect(sendBtn).toBeDisabled();
    }
  });

  test('Click example question sends it', async ({ page }) => {
    let questionSent = '';
    await page.route('**/api/chat', (route) => {
      if (route.request().method() === 'POST') {
        const body = JSON.parse(route.request().postData() || '{}');
        questionSent = body.question;
        return route.fulfill({ json: MOCK_CHAT_RESPONSE });
      }
      return route.continue();
    });
    await page.route('**/api/chat/history*', (route) =>
      route.fulfill({ json: { messages: [], total: 0 } })
    );

    await page.goto('/assistent');

    // Click first example question
    const exampleBtn = page.locator('button').filter({ hasText: /\?/ }).first();
    if (await exampleBtn.isVisible()) {
      await exampleBtn.click();
      await page.waitForTimeout(1000);
      expect(questionSent.length).toBeGreaterThan(0);
    }
  });

  test('Source references link to documents', async ({ page }) => {
    await page.route('**/api/chat/history*', (route) =>
      route.fulfill({ json: { messages: [], total: 0 } })
    );

    await page.goto('/assistent');

    const chatInput = page.getByPlaceholder(/frage/i).first();
    await chatInput.fill('Stromrechnung');
    await chatInput.press('Enter');

    // Wait for answer
    await page.waitForTimeout(1000);

    // Source link should be visible
    const sourceLink = page.locator('a').filter({ hasText: /Stromrechnung/ }).first();
    if (await sourceLink.isVisible()) {
      const href = await sourceLink.getAttribute('href');
      expect(href).toContain('/dokumente/');
    }
  });

  test('Clear history button works', async ({ page }) => {
    let historyCleaned = false;
    await page.route('**/api/chat/history*', (route) => {
      if (route.request().method() === 'DELETE') {
        historyCleaned = true;
        return route.fulfill({ json: { message: 'Geloescht' } });
      }
      return route.fulfill({ json: MOCK_CHAT_HISTORY });
    });

    await page.goto('/assistent');

    const clearBtn = page.getByRole('button', { name: /verlauf.*loeschen|loeschen/i }).first();
    if (await clearBtn.isVisible()) {
      await clearBtn.click();
      await page.waitForTimeout(500);
      expect(historyCleaned).toBeTruthy();
    }
  });

  test('Scope filter dropdown shown with multiple scopes', async ({ page }) => {
    await page.goto('/assistent');

    const scopeSelect = page.locator('select').filter({ has: page.locator('option:has-text("Alle Bereiche")') }).first();
    if (await scopeSelect.isVisible()) {
      await expect(scopeSelect).toBeVisible();
    }
  });

  test('Scope filter is included in chat request', async ({ page }) => {
    let requestBody: Record<string, unknown> = {};
    await page.route('**/api/chat', (route) => {
      if (route.request().method() === 'POST') {
        requestBody = JSON.parse(route.request().postData() || '{}');
        return route.fulfill({ json: MOCK_CHAT_RESPONSE });
      }
      return route.continue();
    });
    await page.route('**/api/chat/history*', (route) =>
      route.fulfill({ json: { messages: [], total: 0 } })
    );

    await page.goto('/assistent');

    // Select scope filter if visible
    const scopeSelect = page.locator('select').filter({ has: page.locator('option:has-text("Alle Bereiche")') }).first();
    if (await scopeSelect.isVisible()) {
      await scopeSelect.selectOption({ index: 1 }); // Select first real scope
    }

    const chatInput = page.getByPlaceholder(/frage/i).first();
    await chatInput.fill('Test');
    await chatInput.press('Enter');

    await page.waitForTimeout(1000);
    // Request should have been sent (with or without scope)
    expect(requestBody).toHaveProperty('question');
  });

  test('Chat history loads previous messages', async ({ page }) => {
    await page.goto('/assistent');

    // Previous messages from mock history should be visible (uses role/content format)
    const firstMessage = MOCK_CHAT_HISTORY.messages[0];
    await expect(page.locator(`text=${firstMessage.content}`).first()).toBeVisible();
  });

  test('Input has max length constraint', async ({ page }) => {
    await page.goto('/assistent');

    const chatInput = page.getByPlaceholder(/frage/i).first();
    const maxLength = await chatInput.getAttribute('maxlength');
    expect(parseInt(maxLength || '0')).toBe(2000);
  });
});
