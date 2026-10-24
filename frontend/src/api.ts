import type { UploadResponse } from './types';

let csrfToken: string | null = null;
export const setCsrfToken = (token: string | null) => { csrfToken = token; };

export class RequestError extends Error {
  constructor(message: string, public readonly status: number) { super(message); this.name = 'RequestError'; }
}

/** Only safe contract errors are shown. HTML/proxy failures never leak raw bodies. */
export function errorMessage(body: unknown, fallback = 'The request could not be completed. Please try again.'): string {
  if (!body || typeof body !== 'object' || !('detail' in body)) return fallback;
  const detail = body.detail;
  if (Array.isArray(detail)) return detail.map((entry: unknown) => {
    if (!entry || typeof entry !== 'object' || !('msg' in entry) || typeof entry.msg !== 'string') return '';
    const field = 'loc' in entry && Array.isArray(entry.loc) ? entry.loc.filter((s: unknown) => s !== 'body').join(' · ') : '';
    return `${field ? `${field}: ` : ''}${entry.msg}`;
  }).filter(Boolean).join('; ') || fallback;
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message;
  return fallback;
}

function ensurePath(path: string) {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('://')) throw new Error('Only local API paths are allowed.');
}

export async function api<T>(path: string, options: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  ensurePath(path);
  const method = options.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, headers, credentials: 'same-origin', cache: 'no-store', signal: options.signal, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new RequestError('Cannot reach the workspace server. Check your connection and that the API is running.', 0);
  }
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) window.dispatchEvent(new Event('touchline:session-expired'));
    throw new RequestError(errorMessage(body, response.status === 401 ? 'Your session has ended. Please sign in again.' : undefined), response.status);
  }
  if (body === null) throw new RequestError('The server returned an unexpected response.', response.status);
  return body as T;
}

export async function optional<T>(path: string, signal?: AbortSignal): Promise<T | null> {
  try { return await api<T>(path, { signal }); } catch (error) {
    if (error instanceof RequestError && error.status === 404) return null;
    throw error;
  }
}

export function uploadVideo(matchId: string, file: File, onProgress: (fraction: number) => void, signal: AbortSignal): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const abort = () => request.abort();
    if (signal.aborted) { reject(new DOMException('Upload cancelled', 'AbortError')); return; }
    signal.addEventListener('abort', abort, { once: true });
    const cleanup = () => signal.removeEventListener('abort', abort);
    request.open('POST', `/api/matches/${encodeURIComponent(matchId)}/upload`);
    request.withCredentials = true;
    if (csrfToken) request.setRequestHeader('X-CSRF-Token', csrfToken);
    request.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded / event.total); };
    request.onload = () => {
      cleanup();
      let body: unknown;
      try { body = JSON.parse(request.responseText); } catch { body = null; }
      if (request.status >= 200 && request.status < 300 && body) resolve(body as UploadResponse);
      else {
        if (request.status === 401) window.dispatchEvent(new Event('touchline:session-expired'));
        reject(new RequestError(errorMessage(body, 'Upload failed. Check the file and try again.'), request.status));
      }
    };
    request.onerror = () => { cleanup(); reject(new RequestError('Connection lost during upload. Refresh the match before retrying.', 0)); };
    request.onabort = () => { cleanup(); reject(new DOMException('Upload cancelled', 'AbortError')); };
    request.ontimeout = () => { cleanup(); reject(new RequestError('Upload timed out. Refresh the match before retrying.', 0)); };
    request.timeout = 30 * 60 * 1000;
    const body = new FormData(); body.append('file', file); request.send(body);
  });
}
