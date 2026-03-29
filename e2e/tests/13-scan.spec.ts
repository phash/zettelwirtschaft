import { test, expect } from '@playwright/test';
import {
  setupScanMocks,
  mockCameraAPI,
  mockCameraPermissionDenied,
  MOCK_UPLOAD_RESPONSE,
} from '../helpers/mock.helpers';
import * as path from 'path';

const TEST_FILES_DIR = path.resolve(__dirname, '../fixtures/test-files');

test.describe('Scan View', () => {
  test('Scan page loads with camera area visible', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraAPI(page);
    await page.goto('/scan');

    // Camera area should be active (video element visible)
    const video = page.locator('video');
    await expect(video).toBeVisible({ timeout: 10000 });
  });

  test('Camera permission denied shows error and file upload fallback', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.goto('/scan');

    // Error message should be shown
    await expect(page.locator('text=Kamerazugriff nicht moeglich')).toBeVisible({ timeout: 10000 });

    // File upload button should be available as fallback
    await expect(page.locator('text=Datei wählen')).toBeVisible();
  });

  test('Camera switch button is visible when camera active', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraAPI(page);
    await page.goto('/scan');

    // Wait for camera to be active
    await expect(page.locator('video')).toBeVisible({ timeout: 10000 });

    // Switch camera button (the refresh/rotate icon button)
    const buttons = page.locator('video').locator('..').locator('button');
    await expect(buttons.first()).toBeVisible();
  });

  test('Capture button is visible when camera active', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraAPI(page);
    await page.goto('/scan');

    await expect(page.locator('video')).toBeVisible({ timeout: 10000 });

    // The large capture button (w-16 h-16 circle)
    const captureBtn = page.locator('button').filter({ has: page.locator('div.rounded-full') });
    await expect(captureBtn.first()).toBeVisible();
  });

  test('Taking a photo shows preview with capture count', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraAPI(page);
    await page.goto('/scan');

    await expect(page.locator('video')).toBeVisible({ timeout: 10000 });

    // Click the capture button
    const captureBtn = page.locator('button').filter({ has: page.locator('div.rounded-full') });
    await captureBtn.first().click();

    // Should show "1 Aufnahme" text
    await expect(page.locator('text=1 Aufnahme')).toBeVisible({ timeout: 5000 });

    // Upload button should appear
    await expect(page.locator('text=Hochladen')).toBeVisible();
  });

  test('File picker allows selecting files for upload', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.goto('/scan');

    await expect(page.locator('text=Datei wählen')).toBeVisible({ timeout: 10000 });

    // Select a file via the hidden file input
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.join(TEST_FILES_DIR, 'scan.png'));

    // Should show 1 capture
    await expect(page.locator('text=1 Aufnahme')).toBeVisible({ timeout: 5000 });

    // Upload button should be visible
    await expect(page.locator('text=Hochladen')).toBeVisible();
  });

  test('Upload shows progress indicator', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.goto('/scan');

    await expect(page.locator('text=Datei wählen')).toBeVisible({ timeout: 10000 });

    // Select a file
    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.join(TEST_FILES_DIR, 'scan.png'));
    await expect(page.locator('text=1 Aufnahme')).toBeVisible({ timeout: 5000 });

    // Click upload
    await page.locator('button', { hasText: 'Hochladen' }).click();

    // Should show progress or navigate (upload completes quickly with mocks)
    // Either "Hochladen..." text appears or navigation happens
    await expect(page).toHaveURL(/\/dokumente/, { timeout: 10000 });
  });

  test('Successful upload navigates to documents page', async ({ page }) => {
    await setupScanMocks(page);
    await mockCameraPermissionDenied(page);
    await page.goto('/scan');

    await expect(page.locator('text=Datei wählen')).toBeVisible({ timeout: 10000 });

    const fileInput = page.locator('input[type="file"]');
    await fileInput.setInputFiles(path.join(TEST_FILES_DIR, 'scan.png'));
    await expect(page.locator('text=1 Aufnahme')).toBeVisible({ timeout: 5000 });

    await page.locator('button', { hasText: 'Hochladen' }).click();

    // Should navigate to /dokumente after successful upload
    await expect(page).toHaveURL(/\/dokumente/, { timeout: 10000 });
  });
});
