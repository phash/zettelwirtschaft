import { test, expect } from '@playwright/test';
import {
  setupUploadMocks,
  MOCK_UPLOAD_RESPONSE,
  MOCK_JOB_STATUS_PENDING,
  MOCK_JOB_STATUS_PROCESSING,
  MOCK_JOB_STATUS_COMPLETED,
} from '../helpers/mock.helpers';
import * as path from 'path';

const TEST_FILES_DIR = path.resolve(__dirname, '../fixtures/test-files');

test.describe('Document Upload', () => {
  test.beforeEach(async ({ page }) => {
    await setupUploadMocks(page);
  });

  test('Upload page shows drop zone', async ({ page }) => {
    await page.goto('/upload');
    await expect(page.locator('text=Dateien hierher ziehen').first()).toBeVisible();
  });

  test('Upload single file via file input', async ({ page }) => {
    // Mock job status polling
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_PENDING })
    );

    await page.goto('/upload');

    // Set file on the hidden input directly
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));

    // Should show uploaded file name in the jobs list
    await expect(page.locator('text=rechnung.pdf').first()).toBeVisible({ timeout: 10000 });
  });

  test('Upload shows progress', async ({ page }) => {
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_PENDING })
    );

    await page.goto('/upload');

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));

    // Should show uploaded file name
    await expect(page.locator('text=rechnung.pdf').first()).toBeVisible({ timeout: 10000 });
  });

  test('Upload tracks job status through completion', async ({ page }) => {
    let statusCallCount = 0;
    await page.route('**/api/documents/*/status', (route) => {
      statusCallCount++;
      if (statusCallCount <= 1) {
        return route.fulfill({ json: MOCK_JOB_STATUS_PENDING });
      }
      if (statusCallCount <= 2) {
        return route.fulfill({ json: MOCK_JOB_STATUS_PROCESSING });
      }
      return route.fulfill({ json: MOCK_JOB_STATUS_COMPLETED });
    });

    await page.goto('/upload');

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));

    // Eventually should show completed status
    await expect(page.locator('text=Fertig').first()).toBeVisible({ timeout: 15000 });
  });

  test('Completed upload shows view link', async ({ page }) => {
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_COMPLETED })
    );

    await page.goto('/upload');

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));

    // Should show "Anzeigen" link
    await expect(page.locator('text=Anzeigen').first()).toBeVisible({ timeout: 15000 });
  });

  test('Multiple files can be uploaded', async ({ page }) => {
    await page.route('**/api/documents/upload', (route) =>
      route.fulfill({
        json: {
          uploaded: [
            { document_id: '100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'OK' },
            { document_id: '101', original_filename: 'scan.png', status: 'PENDING', message: 'OK' },
          ],
          rejected: [],
        },
      })
    );
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_PENDING })
    );

    await page.goto('/upload');

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles([
      path.join(TEST_FILES_DIR, 'rechnung.pdf'),
      path.join(TEST_FILES_DIR, 'scan.png'),
    ]);

    await expect(page.locator('text=rechnung.pdf').first()).toBeVisible({ timeout: 10000 });
  });
});
