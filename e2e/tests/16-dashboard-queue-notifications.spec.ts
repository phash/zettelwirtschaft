import { test, expect, Page } from '@playwright/test';
import {
  setupDashboardMocks,
  setupNotificationMocks,
  MOCK_JOBS_ACTIVE,
  MOCK_JOBS_FAILED,
  MOCK_JOBS_EMPTY,
  MOCK_QUEUE_STATUS,
  MOCK_QUEUE_STATUS_PAUSED,
  MOCK_STATS,
  MOCK_EMAIL_STATS,
  MOCK_NOTIFICATIONS,
} from '../helpers/mock.helpers';
import type { JobStatusResponse, Paginated } from '../helpers/api-types';

// Dashboard: Verarbeitungs-Warteschlange (Pausieren/Fortsetzen, Wiederholen)
// und Benachrichtigungs-Glocke. Duplikat-/Verwerfen-Faelle stehen in
// 15-documents-upload-dashboard.spec.ts, das Oeffnen einer einzelnen
// Benachrichtigung in 20-app-shell.spec.ts.

/** Jobs je Status-Filter (?status=PROCESSING,PENDING bzw. FAILED) mocken. */
async function mockJobs(page: Page, jobs: { active?: Paginated<JobStatusResponse>; failed?: Paginated<JobStatusResponse> }) {
  await page.route('**/api/jobs?*', (route) => {
    const status = new URL(route.request().url()).searchParams.get('status') ?? '';
    const data = status === 'FAILED' ? jobs.failed : status.includes('PENDING') ? jobs.active : undefined;
    return route.fulfill({ json: data ?? MOCK_JOBS_EMPTY });
  });
}

test.describe('Dashboard queue', () => {
  test.beforeEach(async ({ page }) => {
    await setupDashboardMocks(page);
  });

  test('Stat cards include imported e-mails', async ({ page }) => {
    await page.goto('/');
    const cards = page.getByTestId('stat-cards');
    await expect(cards).toContainText(`${MOCK_STATS.total_documents}Gesamt Dokumente`);
    await expect(cards).toContainText(`${MOCK_EMAIL_STATS[0].relevant}E-Mails importiert`);
  });

  test('Active jobs are listed with their state', async ({ page }) => {
    await mockJobs(page, { active: MOCK_JOBS_ACTIVE });
    await page.goto('/');

    const queue = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'In Verarbeitung' }) });
    await expect(queue).toContainText('rechnung-2026.pdf');
    await expect(queue).toContainText('Wird verarbeitet');
    await expect(queue).toContainText('quittung-cafe.jpg');
    await expect(queue).toContainText('Wartet');
  });

  test('Running jobs are polled until the queue is empty', async ({ page }) => {
    await page.clock.install();
    let activePolls = 0;
    await page.route('**/api/jobs?*', (route) => {
      const status = new URL(route.request().url()).searchParams.get('status') ?? '';
      if (!status.includes('PENDING')) return route.fulfill({ json: MOCK_JOBS_EMPTY });
      activePolls += 1;
      return route.fulfill({ json: activePolls === 1 ? MOCK_JOBS_ACTIVE : MOCK_JOBS_EMPTY });
    });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'In Verarbeitung' })).toBeVisible();

    await page.clock.runFor(3500);
    await expect(page.getByRole('heading', { name: 'In Verarbeitung' })).toHaveCount(0);
    const polls = activePolls;
    await page.clock.runFor(30_000);
    expect(activePolls).toBe(polls);
  });

  test('Pause and resume the processing queue', async ({ page }) => {
    let paused = false;
    const calls: string[] = [];
    await mockJobs(page, { active: MOCK_JOBS_ACTIVE });
    await page.route('**/api/jobs/queue-status', (route) =>
      route.fulfill({ json: paused ? MOCK_QUEUE_STATUS_PAUSED : MOCK_QUEUE_STATUS })
    );
    await page.route('**/api/jobs/pause', (route) => {
      calls.push('pause');
      paused = true;
      return route.fulfill({ json: { paused: true, message: 'Queue pausiert' } });
    });
    await page.route('**/api/jobs/resume', (route) => {
      calls.push('resume');
      paused = false;
      return route.fulfill({ json: { paused: false, message: 'Queue fortgesetzt' } });
    });
    await page.goto('/');

    await page.getByRole('button', { name: 'Pausieren' }).click();
    await expect(page.getByText('Verarbeitung pausiert.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Fortsetzen' })).toBeVisible();

    await page.getByRole('button', { name: 'Fortsetzen' }).click();
    await expect(page.getByText('Verarbeitung fortgesetzt.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Pausieren' })).toBeVisible();
    expect(calls).toEqual(['pause', 'resume']);
  });

  test('Paused queue without jobs still offers "Fortsetzen"', async ({ page }) => {
    await page.route('**/api/jobs/queue-status', (route) => route.fulfill({ json: MOCK_QUEUE_STATUS_PAUSED }));
    await page.goto('/');
    await expect(page.getByText('Verarbeitung ist pausiert.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Fortsetzen' })).toBeVisible();
  });

  test('Failed queue toggle is reported', async ({ page }) => {
    await mockJobs(page, { active: MOCK_JOBS_ACTIVE });
    await page.route('**/api/jobs/pause', (route) => route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } }));
    await page.goto('/');
    await page.getByRole('button', { name: 'Pausieren' }).click();
    await expect(page.getByText('Queue-Status konnte nicht geändert werden.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Pausieren' })).toBeVisible();
  });

  test('Retry a failed job', async ({ page }) => {
    let failed = MOCK_JOBS_FAILED;
    await page.route('**/api/jobs?*', (route) => {
      const status = new URL(route.request().url()).searchParams.get('status');
      return route.fulfill({ json: status === 'FAILED' ? failed : MOCK_JOBS_EMPTY });
    });
    const retried: string[] = [];
    await page.route('**/api/jobs/*/retry', (route) => {
      retried.push(new URL(route.request().url()).pathname);
      failed = MOCK_JOBS_EMPTY;
      return route.fulfill({ json: { message: 'Job wird erneut verarbeitet', job_id: 'job-20' } });
    });
    await page.goto('/');

    const job = page.getByTestId('failed-job').filter({ hasText: 'corrupt-file.pdf' });
    await expect(job).toContainText('OCR fehlgeschlagen: Datei ist beschädigt');
    await job.getByRole('button', { name: 'Wiederholen' }).click();

    await expect.poll(() => retried).toEqual(['/api/jobs/job-20/retry']);
    await expect(page.getByText('„corrupt-file.pdf“ wird erneut verarbeitet.')).toBeVisible();
    await expect(page.getByTestId('failed-job')).toHaveCount(0);
  });

  test('Failed retry keeps the job and shows the server message', async ({ page }) => {
    await mockJobs(page, { failed: MOCK_JOBS_FAILED });
    await page.route('**/api/jobs/*/retry', (route) =>
      route.fulfill({ status: 409, json: { detail: 'Job wird bereits verarbeitet' } })
    );
    await page.goto('/');
    await page.getByTestId('failed-job').getByRole('button', { name: 'Wiederholen' }).click();
    await expect(page.getByText('Job wird bereits verarbeitet')).toBeVisible();
    await expect(page.getByTestId('failed-job')).toHaveCount(1);
  });

  test('Recent documents link to their detail page', async ({ page }) => {
    await page.goto('/');
    const recent = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Letzte Dokumente' }) });
    await expect(recent.getByRole('link', { name: /Stromrechnung Januar 2026/ })).toHaveAttribute('href', '/dokumente/doc-1');
  });
});

test.describe('Notifications', () => {
  test.beforeEach(async ({ page }) => {
    await setupDashboardMocks(page);
  });

  test('Bell shows the unread count and lists notifications', async ({ page }) => {
    await setupNotificationMocks(page);
    await page.goto('/');

    const bell = page.getByRole('button', { name: /Benachrichtigungen/ });
    await expect(bell).toContainText('1');
    await bell.click();
    const panel = page.locator('#header-notifications');
    for (const n of MOCK_NOTIFICATIONS) {
      await expect(panel).toContainText(n.title);
    }
  });

  test('"Alle gelesen" marks everything read and clears the badge', async ({ page }) => {
    await setupNotificationMocks(page);
    let readAll = 0;
    await page.route('**/api/notifications/read-all', (route) => {
      readAll += 1;
      return route.fallback();
    });
    await page.goto('/');

    await page.getByRole('button', { name: /Benachrichtigungen/ }).click();
    await page.getByRole('button', { name: 'Alle gelesen' }).click();

    await expect.poll(() => readAll).toBe(1);
    await expect(page.getByRole('button', { name: 'Alle gelesen' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Benachrichtigungen/ })).not.toContainText('ungelesen');
  });

  test('Empty notification list', async ({ page }) => {
    await setupNotificationMocks(page, []);
    await page.goto('/');
    await page.getByRole('button', { name: /Benachrichtigungen/ }).click();
    await expect(page.getByText('Keine Benachrichtigungen.')).toBeVisible();
  });

  test('Dropdown closes with Escape', async ({ page }) => {
    await setupNotificationMocks(page);
    await page.goto('/');
    const bell = page.getByRole('button', { name: /Benachrichtigungen/ });
    await bell.click();
    await expect(page.locator('#header-notifications')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#header-notifications')).toHaveCount(0);
    await expect(bell).toHaveAttribute('aria-expanded', 'false');
  });
});
