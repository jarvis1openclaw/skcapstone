/**
 * Session credential storage.
 *
 * Session credentials live in sessionStorage only, per the approved
 * boundary: no protected data in browser persistence beyond approved
 * session storage. localStorage is never touched. Raw tokens are never
 * written to logs, prompts, or URLs.
 */

const STORAGE_KEY = "sklegal.session.credential";

export interface SessionCredential {
  /** Opaque bearer credential issued by the deployment boundary. */
  token: string;
  /** ISO timestamp after which the credential must not be sent. */
  expiresAt: string;
}

export interface CredentialStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export class SessionCredentialStore {
  private readonly storage: CredentialStorage;

  constructor(storage?: CredentialStorage) {
    const resolved = storage ?? globalThis.sessionStorage;
    if (resolved === undefined || resolved === null) {
      throw new Error("approved session storage is unavailable");
    }
    this.storage = resolved;
  }

  load(): SessionCredential | null {
    const raw = this.storage.getItem(STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    try {
      const parsed = JSON.parse(raw) as SessionCredential;
      if (
        typeof parsed.token !== "string" ||
        typeof parsed.expiresAt !== "string"
      ) {
        this.clear();
        return null;
      }
      return parsed;
    } catch {
      this.clear();
      return null;
    }
  }

  /** Fail closed: an expired credential is treated as absent. */
  loadActive(now: Date = new Date()): SessionCredential | null {
    const credential = this.load();
    if (credential === null) {
      return null;
    }
    if (Date.parse(credential.expiresAt) <= now.getTime()) {
      this.clear();
      return null;
    }
    return credential;
  }

  save(credential: SessionCredential): void {
    this.storage.setItem(STORAGE_KEY, JSON.stringify(credential));
  }

  clear(): void {
    this.storage.removeItem(STORAGE_KEY);
  }
}
