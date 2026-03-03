import { test, expect } from '@playwright/test';
import {
  setupDocumentDetailMocks,
  MOCK_DOCUMENT_DETAIL,
  MOCK_DOCUMENT_WITH_WARRANTY,
  MOCK_DOCUMENT_WITH_QUESTIONS,
  MOCK_FILING_SCOPES,
} from '../helpers/mock.helpers';

test.describe('Document Detail', () => {
  test.beforeEach(async ({ page }) => {
    await setupDocumentDetailMocks(page);
  });

  test('Shows document title and type badge', async ({ page }) => {
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    await expect(page.locator(`text=${MOCK_DOCUMENT_DETAIL.title}`).first()).toBeVisible();
  });

  test('Shows PDF preview', async ({ page }) => {
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    // PDF uses iframe with title="PDF-Vorschau" (file_type must be 'pdf')
    const preview = page.locator('iframe[title="PDF-Vorschau"]').first();
    await expect(preview).toBeVisible({ timeout: 10000 });
  });

  test('Metadata form shows issuer value', async ({ page }) => {
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    // Issuer is displayed via v-model, check with inputValue
    const issuerInput = page.locator('label:has-text("Aussteller") + input, label:has-text("Aussteller") ~ input').first();
    if (await issuerInput.isVisible()) {
      await expect(issuerInput).toHaveValue(MOCK_DOCUMENT_DETAIL.issuer);
    }
  });

  test('Edit and save metadata', async ({ page }) => {
    let patchCalled = false;
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() === 'PATCH') {
        patchCalled = true;
        const body = JSON.parse(route.request().postData() || '{}');
        return route.fulfill({ json: { ...MOCK_DOCUMENT_DETAIL, ...body } });
      }
      return route.fulfill({ json: MOCK_DOCUMENT_DETAIL });
    });

    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    // Modify the title field to trigger dirty state
    // Use 'main input' to skip the header search bar which is the first input on page
    const titleInput = page.locator('main input').first();
    await titleInput.fill('Geaenderte Stromrechnung');

    // Click save button
    const saveBtn = page.getByRole('button', { name: /speichern/i }).first();
    await saveBtn.click();

    await page.waitForTimeout(1000);
    expect(patchCalled).toBeTruthy();
  });

  test('Add tag to document', async ({ page }) => {
    let tagAdded = '';
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}/tags`, (route) => {
      if (route.request().method() === 'POST') {
        const body = JSON.parse(route.request().postData() || '{}');
        tagAdded = body.name;
        return route.fulfill({ json: { id: 99, name: body.name } });
      }
      return route.continue();
    });

    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    // Find the tag input
    const tagInput = page.getByPlaceholder(/tag/i).first();
    await tagInput.fill('Neuer-Tag');
    await tagInput.press('Enter');

    await page.waitForTimeout(500);
    expect(tagAdded).toBe('Neuer-Tag');
  });

  test('Remove tag from document', async ({ page }) => {
    let tagRemoved = false;
    await page.route('**/api/documents/*/tags/*', (route) => {
      if (route.request().method() === 'DELETE') {
        tagRemoved = true;
        return route.fulfill({ json: { message: 'Tag entfernt' } });
      }
      return route.continue();
    });

    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    // Find and click remove button on first tag
    const removeButtons = page.locator('[class*="tag"] button, .badge button, [aria-label*="entfernen"]');
    const count = await removeButtons.count();
    if (count > 0) {
      await removeButtons.first().click();
      await page.waitForTimeout(500);
      expect(tagRemoved).toBeTruthy();
    }
  });

  test('Delete document with confirmation', async ({ page }) => {
    let deleteRequested = false;
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() === 'DELETE') {
        deleteRequested = true;
        return route.fulfill({ json: { message: 'Geloescht' } });
      }
      return route.fulfill({ json: MOCK_DOCUMENT_DETAIL });
    });

    // Mock documents list for redirect
    await page.route('**/api/documents?*', (route) =>
      route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } })
    );
    await page.route('**/api/documents', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      return route.continue();
    });

    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    // Click delete button
    const deleteBtn = page.getByRole('button', { name: /loeschen/i }).first();
    await deleteBtn.click();

    // Confirm dialog should appear
    const confirmBtn = page.getByRole('button', { name: /loeschen/i }).nth(1);
    await confirmBtn.click();

    await page.waitForTimeout(500);
    expect(deleteRequested).toBeTruthy();
  });

  test('Download link is present', async ({ page }) => {
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);
    const downloadLink = page.locator('a[download], a:has-text("Herunterladen")').first();
    await expect(downloadLink).toBeVisible();
  });

  test('Back button navigates to documents list', async ({ page }) => {
    // Mock documents list
    await page.route('**/api/documents*', (route) => {
      if (route.request().method() === 'GET' && !route.request().url().includes(`/${MOCK_DOCUMENT_DETAIL.id}`)) {
        return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } });
      }
      return route.continue();
    });
    await page.route('**/api/tags', (route) =>
      route.fulfill({ json: [] })
    );

    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    const backBtn = page.locator('button, a').filter({ hasText: /zurueck|←/i }).first();
    if (await backBtn.isVisible()) {
      await backBtn.click();
      await expect(page).toHaveURL(/\/dokumente$/);
    }
  });

  test('Shows confidence bar', async ({ page }) => {
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    // Confidence section should be visible — look for "KI-Analyse" heading or percentage
    const analysisSection = page.locator('text=/KI-Analyse|Konfidenz|92/').first();
    await expect(analysisSection).toBeVisible({ timeout: 10000 });
  });

  test('Shows warranty info when present', async ({ page }) => {
    await setupDocumentDetailMocks(page, MOCK_DOCUMENT_WITH_WARRANTY);
    await page.goto(`/dokumente/${MOCK_DOCUMENT_WITH_WARRANTY.id}`);

    await expect(page.locator(`text=${MOCK_DOCUMENT_WITH_WARRANTY.warranty_info!.product_name}`).first()).toBeVisible();
  });

  test('Shows review questions when present', async ({ page }) => {
    await setupDocumentDetailMocks(page, MOCK_DOCUMENT_WITH_QUESTIONS);
    await page.goto(`/dokumente/${MOCK_DOCUMENT_WITH_QUESTIONS.id}`);

    const firstQuestion = MOCK_DOCUMENT_WITH_QUESTIONS.review_questions[0];
    await expect(page.locator(`text=${firstQuestion.question}`).first()).toBeVisible();
  });

  test('Filing scope dropdown shows options', async ({ page }) => {
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    const scopeSelect = page.locator('select').filter({ has: page.locator(`option:has-text("${MOCK_FILING_SCOPES[0].name}")`) }).first();
    if (await scopeSelect.isVisible()) {
      const options = await scopeSelect.locator('option').allTextContents();
      expect(options.some(o => o.includes('Privat'))).toBeTruthy();
    }
  });

  test('Tax category dropdown appears when tax relevant checked', async ({ page }) => {
    await page.goto(`/dokumente/${MOCK_DOCUMENT_DETAIL.id}`);

    // Tax relevant should be checked (mock data has tax_relevant: true)
    const taxToggle = page.locator('#taxToggle');
    if (await taxToggle.isVisible()) {
      await expect(taxToggle).toBeChecked();
      // Tax category dropdown should be visible
      await expect(page.locator('select').filter({ has: page.locator('option:has-text("Werbungskosten")') }).first()).toBeVisible();
    }
  });
});
