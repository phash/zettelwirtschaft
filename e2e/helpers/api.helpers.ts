import { APIRequestContext } from '@playwright/test';

const API_BASE = '/api';

/**
 * Direct API calls for test setup/teardown.
 * Used when tests need to prepare state before UI interactions.
 */
export class ApiHelper {
  constructor(private request: APIRequestContext) {}

  async getHealth() {
    const res = await this.request.get(`${API_BASE}/health`);
    return res.json();
  }

  async getStats() {
    const res = await this.request.get(`${API_BASE}/stats`);
    return res.json();
  }

  async getDocuments(params: Record<string, string> = {}) {
    const query = new URLSearchParams(params).toString();
    const res = await this.request.get(`${API_BASE}/documents?${query}`);
    return res.json();
  }

  async getDocument(id: number) {
    const res = await this.request.get(`${API_BASE}/documents/${id}`);
    return res.json();
  }

  async uploadFile(filePath: string) {
    const res = await this.request.post(`${API_BASE}/documents/upload`, {
      multipart: {
        files: {
          name: 'test.pdf',
          mimeType: 'application/pdf',
          buffer: Buffer.from('%PDF-1.4 test content'),
        },
      },
    });
    return res.json();
  }

  async getJobs(params: Record<string, string> = {}) {
    const query = new URLSearchParams(params).toString();
    const res = await this.request.get(`${API_BASE}/jobs?${query}`);
    return res.json();
  }

  async getFilingScopes() {
    const res = await this.request.get(`${API_BASE}/filing-scopes`);
    return res.json();
  }

  async getAuthStatus() {
    const res = await this.request.get(`${API_BASE}/auth/status`);
    return res.json();
  }
}
