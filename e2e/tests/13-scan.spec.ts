import { test, expect } from '@playwright/test';
import {
  setupScanMocks,
  mockCameraAPI,
  mockCameraPermissionDenied,
} from '../helpers/mock.helpers';
import * as path from 'path';

// Abgelehnte Aufnahmen, MIME-Typ, fehlende Kamera und Stream-Aufraeumen stehen
// in 15-documents-upload-dashboard.spec.ts.

const TEST_FILES_DIR = path.resolve(__dirname, '../fixtures/test-files');

test.describe('Scan View', () => {
  test('Scan page loads with camera and its controls', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraAPI(page);
    await page.goto('/scan');

    await expect(page.locator('video')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Kamera wechseln' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Foto aufnehmen' })).toBeVisible();
  });

  test('Camera permission denied shows error and file upload fallback', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.goto('/scan');

    await expect(page.getByText('Kamerazugriff nicht möglich')).toBeVisible();
    await expect(page.getByText('Datei wählen')).toBeVisible();
    await expect(page.locator('video')).toBeHidden();
  });

  test('Taking a photo shows preview with capture count', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraAPI(page);
    await page.goto('/scan');

    await page.getByRole('button', { name: 'Foto aufnehmen' }).click();
    await expect(page.getByText('1 Aufnahme', { exact: true })).toBeVisible();
    await expect(page.getByRole('img', { name: 'Aufnahme 1' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Hochladen' })).toBeEnabled();

    await page.getByRole('button', { name: 'Foto aufnehmen' }).click();
    await expect(page.getByText('2 Aufnahmen', { exact: true })).toBeVisible();
  });

  test('A capture can be removed again', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraAPI(page);
    await page.goto('/scan');

    await page.getByRole('button', { name: 'Foto aufnehmen' }).click();
    await page.getByRole('button', { name: 'Aufnahme 1 entfernen' }).click();
    await expect(page.getByText('1 Aufnahme', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Hochladen' })).toHaveCount(0);
  });

  test('File picker allows selecting files for upload', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.goto('/scan');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'scan.png'));
    await expect(page.getByText('1 Aufnahme', { exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Hochladen' })).toBeVisible();
  });

  test('Unsupported file type is refused before upload', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    let uploads = 0;
    await page.route('**/api/documents/upload', (route) => {
      uploads += 1;
      return route.fallback();
    });
    await page.goto('/scan');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'invalid.exe'));
    await expect(page.getByText('invalid.exe: Dateityp wird nicht unterstützt.')).toBeVisible();
    await expect(page.getByText(/Aufnahme/)).toHaveCount(0);
    expect(uploads).toBe(0);
  });

  test('Upload shows a busy state while the request is running', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route('**/api/documents/upload', async (route) => {
      await gate;
      return route.fulfill({
        json: { uploaded: [{ job_id: 'job-1', original_filename: 'scan.png', status: 'PENDING', message: 'OK' }], rejected: [] },
      });
    });
    await page.goto('/scan');
    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'scan.png'));

    await page.getByRole('button', { name: 'Hochladen' }).click();
    const busy = page.getByRole('button', { name: /Hochladen\.\.\. \d+%/ });
    await expect(busy).toBeVisible();
    await expect(busy).toBeDisabled();

    release();
    await expect(page).toHaveURL(/\/dokumente/);
  });

  test('Successful upload navigates to documents page', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.goto('/scan');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'scan.png'));
    await page.getByRole('button', { name: 'Hochladen' }).click();

    await expect(page).toHaveURL(/\/dokumente/);
    await expect(page.getByText('1 Scan(s) hochgeladen.')).toBeVisible();
  });
});
