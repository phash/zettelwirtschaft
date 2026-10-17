import { test, expect } from '@playwright/test';
import {
  setupUploadMocks,
  MOCK_JOB_STATUS_PENDING,
  MOCK_JOB_STATUS_PROCESSING,
  MOCK_JOB_STATUS_COMPLETED,
} from '../helpers/mock.helpers';
import * as path from 'path';

const TEST_FILES_DIR = path.resolve(__dirname, '../fixtures/test-files');
const POLL_INTERVAL_MS = 3000;  // UploadView pollt den Job-Status alle 3 s

test.describe('Document Upload', () => {
  test.beforeEach(async ({ page }) => {
    await setupUploadMocks(page);
  });

  test('Upload page shows drop zone', async ({ page }) => {
    await page.goto('/upload');
    await expect(page.getByText('Dateien hierher ziehen')).toBeVisible();
  });

  test('Upload single file via file input', async ({ page }) => {
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_PENDING })
    );
    await page.goto('/upload');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));

    const row = page.getByTestId('upload-job');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('rechnung.pdf');
    await expect(row).toContainText('Wartet...');
    await expect(page.getByText('1 Datei(en) hochgeladen.')).toBeVisible();
  });

  test('Upload shows progress while the request is running', async ({ page }) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => { release = resolve; });
    await page.route('**/api/documents/upload', async (route) => {
      await gate;
      return route.fulfill({
        json: { uploaded: [{ job_id: 'job-100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'OK' }], rejected: [] },
      });
    });
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_PENDING })
    );
    await page.goto('/upload');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));
    await expect(page.getByText(/Wird hochgeladen\.\.\. \d+%/)).toBeVisible();

    release();
    await expect(page.getByText(/Wird hochgeladen/)).toHaveCount(0);
    await expect(page.getByTestId('upload-job')).toContainText('rechnung.pdf');
  });

  test('Upload tracks job status through completion and links the document', async ({ page }) => {
    await page.clock.install();
    const states = [MOCK_JOB_STATUS_PENDING, MOCK_JOB_STATUS_PROCESSING, MOCK_JOB_STATUS_COMPLETED];
    const polled: string[] = [];
    await page.route('**/api/documents/*/status', (route) => {
      polled.push(new URL(route.request().url()).pathname);
      return route.fulfill({ json: states[Math.min(polled.length - 1, states.length - 1)] });
    });
    await page.goto('/upload');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));
    const row = page.getByTestId('upload-job');
    await expect(row).toContainText('Wartet...');

    await page.clock.runFor(POLL_INTERVAL_MS);
    await page.clock.runFor(POLL_INTERVAL_MS);
    await expect(row).toContainText('Wird verarbeitet...');
    await page.clock.runFor(POLL_INTERVAL_MS);
    await expect(row).toContainText('Fertig');
    // Link auf die Dokument-ID aus dem Job-Status, nicht auf die Job-ID
    await expect(row.getByRole('link', { name: 'Anzeigen' })).toHaveAttribute('href', '/dokumente/doc-5');
    expect(new Set(polled)).toEqual(new Set(['/api/documents/job-100/status']));

    // Endzustand: kein weiteres Polling
    const count = polled.length;
    await page.clock.runFor(POLL_INTERVAL_MS * 3);
    expect(polled.length).toBe(count);
  });

  test('Vanished job (404) is shown as not found and polling stops', async ({ page }) => {
    await page.clock.install();
    let polls = 0;
    await page.route('**/api/documents/*/status', (route) => {
      polls += 1;
      return route.fulfill({ status: 404, json: { detail: 'Job nicht gefunden' } });
    });
    await page.goto('/upload');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'rechnung.pdf'));
    await page.clock.runFor(POLL_INTERVAL_MS);
    await expect(page.getByTestId('upload-job')).toContainText('Nicht gefunden');
    await page.clock.runFor(POLL_INTERVAL_MS * 3);
    expect(polls).toBe(1);
  });

  test('Multiple files are uploaded one request each', async ({ page }) => {
    const sent: string[] = [];
    await page.route('**/api/documents/upload', (route) => {
      const body = route.request().postDataBuffer()?.toString('latin1') || '';
      const name = /filename="([^"]+)"/.exec(body)?.[1] ?? '?';
      sent.push(name);
      return route.fulfill({
        json: { uploaded: [{ job_id: `job-${name}`, original_filename: name, status: 'PENDING', message: 'OK' }], rejected: [] },
      });
    });
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_PENDING })
    );
    await page.goto('/upload');

    await page.locator('input[type="file"]').setInputFiles([
      path.join(TEST_FILES_DIR, 'rechnung.pdf'),
      path.join(TEST_FILES_DIR, 'scan.png'),
    ]);

    await expect(page.getByTestId('upload-job')).toHaveCount(2);
    await expect(page.getByTestId('upload-job').filter({ hasText: 'scan.png' })).toBeVisible();
    await expect(page.getByTestId('upload-job').filter({ hasText: 'rechnung.pdf' })).toBeVisible();
    expect(sent).toEqual(['rechnung.pdf', 'scan.png']);
    await expect(page.getByText('2 Datei(en) hochgeladen.')).toBeVisible();
  });

  test('Rejected file gets its own error toast, accepted file is listed', async ({ page }) => {
    await page.route('**/api/documents/upload', (route) => {
      const body = route.request().postDataBuffer()?.toString('latin1') || '';
      if (body.includes('filename="invalid.exe"')) {
        return route.fulfill({ json: { uploaded: [], rejected: [{ filename: 'invalid.exe', error: 'Dateityp nicht erlaubt' }] } });
      }
      return route.fulfill({
        json: { uploaded: [{ job_id: 'job-100', original_filename: 'rechnung.pdf', status: 'PENDING', message: 'OK' }], rejected: [] },
      });
    });
    await page.route('**/api/documents/*/status', (route) =>
      route.fulfill({ json: MOCK_JOB_STATUS_PENDING })
    );
    await page.goto('/upload');

    await page.locator('input[type="file"]').setInputFiles([
      path.join(TEST_FILES_DIR, 'rechnung.pdf'),
      path.join(TEST_FILES_DIR, 'invalid.exe'),
    ]);

    await expect(page.getByText('invalid.exe: Dateityp nicht erlaubt')).toBeVisible();
    await expect(page.getByText('1 Datei(en) hochgeladen.')).toBeVisible();
    await expect(page.getByTestId('upload-job')).toHaveCount(1);
    await expect(page.getByTestId('upload-job')).toContainText('rechnung.pdf');
  });

  test('Only rejected files: no success toast and no file list', async ({ page }) => {
    await page.route('**/api/documents/upload', (route) =>
      route.fulfill({ json: { uploaded: [], rejected: [{ filename: 'invalid.exe', error: 'Dateityp nicht erlaubt' }] } })
    );
    await page.goto('/upload');

    await page.locator('input[type="file"]').setInputFiles(path.join(TEST_FILES_DIR, 'invalid.exe'));

    await expect(page.getByText('invalid.exe: Dateityp nicht erlaubt')).toBeVisible();
    await expect(page.getByText(/hochgeladen\./)).toHaveCount(0);
    await expect(page.getByTestId('upload-job')).toHaveCount(0);
  });
});
