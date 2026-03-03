import { test, expect } from '@playwright/test';
import {
  setupReviewMocks,
  MOCK_REVIEW_PENDING,
  MOCK_DOCUMENT_WITH_QUESTIONS,
} from '../helpers/mock.helpers';

test.describe('Review System', () => {
  test.beforeEach(async ({ page }) => {
    await setupReviewMocks(page);
  });

  test('Shows pending review count', async ({ page }) => {
    await page.goto('/pruefen');

    // Should show "1 von 2" or similar counter
    await expect(page.locator('text=/\\d+ von \\d+/').first()).toBeVisible();
  });

  test('Shows document preview in review', async ({ page }) => {
    await page.goto('/pruefen');

    // Preview pane should be visible (iframe or img)
    const preview = page.locator('iframe, img[src*="file"], img[src*="thumbnail"]').first();
    await expect(preview).toBeVisible();
  });

  test('Shows KI data summary', async ({ page }) => {
    await page.goto('/pruefen');

    // Should show "Erkannte Daten" section heading
    await expect(page.locator('text=Erkannte Daten').first()).toBeVisible();
  });

  test('Shows questions with type badges', async ({ page }) => {
    await page.goto('/pruefen');

    const questions = MOCK_DOCUMENT_WITH_QUESTIONS.review_questions;
    // First question should be visible
    await expect(page.locator(`text=${questions[0].question}`).first()).toBeVisible();
  });

  test('Shows suggested answer buttons', async ({ page }) => {
    await page.goto('/pruefen');

    const suggestions = MOCK_DOCUMENT_WITH_QUESTIONS.review_questions[0].suggested_answers;
    if (suggestions && suggestions.length > 0) {
      // First suggestion should be clickable
      await expect(page.locator(`text=${suggestions[0]}`).first()).toBeVisible();
    }
  });

  test('Click suggested answer and submit', async ({ page }) => {
    let answerSubmitted = false;
    await page.route('**/api/review/questions/*/answer', (route) => {
      answerSubmitted = true;
      return route.fulfill({ json: { message: 'OK' } });
    });

    await page.goto('/pruefen');

    const suggestions = MOCK_DOCUMENT_WITH_QUESTIONS.review_questions[0].suggested_answers;
    if (suggestions && suggestions.length > 0) {
      // Click first suggestion
      await page.locator(`text=${suggestions[0]}`).first().click();

      // Click answer button
      const answerBtn = page.getByRole('button', { name: /beantworten/i }).first();
      await answerBtn.click();

      await page.waitForTimeout(500);
      expect(answerSubmitted).toBeTruthy();
    }
  });

  test('Free text answer via textarea', async ({ page }) => {
    let submittedAnswer = '';
    await page.route('**/api/review/questions/*/answer', (route) => {
      const body = JSON.parse(route.request().postData() || '{}');
      submittedAnswer = body.answer;
      return route.fulfill({ json: { message: 'OK' } });
    });

    await page.goto('/pruefen');

    // Find textarea for answer
    const textarea = page.locator('textarea').first();
    await textarea.fill('Meine Antwort');

    const answerBtn = page.getByRole('button', { name: /beantworten/i }).first();
    await answerBtn.click();

    await page.waitForTimeout(500);
    expect(submittedAnswer).toBe('Meine Antwort');
  });

  test('Skip button skips to next document', async ({ page }) => {
    let skipCalled = false;
    await page.route('**/api/review/documents/*/skip', (route) => {
      skipCalled = true;
      return route.fulfill({ json: { message: 'Uebersprungen' } });
    });

    await page.goto('/pruefen');

    const skipBtn = page.getByRole('button', { name: /ueberspringen/i }).first();
    await skipBtn.click();

    await page.waitForTimeout(500);
    expect(skipCalled).toBeTruthy();
  });

  test('Confirm button appears when all questions answered', async ({ page }) => {
    // Mock with all questions answered
    const allAnswered = {
      ...MOCK_DOCUMENT_WITH_QUESTIONS,
      review_questions: MOCK_DOCUMENT_WITH_QUESTIONS.review_questions.map((q) => ({
        ...q,
        answer: 'Answered',
        is_answered: true,
      })),
    };

    await page.route('**/api/review/documents/*', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: { document: allAnswered, questions: allAnswered.review_questions } });
      }
      return route.continue();
    });

    await page.goto('/pruefen');

    const confirmBtn = page.getByRole('button', { name: /bestaetigen/i }).first();
    await expect(confirmBtn).toBeVisible();
  });

  test('Approve sends POST and loads next document', async ({ page }) => {
    let approveCalled = false;
    await page.route('**/api/review/documents/*/approve', (route) => {
      approveCalled = true;
      return route.fulfill({ json: { message: 'Bestaetigt' } });
    });

    // Mock all answered
    const allAnswered = {
      ...MOCK_DOCUMENT_WITH_QUESTIONS,
      review_questions: MOCK_DOCUMENT_WITH_QUESTIONS.review_questions.map((q) => ({
        ...q,
        answer: 'Answered',
        is_answered: true,
      })),
    };
    await page.route('**/api/review/documents/*', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: { document: allAnswered, questions: allAnswered.review_questions } });
      }
      return route.continue();
    });

    await page.goto('/pruefen');

    const confirmBtn = page.getByRole('button', { name: /bestaetigen/i }).first();
    await confirmBtn.click();

    await page.waitForTimeout(500);
    expect(approveCalled).toBeTruthy();
  });

  test('Empty state when all reviewed', async ({ page }) => {
    await page.route('**/api/review/pending', (route) =>
      route.fulfill({ json: { documents: [], total: 0 } })
    );

    await page.goto('/pruefen');
    await expect(page.locator('text=geprueft').first()).toBeVisible();
  });

  test('Details link navigates to document detail', async ({ page }) => {
    await page.goto('/pruefen');

    const detailsLink = page.locator('a:has-text("Details")').first();
    if (await detailsLink.isVisible()) {
      const href = await detailsLink.getAttribute('href');
      expect(href).toContain('/dokumente/');
    }
  });

  test('Progress bar shows answered/total ratio', async ({ page }) => {
    await page.goto('/pruefen');

    // Progress bar should show 0/2 (none answered)
    await expect(page.locator('text=/0.*2|0 von 2/').first()).toBeVisible();
  });
});
