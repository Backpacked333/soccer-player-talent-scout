import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import snapshot from '../demo-public/scoutroom-demo.json';
import PublicDemo, { DemoWorkspace } from './PublicDemo';
import { validateDemo } from './demo-types';

const data = validateDemo(snapshot);
const featured = data.players.find((p) => p.display_name === 'Demo player 07')!;
const navigate = (path: string) => { window.location.hash = path; };
beforeEach(() => { window.location.hash = '/'; vi.stubGlobal('scrollTo', vi.fn()); });
afterEach(() => vi.unstubAllGlobals());

describe('public fictional demo', () => {
  it('opens without authentication and has no upload or editing controls', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    render(<DemoWorkspace data={data} />);
    expect(screen.getByRole('heading', { name: 'Every player. More context.' })).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent('Read-only demonstration');
    expect(document.querySelector('input[type=file], input[type=password], form')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
  it('searches fictional profiles without network calls', async () => {
    navigate('/players'); render(<DemoWorkspace data={data} />);
    await userEvent.type(screen.getByRole('searchbox'), '07');
    expect(screen.getByRole('heading', { name: 'Demo player 07' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Demo player 12' })).not.toBeInTheDocument();
  });
  it('replays actual date-bounded evidence and links named relationships', async () => {
    navigate(`/players/${featured.id}`); render(<DemoWorkspace data={data} />);
    const history = data.histories[featured.id]; const dates = Object.keys(history).sort();
    const region = document.querySelector('.demo-history-stats')!;
    expect(region).toHaveTextContent(String(history[dates[2]].approved_event_count));
    await userEvent.click(screen.getByRole('button', { name: /After fixture 1/ }));
    expect(region).toHaveTextContent(String(history[dates[0]].approved_event_count));
    expect(document.querySelectorAll('.demo-history-list a')).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Demo player 12' })).toBeInTheDocument();
    expect(screen.getByText(/All three fixtures. These metrics do not change/)).toBeInTheDocument();
  });
  it('filters the event ledger and exposes no video input', async () => {
    navigate(`/matches/${data.fixtures[0].match.id}`); render(<DemoWorkspace data={data} />);
    expect(screen.getByText('No footage is included in this public demo.')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Action' }), 'duel');
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(rows).toHaveLength(5);
    expect(document.querySelector('input[type=file]')).toBeNull();
  });
  it('keeps missing pass evidence unranked and refuses self-comparison', async () => {
    navigate('/comparisons'); render(<DemoWorkspace data={data} />);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Appearance role' }), 'defender');
    expect(within(screen.getByRole('table')).getByRole('cell', { name: 'Unranked' })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Duel actor' }), featured.id);
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Duel opponent' }), featured.id);
    expect(screen.getByRole('status')).toHaveTextContent('Choose two different players');
  });
  it('loads only the public static snapshot with cookies omitted', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }); vi.stubGlobal('fetch', fetch);
    render(<PublicDemo />);
    await screen.findByRole('heading', { name: 'Every player. More context.' });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith('/scoutroom-demo.json', expect.objectContaining({ credentials: 'omit' }));
  });
  it('fails closed on a non-fictional or failed response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...snapshot, is_synthetic: false }) }));
    render(<PublicDemo />);
    expect(await screen.findByRole('alert')).toHaveTextContent('only accepts its fictional');
  });
  it('supports navigation and closes the mobile menu with Escape', async () => {
    render(<DemoWorkspace data={data} />);
    await userEvent.click(screen.getByRole('button', { name: 'Open navigation' }));
    expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveAttribute('aria-expanded', 'true');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('button', { name: 'Open navigation' })).toHaveAttribute('aria-expanded', 'false');
    navigate('/about'); fireEvent(window, new HashChangeEvent('hashchange'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'A demonstration. Not a prediction.' })).toBeInTheDocument());
  });
  it('rejects real records nested in a public-shaped snapshot', () => {
    const mixed = structuredClone(snapshot); mixed.fixtures[0].events[0].is_synthetic = false;
    expect(() => validateDemo(mixed)).toThrow('fictional');
  });
});
