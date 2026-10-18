/**
 * Zentrale Test-Zugangsdaten (PIN, Dummy-IMAP-/SMB-Passwoerter) fuer die E2E-Tests.
 *
 * Im Repo stehen bewusst KEINE Credential-Literale (Secret-Scanner wie
 * GitGuardian melden sonst "Generic Password"). Quelle der Werte, in dieser
 * Reihenfolge:
 *   1. Umgebungsvariable gleichen Namens (z.B. TEST_PIN),
 *   2. e2e/.env.test (gitignored, Vorlage: e2e/.env.test.example),
 *   3. sonst zufaellig erzeugt.
 *
 * Playwright-Worker sind eigene Prozesse: playwright.config.ts ruft
 * ensureTestCredentials() im Hauptprozess auf und schreibt fehlende Werte
 * nach process.env — die Worker erben sie, alle Tests sehen dieselben Werte.
 */
import { existsSync, readFileSync } from 'node:fs';
import { randomBytes, randomInt } from 'node:crypto';
import path from 'node:path';
import { parseEnv } from 'node:util';

export const ENV_TEST_FILE = path.resolve(__dirname, '../.env.test');

/** Installer-PINs sind 6-stellig; das Backend verlangt mindestens 4 Zeichen. */
const PIN_LENGTH = 6;
const MIN_PIN_LENGTH = 4;

const GENERATORS = {
  TEST_PIN: () => Array.from({ length: PIN_LENGTH }, () => randomInt(10)).join(''),
  TEST_IMAP_PASSWORD: () => randomBytes(18).toString('base64url'),
  TEST_IMAP_PASSWORD_2: () => randomBytes(18).toString('base64url'),
  TEST_SMB_PASSWORD: () => randomBytes(18).toString('base64url'),
} as const;

type CredentialName = keyof typeof GENERATORS;

/** Laedt e2e/.env.test (ohne vorhandene Umgebungswerte zu ueberschreiben) und
 *  erzeugt fehlende Werte. Idempotent — mehrfacher Aufruf aendert nichts. */
export function ensureTestCredentials(): void {
  const fileValues: Record<string, string | undefined> = existsSync(ENV_TEST_FILE)
    ? parseEnv(readFileSync(ENV_TEST_FILE, 'utf-8'))
    : {};
  for (const name of Object.keys(GENERATORS) as CredentialName[]) {
    if (process.env[name]?.trim()) continue;
    process.env[name] = fileValues[name]?.trim() || GENERATORS[name]();
  }
  validate();
}

function read(name: CredentialName): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} fehlt — ensureTestCredentials() nicht aufgerufen?`);
  return value;
}

function validate(): void {
  const source = `(Umgebung oder ${ENV_TEST_FILE})`;
  const pin = read('TEST_PIN');
  if (!/^\d+$/.test(pin) || pin.length < MIN_PIN_LENGTH) {
    throw new Error(`TEST_PIN muss aus mindestens ${MIN_PIN_LENGTH} Ziffern bestehen ${source}`);
  }
  if (read('TEST_IMAP_PASSWORD') === read('TEST_IMAP_PASSWORD_2')) {
    throw new Error(`TEST_IMAP_PASSWORD und TEST_IMAP_PASSWORD_2 muessen sich unterscheiden ${source}`);
  }
}

// Auch direkt beim Import: im Worker sind die Werte schon geerbt (no-op),
// so funktionieren die Helfer aber auch ohne Umweg ueber die Config.
ensureTestCredentials();

/** Richtiger PIN fuer die PIN-Login-Mocks. */
export const TEST_PIN = read('TEST_PIN');
/** Garantiert falscher PIN gleicher Laenge (jede Ziffer um 1 verschoben). */
export const TEST_WRONG_PIN = [...TEST_PIN].map((c) => String((Number(c) + 1) % 10)).join('');
/** Dummy-IMAP-Passwort (neues Konto). */
export const TEST_IMAP_PASSWORD = read('TEST_IMAP_PASSWORD');
/** Zweites, abweichendes Dummy-IMAP-Passwort (Passwortwechsel). */
export const TEST_IMAP_PASSWORD_2 = read('TEST_IMAP_PASSWORD_2');
/** Dummy-Passwort einer (gemockten) SMB-Freigabe fuer Netzwerkordner-Tests. */
export const TEST_SMB_PASSWORD = read('TEST_SMB_PASSWORD');
