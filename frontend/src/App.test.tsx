import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { SYNTHETIC_NOTICE } from './types';
import { testSession } from './test-fixtures';

const fetchMock = vi.fn<typeof fetch>();
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => { window.location.hash = ''; vi.stubGlobal('fetch', fetchMock); vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined); });
afterEach(() => vi.unstubAllGlobals());

describe('authentication and explicit demonstration', () => {
  it('exposes labeled login controls and does not seed demo data on sign-in', async () => {
    const user = userEvent.setup();
    fetchMock.mockImplementation(async (url) => url === '/api/auth/me' ? json({}, 401) : url === '/api/auth/login' ? json(testSession) : json([]));
    render(<App />);
    await user.type(await screen.findByLabelText('Email address'), 'scout@example.test'); await user.type(screen.getByLabelText('Password'), 'test-only-password-123');
    await user.click(screen.getByRole('button', { name: 'Sign in to workspace' }));
    expect(await screen.findByRole('navigation', { name: 'Main navigation' })).toBeVisible();
    expect(screen.getByLabelText('Workspace data mode')).toHaveValue('real');
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/demo/seed'))).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({ body: JSON.stringify({ email: 'scout@example.test', password: 'test-only-password-123' }), credentials: 'same-origin' }));
  });
  it('creates an empty private workspace through the documented signup route', async () => {
    const user = userEvent.setup(); fetchMock.mockImplementation(async (url) => url === '/api/auth/me' ? json({}, 401) : url === '/api/auth/signup' ? json(testSession, 201) : json([]));
    render(<App />); await user.click(await screen.findByRole('button', { name: 'Create an account' }));
    await user.type(screen.getByLabelText('Workspace name'), 'Test workspace'); await user.type(screen.getByLabelText('Email address'), 'scout@example.test'); await user.type(screen.getByLabelText(/Password/), 'test-only-password-123');
    await user.click(screen.getByRole('button', { name: 'Create private workspace' }));
    expect(await screen.findByRole('navigation', { name: 'Main navigation' })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/signup', expect.objectContaining({ body: JSON.stringify({ email: 'scout@example.test', password: 'test-only-password-123', workspace_name: 'Test workspace' }) }));
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/demo/seed'))).toBe(false);
  });
  it('seeds only after an explicit click, then prominently labels synthetic mode', async () => {
    const user = userEvent.setup(); fetchMock.mockImplementation(async (url) => url === '/api/auth/me' ? json(testSession) : url === '/api/demo/seed' ? json({ players_created: 4, matches_created: 2, message: SYNTHETIC_NOTICE }) : json([]));
    render(<App />); const seed = await screen.findByRole('button', { name: 'Seed synthetic demo' });
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/demo/seed')).toBe(false); await user.click(seed);
    expect(await screen.findByText(SYNTHETIC_NOTICE)).toBeVisible(); expect(screen.getByLabelText('Workspace data mode')).toHaveValue('demo');
    expect(fetchMock).toHaveBeenCalledWith('/api/demo/seed', expect.objectContaining({ method: 'POST', headers: expect.objectContaining({ 'X-CSRF-Token': testSession.csrf_token }) }));
    await user.selectOptions(screen.getByLabelText('Workspace data mode'), 'real');
    await waitFor(() => expect(screen.queryByRole('note')).not.toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith('/api/players?mode=real', expect.anything());
  });
  it('shows seed errors without switching mode or inventing records', async () => {
    const user = userEvent.setup(); fetchMock.mockImplementation(async (url) => url === '/api/auth/me' ? json(testSession) : url === '/api/demo/seed' ? json({ detail: { code: 'busy', message: 'Database temporarily unavailable.' } }, 503) : json([]));
    render(<App />); await user.click(await screen.findByRole('button', { name: 'Seed synthetic demo' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Database temporarily unavailable.'); expect(screen.getByLabelText('Workspace data mode')).toHaveValue('real'); expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });
  it('shows API-unavailable errors instead of a fake dashboard or signed-out state', async () => {
    fetchMock.mockRejectedValue(new TypeError('network unavailable'));
    render(<App />); const alert = await screen.findByRole('alert'); expect(alert).toHaveTextContent('Cannot reach the workspace server'); expect(within(alert).getByRole('button', { name: 'Try again' })).toBeVisible(); expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });
});
