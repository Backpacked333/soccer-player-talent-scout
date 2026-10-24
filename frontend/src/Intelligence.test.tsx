import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { IdentityReview, SourceEvidence } from './IdentityReview';
import { EventValue, PlayerKnowledge, knowledgeQuery } from './Knowledge';
import { testKnowledge, unavailableValue } from './intelligence-fixtures';
import type { Identity } from './intelligence-types';
import type { JobRead } from './types';
import { testAppearance, testEvent, testMatch, testPlayer } from './test-fixtures';

vi.mock('./api', () => ({ api: vi.fn() }));
beforeEach(() => { vi.mocked(api).mockReset(); });
const identity: Identity = { id: 'identity-test', upload_id: 'upload-test', track_id: 'chunk:synthetic:1', start_seconds: 1, end_seconds: 2, player_id: testPlayer.id, status: 'proposed', revision: 1, reason: 'Unclear fictional jersey', reviewed_by: null, reviewed_at: null };

describe('auditable reviewed intelligence', () => {
  it('shows three-match cumulative evidence and relationships, never autonomous learning', async () => {
    vi.mocked(api).mockResolvedValue(testKnowledge);
    render(<PlayerKnowledge id={testPlayer.id} mode="demo" query={knowledgeQuery(testMatch, 'demo', 'all_roles', '2025-04-01')} />);
    expect(await screen.findByText(/not autonomous visual learning/)).toBeVisible();
    expect(screen.getByText(/cumulative 3 events and 2\/3 passes/)).toBeVisible();
    expect(screen.getByText('Supporting event IDs: event-1')).toBeVisible();
    expect(screen.getByRole('link', { name: 'fictional-teammate' })).toHaveAttribute('href', '#/players/fictional-teammate');
    expect(screen.getByRole('link', { name: 'fictional-rival' })).toBeVisible();
    expect(screen.getByText('Supporting event IDs: duel-1')).toBeVisible();
    expect(screen.getByText(/Counts are not per-90 rates/)).toBeVisible();
    expect(api).toHaveBeenCalledWith(expect.stringContaining('role=all_roles&as_of=2025-04-01'), expect.anything());
  });
  it('refuses a history response for another mode or player', async () => {
    vi.mocked(api).mockResolvedValue(testKnowledge);
    render(<PlayerKnowledge id="other-player" mode="real" query="mode=real" />);
    expect(await screen.findByRole('alert')).toHaveTextContent('History scope did not match');
    expect(screen.queryByText('fictional-rival')).not.toBeInTheDocument();
  });
  it('shows unresolved, confirmed and rejected identities and requires a correction reason/revision', async () => {
    const onSaved = vi.fn();
    let rows: Identity[] = [identity, { ...identity, id: 'confirmed', status: 'confirmed' }, { ...identity, id: 'rejected', status: 'rejected' }];
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path.includes('/knowledge?')) return testKnowledge;
      if (options?.method === 'PUT') { rows = rows.map((row) => row.id === identity.id ? { ...row, status: 'confirmed', revision: 2 } : row); return rows[0]; }
      return rows;
    });
    const user = userEvent.setup();
    render(<IdentityReview match={testMatch} mode="demo" roster={[testAppearance]} players={[testPlayer]} events={[]} upload={null} job={undefined} onSaved={onSaved} />);
    expect(await screen.findByText(/Unresolved: no identity is confirmed/)).toBeVisible();
    expect(screen.getByText('Confirmed')).toBeVisible();
    expect(screen.getByText('Rejected')).toBeVisible();
    const article = screen.getByText(/Unresolved: no identity/).closest('article')!;
    const reason = within(article).getByLabelText('Reason for identity correction');
    expect(reason).toBeRequired();
    await user.click(within(article).getByRole('button', { name: 'Save identity decision' }));
    expect(onSaved).not.toHaveBeenCalled();
    await user.type(reason, 'Reviewed the fictional jersey on the source frame');
    await user.selectOptions(within(article).getByLabelText('Identity decision'), 'confirmed');
    await user.click(within(article).getByRole('button', { name: 'Save identity decision' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/matches/match-test/identities/identity-test', { method: 'PUT', body: { expected_revision: 1, player_id: testPlayer.id, status: 'confirmed', reason: 'Reviewed the fictional jersey on the source frame' } }));
    expect(onSaved).toHaveBeenCalledOnce();
    expect(screen.getAllByText(/never transfers approval to another child/).length).toBe(3);
  });
  it('binds only a confirmed same-player interval with revision and a fresh-review warning', async () => {
    const onSaved = vi.fn();
    const event = { ...testEvent, timestamp_seconds: 1.5, end_timestamp_seconds: null };
    const confirmed = { ...identity, id: 'confirmed-identity', status: 'confirmed' as const, revision: 3 };
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path.includes('/knowledge?')) return testKnowledge;
      if (options?.method === 'POST') return { ...event, status: 'draft' };
      return [identity, confirmed];
    });
    const user = userEvent.setup();
    render(<IdentityReview match={testMatch} mode="demo" roster={[testAppearance]} players={[testPlayer]} events={[event, { ...event, id: 'outside', timestamp_seconds: 5 }, { ...event, id: 'other-player', player_id: 'other' }]} upload={null} job={undefined} onSaved={onSaved} />);
    const selector = await screen.findByLabelText('Event to link to confirmed track');
    // One link form, not one per unresolved row; no out-of-interval/opponent options.
    expect(screen.getAllByLabelText('Event to link to confirmed track')).toHaveLength(1);
    expect(within(selector).getAllByRole('option')).toHaveLength(2);
    const form = selector.closest('form')!;
    await user.selectOptions(selector, event.id);
    await user.click(within(form).getByRole('button'));
    expect(onSaved).not.toHaveBeenCalled();
    await user.type(within(form).getByRole('textbox'), 'Explicit fictional source attribution');
    await user.click(within(form).getByRole('button'));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/events/event-test/identity', { method: 'POST', body: { assignment_id: confirmed.id, expected_revision: 3, reason: 'Explicit fictional source attribution' } }));
    expect(onSaved).toHaveBeenCalledOnce();
    expect(await screen.findByText('Event returned to draft. Review it again before it contributes.')).toBeVisible();
  });
  it('paginates source observations and distinguishes detector from reviewed evidence', async () => {
    const observation = { id: 'o1', track_id: 'chunk:synthetic:1', timestamp_seconds: 5, label: 'player', bbox: [0.1, 0.2, 0.3, 0.4], confidence: 0.9, model_version: 'synthetic-only', processing_chunk_id: 'c1' };
    vi.mocked(api).mockImplementation(async (path) => path.includes('observations') ? { items: [observation], next_cursor: path.includes('cursor=') ? null : 'o1' } : { items: [], next_cursor: null });
    const job = { id: 'job-test', processing_coverage: { status: 'partial', source_duration_seconds: 5700, covered_seconds: 600, planned_chunks: 10, completed_chunks: 1, failed_chunks: 1, sample_interval_seconds: 10, sampled_frames: 60, observation_count: 60, interpretation: 'Sampled source only.' } } as JobRead;
    const onTrack = vi.fn(); const user = userEvent.setup();
    render(<SourceEvidence job={job} onTrack={onTrack} />);
    await user.click(screen.getByText('Detector coverage and anonymous source evidence'));
    expect(screen.getByText('Detector coverage is not reviewed-event coverage.')).toBeVisible();
    expect(await screen.findByText('chunk:synthetic:1')).toBeVisible();
    expect(screen.getByText('0.100, 0.200, 0.300, 0.400')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Use source track' }));
    expect(onTrack).toHaveBeenCalledWith(observation);
    await user.click(screen.getByRole('button', { name: 'Next observations' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/jobs/job-test/observations?limit=50&cursor=o1', expect.anything()));
    expect(screen.getByRole('button', { name: 'Next observations' })).toBeDisabled();
  });
  it('keeps a missing spatial artifact unavailable rather than showing an invented score', async () => {
    vi.mocked(api).mockResolvedValue(unavailableValue);
    render(<EventValue eventId="event-test" />);
    expect(await screen.findByText('Action value unavailable')).toBeVisible();
    expect(screen.getByText('No operator-activated spatial model.')).toBeVisible();
    expect(screen.queryByText(/Research action value:/)).not.toBeInTheDocument();
  });
});
