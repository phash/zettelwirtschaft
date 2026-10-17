import { test, expect } from '@playwright/test';
import {
  setupDocumentDetailMocks,
  setupDocumentsMocks,
  MOCK_DOCUMENT_DETAIL,
  MOCK_DOCUMENT_WITH_WARRANTY,
  MOCK_DOCUMENT_WITH_QUESTIONS,
  MOCK_DOCUMENTS,
  MOCK_FILING_SCOPES,
} from '../helpers/mock.helpers';

// Weitere Detail-Faelle (Steuerjahr, geleerte Felder, 422, Leave-Guard, TIFF,
// 404) stehen in 15-documents-upload-dashboard.spec.ts.

const DOC_URL = `/dokumente/${MOCK_DOCUMENT_DETAIL.id}`;

test.describe('Document Detail', () => {
  test.beforeEach(async ({ page }) => {
    await setupDocumentDetailMocks(page);
  });

  test('Shows document title as heading', async ({ page }) => {
    await page.goto(DOC_URL);
    await expect(page.getByRole('heading', { level: 1, name: MOCK_DOCUMENT_DETAIL.title })).toBeVisible();
  });

  test('Shows PDF preview', async ({ page }) => {
    await page.goto(DOC_URL);
    await expect(page.locator('iframe[title="PDF-Vorschau"]')).toHaveAttribute('src', `/api/documents/${MOCK_DOCUMENT_DETAIL.id}/file`);
  });

  test('Metadata form is filled from the document', async ({ page }) => {
    await page.goto(DOC_URL);
    await expect(page.getByLabel('Titel')).toHaveValue(MOCK_DOCUMENT_DETAIL.title);
    await expect(page.getByLabel('Aussteller')).toHaveValue(MOCK_DOCUMENT_DETAIL.issuer!);
    await expect(page.getByLabel('Datum')).toHaveValue('2026-01-15');
    await expect(page.getByLabel('Referenznummer')).toHaveValue('RE-2026-001');
    await expect(page.getByLabel('Dokumenttyp')).toHaveValue('RECHNUNG');
  });

  test('Edit and save sends only the changed field', async ({ page }) => {
    const bodies: Record<string, unknown>[] = [];
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback();
      const body = route.request().postDataJSON() as Record<string, unknown>;
      bodies.push(body);
      return route.fulfill({ json: { ...MOCK_DOCUMENT_DETAIL, ...body } });
    });
    await page.goto(DOC_URL);

    await page.getByLabel('Titel').fill('Geänderte Stromrechnung');
    await page.getByRole('button', { name: 'Speichern' }).click();

    await expect.poll(() => bodies).toEqual([{ title: 'Geänderte Stromrechnung' }]);
    await expect(page.getByText('Änderungen gespeichert.')).toBeVisible();
    await expect(page.getByRole('heading', { level: 1, name: 'Geänderte Stromrechnung' })).toBeVisible();
  });

  test('Saving without changes sends nothing', async ({ page }) => {
    let patches = 0;
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() === 'PATCH') patches += 1;
      return route.fallback();
    });
    await page.goto(DOC_URL);
    await page.getByRole('button', { name: 'Speichern' }).click();
    await expect(page.getByText('Keine Änderungen.')).toBeVisible();
    expect(patches).toBe(0);
  });

  test('Add tag to document', async ({ page }) => {
    const [req] = await Promise.all([
      page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith(`/api/documents/${MOCK_DOCUMENT_DETAIL.id}/tags`)),
      (async () => {
        await page.goto(DOC_URL);
        const tagInput = page.getByLabel('Neuer Tag');
        await tagInput.fill('Neuer-Tag');
        await tagInput.press('Enter');
      })(),
    ]);
    expect(req.postDataJSON()).toEqual({ name: 'Neuer-Tag' });
    await expect(page.getByRole('button', { name: 'Tag „neuer-tag“ entfernen' })).toBeVisible();
    await expect(page.getByLabel('Neuer Tag')).toHaveValue('');
  });

  test('Remove tag from document', async ({ page }) => {
    const deleted: string[] = [];
    await page.route('**/api/documents/*/tags/*', (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      deleted.push(new URL(route.request().url()).pathname);
      return route.fulfill({ status: 204, body: '' });
    });
    await page.goto(DOC_URL);

    const tag = MOCK_DOCUMENT_DETAIL.tags[1];
    const removeBtn = page.getByRole('button', { name: `Tag „${tag.name}“ entfernen` });
    await expect(removeBtn).toBeVisible();
    await removeBtn.click();

    await expect.poll(() => deleted).toEqual([`/api/documents/${MOCK_DOCUMENT_DETAIL.id}/tags/${tag.id}`]);
    await expect(removeBtn).toHaveCount(0);
    await expect(page.getByRole('button', { name: `Tag „${MOCK_DOCUMENT_DETAIL.tags[0].name}“ entfernen` })).toBeVisible();
  });

  test('Delete document with confirmation returns to the list', async ({ page }) => {
    await setupDocumentsMocks(page);
    await setupDocumentDetailMocks(page);
    let deletes = 0;
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      deletes += 1;
      return route.fulfill({ status: 204, body: '' });
    });
    await page.goto(DOC_URL);

    await page.getByRole('button', { name: 'Löschen' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toContainText('Dokument löschen');
    expect(deletes).toBe(0);
    await dialog.getByRole('button', { name: 'Löschen' }).click();

    await expect(page).toHaveURL('/dokumente');
    expect(deletes).toBe(1);
    await expect(page.getByText('Dokument gelöscht.')).toBeVisible();
    await expect(page.getByRole('link', { name: `Dokument ${MOCK_DOCUMENTS.items[1].title} öffnen` })).toBeVisible();
  });

  test('Cancelled delete sends no request', async ({ page }) => {
    let deletes = 0;
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() === 'DELETE') deletes += 1;
      return route.fallback();
    });
    await page.goto(DOC_URL);
    await page.getByRole('button', { name: 'Löschen' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Abbrechen' }).click();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
    await expect(page).toHaveURL(DOC_URL);
    expect(deletes).toBe(0);
  });

  test('Failed delete keeps the document and shows the error', async ({ page }) => {
    await page.route(`**/api/documents/${MOCK_DOCUMENT_DETAIL.id}`, (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback();
      return route.fulfill({ status: 500, json: { detail: 'Interner Serverfehler' } });
    });
    await page.goto(DOC_URL);
    await page.getByRole('button', { name: 'Löschen' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Löschen' }).click();

    await expect(page.getByText(/Interner Serverfehler|Löschen fehlgeschlagen/)).toBeVisible();
    await expect(page).toHaveURL(DOC_URL);
  });

  test('Download link points to the file', async ({ page }) => {
    await page.goto(DOC_URL);
    const link = page.getByRole('main').getByRole('link', { name: 'Herunterladen' });
    await expect(link).toHaveAttribute('href', `/api/documents/${MOCK_DOCUMENT_DETAIL.id}/file`);
    await expect(link).toHaveAttribute('download', '');
  });

  test('Back button without history goes to the documents list', async ({ page }) => {
    await setupDocumentsMocks(page);
    await setupDocumentDetailMocks(page);
    await page.goto(DOC_URL);

    await page.getByRole('button', { name: 'Zurück' }).click();
    await expect(page).toHaveURL(/\/dokumente$/);
  });

  test('Shows AI confidence with percentage and bar width', async ({ page }) => {
    await page.goto(DOC_URL);
    const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'KI-Analyse' }) });
    await expect(card).toContainText('92%');
    await expect(card.locator('[style*="width"]')).toHaveAttribute('style', /width: 92%/);
    await expect(card).toContainText(MOCK_DOCUMENT_DETAIL.summary);
  });

  test('Shows warranty info when present', async ({ page }) => {
    await setupDocumentDetailMocks(page, MOCK_DOCUMENT_WITH_WARRANTY);
    await page.goto(`/dokumente/${MOCK_DOCUMENT_WITH_WARRANTY.id}`);

    const card = page.locator('.card').filter({ has: page.getByRole('heading', { name: 'Garantie' }) });
    await expect(card).toContainText(MOCK_DOCUMENT_WITH_WARRANTY.warranty_info.product_name);
    await expect(card).toContainText('10.1.2028');
    await expect(card).toContainText('Aktiv');
  });

  test('Shows review questions with answer inputs', async ({ page }) => {
    await setupDocumentDetailMocks(page, MOCK_DOCUMENT_WITH_QUESTIONS);
    await page.goto(`/dokumente/${MOCK_DOCUMENT_WITH_QUESTIONS.id}`);

    for (const q of MOCK_DOCUMENT_WITH_QUESTIONS.review_questions) {
      await expect(page.getByLabel(q.question)).toBeVisible();
    }
  });

  test('Filing scope dropdown lists all scopes with the current one selected', async ({ page }) => {
    await page.goto(DOC_URL);
    const scopeSelect = page.getByLabel('Ablagebereich');
    await expect(scopeSelect).toHaveValue(MOCK_DOCUMENT_DETAIL.filing_scope_id!);
    for (const scope of MOCK_FILING_SCOPES) {
      await expect(scopeSelect.locator('option', { hasText: scope.name })).toHaveCount(1);
    }
  });

  test('Tax category is shown for tax relevant documents and hidden otherwise', async ({ page }) => {
    await page.goto(DOC_URL);
    const taxToggle = page.locator('#taxToggle');
    await expect(taxToggle).toBeChecked();
    await expect(page.getByLabel('Steuerkategorie')).toHaveValue(MOCK_DOCUMENT_DETAIL.tax_category);

    await taxToggle.uncheck();
    await expect(page.getByLabel('Steuerkategorie')).toHaveCount(0);
  });
});
