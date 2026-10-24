import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, optional } from './api';
import { MatchReview } from './MatchReview';
import { testKnowledge, unavailableValue } from './intelligence-fixtures';
import type { AppearanceRead, EventRead, EventWrite, PlayerRead } from './types';
import { testAppearance, testEvent, testMatch, testPlayer } from './test-fixtures';

vi.mock('./api', () => ({ api: vi.fn(), optional: vi.fn(), uploadVideo: vi.fn() }));
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(optional).mockReset(); window.location.hash = '/matches/match-test'; });
afterEach(() => { vi.useRealTimers(); });

function setup({ real = false, initialEvents = [], roster = [testAppearance], players = [testPlayer] }: { real?: boolean; initialEvents?: EventRead[]; roster?: AppearanceRead[]; players?: PlayerRead[] } = {}) {
  let events = initialEvents;
  vi.mocked(api).mockImplementation(async (path, options) => {
    if (path.includes('/knowledge?')) return { ...testKnowledge, scope: { ...testKnowledge.scope, mode: real ? 'real' : 'demo' } };
    if (path.endsWith('/action-value')) return unavailableValue;
    if (path === '/matches/match-test' && !options?.method) return { ...testMatch, is_synthetic: !real };
    if (path === '/matches/match-test/roster') return roster.map((row) => ({ ...row, is_synthetic: !real }));
    if (path.startsWith('/players?')) return players.map((row) => ({ ...row, is_synthetic: !real }));
    if (path === '/matches/match-test/events' && options?.method === 'POST') { events = [...events, { ...testEvent, ...(options.body as EventWrite), is_synthetic: !real }]; return events[events.length - 1]; }
    if (path === '/matches/match-test/events') return events;
    if (path === '/events/event-test' && options?.method === 'DELETE') { events = []; return undefined; }
    if (path === '/events/event-test' && options?.method === 'PUT') { events = [{ ...testEvent, ...(options.body as EventWrite), is_synthetic: !real }]; return events[0]; }
    return [];
  });
  vi.mocked(optional).mockResolvedValue(null);
}

function footage({ permitted = true, expired = false } = {}) {
  const created = new Date(Date.now() - (expired ? 31 : 1) * 86400000).toISOString();
  vi.mocked(optional).mockImplementation(async (path) => path.endsWith('/upload') ? { id: 'upload-test', status: 'probed', duration_seconds: 300, width: 1280, height: 720, size_bytes: 1024, original_filename: 'test.mp4', created_at: created } : permitted ? { retention_days: 30 } : null);
}

describe('human review room', () => {
  it('submits only actual backend spatial semantics and never infers goal from success', async () => {
    setup(); const user = userEvent.setup(); render(<MatchReview id="match-test" mode="demo" />);
    await user.click(await screen.findByRole('button', { name: 'Add observation' }));
    await user.selectOptions(screen.getByLabelText('Actor · authorized roster'), 'player-test');
    await user.click(screen.getByText('Explicit contest, shot and spatial semantics'));
    await user.selectOptions(screen.getByLabelText('Pitch coordinate convention'), 'attack_normalized');
    await user.type(screen.getByLabelText('Coordinate provenance / calibration reference'), 'Fictional manual pitch marks');
    await user.selectOptions(screen.getByLabelText('Possession retained'), 'true');
    for (const label of ['Normalized start_x', 'Normalized start_y', 'Normalized end_x', 'Normalized end_y']) await user.type(screen.getByLabelText(label), '0.25');
    await user.click(screen.getByRole('button', { name: 'Save observation' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/matches/match-test/events', expect.objectContaining({ method: 'POST', body: expect.objectContaining({ coordinate_system: 'attack_normalized', coordinate_provenance: 'Fictional manual pitch marks', possession_retained: true, start_x: 0.25 }) })));
    const body = vi.mocked(api).mock.calls.find(([path, options]) => path === '/matches/match-test/events' && options?.method === 'POST')![1]!.body as EventWrite;
    expect(body.shot_outcome).toBeUndefined(); expect(body).not.toHaveProperty('coordinate_source'); expect(body).not.toHaveProperty('attack_direction');
  });
  it('requires actor and known outcome before approving and saves only editable fields', async () => {
    setup(); const user = userEvent.setup(); render(<MatchReview id="match-test" mode="demo" />);
    await user.click(await screen.findByRole('button', { name: 'Add observation' }));
    expect(screen.getByRole('button', { name: 'Save observation' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Actor · authorized roster'), 'player-test'); await user.selectOptions(screen.getByLabelText('Review decision'), 'approved');
    expect(screen.getByRole('button', { name: 'Save observation' })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('Observed outcome'), 'successful');
    const start = screen.getByLabelText(/Start timestamp/); await user.clear(start); await user.type(start, '65.5');
    await user.click(screen.getByRole('button', { name: 'Save observation' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/matches/match-test/events', { method: 'POST', body: { player_id: 'player-test', kind: 'pass', outcome: 'successful', timestamp_seconds: 65.5, end_timestamp_seconds: null, status: 'approved', opponent_player_id: null, start_x: null, start_y: null, end_x: null, end_y: null, notes: '' } }));
    expect(await screen.findByRole('button', { name: 'Edit pass at 1:05' })).toBeVisible(); expect(screen.getByText('Model confidence: unavailable')).toBeVisible(); expect(screen.getByText('Synthetic fixture · no real video')).toBeVisible();
  });
  it('edits and deletes observations through persistent routes, not local-only rows', async () => {
    setup({ initialEvents: [testEvent] }); const user = userEvent.setup(); vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<MatchReview id="match-test" mode="demo" />); await user.click(await screen.findByRole('button', { name: 'Edit pass at 1:05' }));
    await user.selectOptions(screen.getByLabelText('Observed outcome'), 'unsuccessful'); await user.click(screen.getByRole('button', { name: 'Save correction' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/events/event-test', expect.objectContaining({ method: 'PUT', body: expect.objectContaining({ outcome: 'unsuccessful' }) })));
    expect(await screen.findByText('Unsuccessful')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Delete pass at 1:05' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/events/event-test', { method: 'DELETE' }));
    expect(await screen.findByText('No observations in this view')).toBeVisible();
  });
  it('does not allow real event annotation with no probed footage', async () => {
    setup({ real: true }); render(<MatchReview id="match-test" mode="real" />);
    expect(await screen.findByRole('button', { name: 'Add observation' })).toBeDisabled(); expect(screen.getByText(/upload valid, probed footage before recording real evidence/)).toBeVisible();
  });
  it('loads private video without URL tokens, seeks evidence seconds and exposes media errors', async () => {
    setup({ real: true, initialEvents: [{ ...testEvent, is_synthetic: false, provenance: 'manual' }] });
    footage();
    window.location.hash = '/matches/match-test?t=65.5'; render(<MatchReview id="match-test" mode="real" />);
    const video = await screen.findByLabelText(/Private match video:/) as HTMLVideoElement;
    expect(video).toHaveAttribute('src', '/api/uploads/upload-test/video'); fireEvent.loadedMetadata(video); expect(video.currentTime).toBe(65.5);
    fireEvent.error(video); expect(await screen.findByRole('alert')).toHaveTextContent('private video could not be loaded');
  });
  it.each([{ permitted: false, expired: false }, { permitted: true, expired: true }])('blocks playback and annotation when permission is unavailable: %j', async (permissions) => {
    setup({ real: true, initialEvents: [{ ...testEvent, is_synthetic: false, provenance: 'manual' }] }); footage(permissions);
    render(<MatchReview id="match-test" mode="real" />);
    expect(await screen.findByRole('button', { name: 'Add observation' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Edit pass at 1:05' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Seek to 1:05' })).toBeDisabled();
    expect(screen.queryByLabelText(/Private match video:/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete pass at 1:05' })).toBeEnabled();
    expect(screen.getByText(permissions.expired ? /retention period has expired/i : /Confirm current footage permission/i)).toBeVisible();
  });
  it('removes playback and an open editor when retention expires on an idle page', async () => {
    vi.useFakeTimers(); setup({ real: true }); footage();
    await act(async () => { render(<MatchReview id="match-test" mode="real" />); });
    fireEvent.click(screen.getByRole('button', { name: 'Add observation' }));
    expect(screen.getByRole('button', { name: 'Save observation' })).toBeVisible();
    expect(screen.getByLabelText(/Private match video:/)).toBeVisible();
    await act(async () => { vi.advanceTimersByTime(29 * 86400000); });
    expect(screen.getByRole('button', { name: 'Add observation' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save observation' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Private match video:/)).not.toBeInTheDocument();
  });
  it('preserves source-time precision without a native form step mismatch', async () => {
    setup({ real: true }); footage(); const user = userEvent.setup();
    render(<MatchReview id="match-test" mode="real" />);
    const video = await screen.findByLabelText(/Private match video:/) as HTMLVideoElement;
    video.currentTime = 65.123456;
    await user.click(screen.getByRole('button', { name: 'Add observation' }));
    await user.selectOptions(screen.getByLabelText('Actor · authorized roster'), 'player-test');
    const start = screen.getByLabelText(/Start timestamp/) as HTMLInputElement;
    expect(start.valueAsNumber).toBe(65.123456);
    expect(start.validity.stepMismatch).toBe(false);
    video.currentTime = 66.123456;
    await user.click(screen.getByRole('button', { name: 'Use current video time' }));
    expect(start.valueAsNumber).toBe(66.123456);
    const end = screen.getByLabelText(/End timestamp/) as HTMLInputElement;
    fireEvent.change(end, { target: { value: '67.654321' } });
    expect(end.checkValidity()).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Save observation' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/matches/match-test/events', expect.objectContaining({ method: 'POST', body: expect.objectContaining({ timestamp_seconds: 66.123456, end_timestamp_seconds: 67.654321 }) })));
  });
  it('offers only opposing-side actors and clears the opponent when the actor changes', async () => {
    setup({
      roster: [testAppearance, { ...testAppearance, id: 'same', player_id: 'teammate' }, { ...testAppearance, id: 'other', player_id: 'opponent', side: 'away' }],
      players: [testPlayer, { ...testPlayer, id: 'teammate', display_name: 'Synthetic teammate' }, { ...testPlayer, id: 'opponent', display_name: 'Synthetic opponent' }],
    });
    const user = userEvent.setup(); render(<MatchReview id="match-test" mode="demo" />);
    await user.click(await screen.findByRole('button', { name: 'Add observation' }));
    const opponent = screen.getByLabelText('Opponent actor (optional)');
    expect(opponent).toBeDisabled();
    const actor = screen.getByLabelText('Actor · authorized roster');
    await user.selectOptions(actor, 'player-test');
    expect(within(opponent).queryByRole('option', { name: /Synthetic teammate/ })).not.toBeInTheDocument();
    expect(within(opponent).getByRole('option', { name: /Synthetic opponent/ })).toBeVisible();
    await user.selectOptions(opponent, 'opponent');
    await user.selectOptions(actor, 'opponent');
    expect(opponent).toHaveValue('');
    expect(within(opponent).queryByRole('option', { name: /Synthetic opponent/ })).not.toBeInTheDocument();
    expect(within(opponent).getByRole('option', { name: /Synthetic teammate/ })).toBeVisible();
  });
  it('blocks an opposite-mode direct link rather than mixing data', async () => {
    setup(); render(<MatchReview id="match-test" mode="real" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('other data mode'); expect(screen.queryByRole('button', { name: 'Add observation' })).not.toBeInTheDocument();
  });
});
