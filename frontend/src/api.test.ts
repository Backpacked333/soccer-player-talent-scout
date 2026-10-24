import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, errorMessage, optional, RequestError, setCsrfToken, uploadVideo } from './api';

const mockFetch = vi.fn<typeof fetch>();
beforeEach(() => { vi.stubGlobal('fetch', mockFetch); setCsrfToken(null); });
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); });

describe('private API client', () => {
  it('uses same-origin cookies and separate CSRF on writes, never URL credentials', async () => {
    setCsrfToken('csrf-test'); mockFetch.mockResolvedValue(new Response(JSON.stringify({ id: 'p' }), { status: 201 }));
    await api('/players', { method: 'POST', body: { display_name: 'Synthetic fixture' } });
    expect(mockFetch).toHaveBeenCalledWith('/api/players', expect.objectContaining({ credentials: 'same-origin', cache: 'no-store', method: 'POST', headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-test', 'Content-Type': 'application/json' }) }));
    expect(mockFetch.mock.calls[0][0]).not.toContain('csrf');
  });
  it('does not add CSRF or JSON content type to reads', async () => {
    setCsrfToken('csrf-test'); mockFetch.mockResolvedValue(new Response('[]'));
    await api('/matches?mode=real');
    expect(mockFetch.mock.calls[0][1]?.headers).toEqual({ Accept: 'application/json' });
  });
  it('supports real 204 mutations without fabricating a response', async () => {
    mockFetch.mockResolvedValue(new Response(null, { status: 204 })); expect(await api('/events/e', { method: 'DELETE' })).toBeUndefined();
  });
  it('rejects remote or scheme-relative API paths', async () => {
    await expect(api('https://external.invalid/collect')).rejects.toThrow('Only local'); await expect(api('//external.invalid')).rejects.toThrow('Only local'); expect(mockFetch).not.toHaveBeenCalled();
  });
  it('only treats 404 as absent optional data, never hides authorization errors', async () => {
    mockFetch.mockResolvedValue(new Response('{}', { status: 404 })); expect(await optional('/matches/m/upload')).toBeNull();
    mockFetch.mockResolvedValue(new Response('{}', { status: 403 })); await expect(optional('/matches/m/upload')).rejects.toBeInstanceOf(RequestError);
  });
  it('expires the UI session after an authenticated 401', async () => {
    const handler = vi.fn(); window.addEventListener('touchline:session-expired', handler);
    mockFetch.mockResolvedValue(new Response('{}', { status: 401 })); await expect(api('/players')).rejects.toThrow('session has ended'); expect(handler).toHaveBeenCalledOnce();
    window.removeEventListener('touchline:session-expired', handler);
  });
  it('surfaces safe validation messages and hides proxy HTML', async () => {
    expect(errorMessage({ detail: [{ loc: ['body', 'played_seconds'], msg: 'Must be nonnegative' }] })).toBe('played_seconds: Must be nonnegative');
    expect(errorMessage({ detail: { code: 'permission_required', message: 'Permission declaration required.' } })).toBe('Permission declaration required.');
    mockFetch.mockResolvedValue(new Response('<html>secret internal stack trace</html>', { status: 500 }));
    await expect(api('/players')).rejects.not.toThrow('secret');
  });
  it('preserves aborts for effect cleanup and shows a safe network failure', async () => {
    mockFetch.mockRejectedValue(new DOMException('Cancelled', 'AbortError')); await expect(api('/players')).rejects.toHaveProperty('name', 'AbortError');
    mockFetch.mockRejectedValue(new TypeError('private upstream host')); await expect(api('/players')).rejects.toThrow('Cannot reach the workspace server');
  });
  it('never starts a cancelled upload', async () => {
    const controller = new AbortController(); controller.abort();
    await expect(uploadVideo('m', new File(['x'], 'x.mp4', { type: 'video/mp4' }), vi.fn(), controller.signal)).rejects.toHaveProperty('name', 'AbortError');
  });
});
