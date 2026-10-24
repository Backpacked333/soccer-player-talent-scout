import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, uploadVideo } from './api';
import { ConsentForm, RosterEditor, UploadPanel } from './ReviewSetup';
import { testAppearance, testMatch, testPlayer } from './test-fixtures';

vi.mock('./api', () => ({ api: vi.fn(), uploadVideo: vi.fn() }));
beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(uploadVideo).mockReset(); });

describe('permission and identity workflow', () => {
  it('starts with four unchecked explicit declarations, blocked without a roster', () => {
    render(<ConsentForm matchId="match-test" attestation={null} hasRoster={false} onSaved={vi.fn()} />);
    const boxes = screen.getAllByRole('checkbox'); expect(boxes).toHaveLength(4);
    boxes.forEach((box) => { expect(box).not.toBeChecked(); expect(box).toBeRequired(); expect(box).toBeDisabled(); });
    expect(screen.getByRole('button', { name: 'Record permission declaration' })).toBeDisabled();
  });
  it('persists all explicit consent declarations, retention and notes only after confirmation', async () => {
    const user = userEvent.setup(); const saved = vi.fn(); vi.mocked(api).mockResolvedValue({});
    render(<ConsentForm matchId="match-test" attestation={null} hasRoster onSaved={saved} />);
    await user.click(screen.getByRole('button', { name: 'Record permission declaration' })); expect(api).not.toHaveBeenCalled();
    for (const checkbox of screen.getAllByRole('checkbox')) await user.click(checkbox);
    await user.type(screen.getByLabelText('Permission notes (optional)'), 'Synthetic permission test');
    await user.click(screen.getByRole('button', { name: 'Record permission declaration' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/matches/match-test/attestation', { method: 'POST', body: { permission_to_process: true, guardian_or_club_authorization: true, opponent_privacy_review: true, authorized_roster: true, retention_days: 30, notes: 'Synthetic permission test' } }));
    expect(saved).toHaveBeenCalledOnce(); expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
  it('saves coverage through a full roster PUT without tenant/provenance fields', async () => {
    const user = userEvent.setup(); vi.mocked(api).mockResolvedValue([]); const saved = vi.fn();
    render(<RosterEditor match={testMatch} roster={[testAppearance]} players={[testPlayer]} onSaved={saved} />);
    const coverage = screen.getByLabelText('Synthetic roster 01: reviewed seconds'); expect(coverage).toHaveAttribute('max', '1800');
    await user.clear(coverage); await user.type(coverage, '900'); await user.click(screen.getByRole('button', { name: 'Save authorized roster' }));
    await waitFor(() => expect(api).toHaveBeenCalledWith('/matches/match-test/roster', expect.objectContaining({ method: 'PUT', body: { appearances: [{ player_id: 'player-test', jersey_number: 7, side: 'home', role: 'midfielder', played_seconds: 1800, reviewed_seconds: 900, notes: '' }] } })));
    expect(saved).toHaveBeenCalledOnce();
  });
  it('shows a persistence failure instead of a fake successful save', async () => {
    const user = userEvent.setup(); vi.mocked(api).mockRejectedValue(new Error('Actor is referenced by reviewed evidence.'));
    render(<RosterEditor match={testMatch} roster={[testAppearance]} players={[testPlayer]} onSaved={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: 'Save authorized roster' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Actor is referenced'); expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});

describe('private upload controls', () => {
  it('disables file selection and upload until permission and roster exist', () => {
    render(<UploadPanel matchId="match-test" upload={null} permitted={false} onSaved={vi.fn()} />);
    expect(screen.getByLabelText('Select a video file')).toBeDisabled(); expect(screen.getByRole('button', { name: 'Upload private footage' })).toBeDisabled();
  });
  it('sends a real selected video and reports progress without generating analysis', async () => {
    const user = userEvent.setup(); const saved = vi.fn();
    vi.mocked(uploadVideo).mockImplementation(async (_id, _file, progress) => { progress(1); return { upload: {}, job: {} } as never; });
    render(<UploadPanel matchId="match-test" upload={null} permitted onSaved={saved} />);
    const file = new File(['synthetic test bytes only'], 'sample.mp4', { type: 'video/mp4' });
    await user.upload(screen.getByLabelText('Select a video file'), file); await user.click(screen.getByRole('button', { name: 'Upload private footage' }));
    await waitFor(() => expect(uploadVideo).toHaveBeenCalledWith('match-test', file, expect.any(Function), expect.any(AbortSignal)));
    expect(saved).toHaveBeenCalledOnce(); expect(api).not.toHaveBeenCalled(); expect(screen.getByText(/An upload does not create player statistics/)).toBeVisible();
  });
  it('aborts an in-flight transfer on unmount', async () => {
    const user = userEvent.setup(); let signal: AbortSignal | undefined;
    vi.mocked(uploadVideo).mockImplementation((_id, _file, _progress, suppliedSignal) => { signal = suppliedSignal; return new Promise(() => undefined); });
    const { unmount } = render(<UploadPanel matchId="match-test" upload={null} permitted onSaved={vi.fn()} />);
    await user.upload(screen.getByLabelText('Select a video file'), new File(['x'], 'sample.webm', { type: 'video/webm' })); await user.click(screen.getByRole('button', { name: 'Upload private footage' }));
    expect(signal?.aborted).toBe(false); unmount(); expect(signal?.aborted).toBe(true);
  });
});
