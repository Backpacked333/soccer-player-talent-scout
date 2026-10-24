import { createRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { VideoEvidence } from './VideoEvidence';
import type { Identity } from './intelligence-types';
import type { JobRead, UploadRead } from './types';
import type { TimelineWindow } from './video-evidence';
import { testEvent, testMatch, testPlayer } from './test-fixtures';
import { unavailableValue } from './intelligence-fixtures';

vi.mock('./api', () => ({ api: vi.fn() }));
const upload: UploadRead = { id: 'upload', match_id: testMatch.id, workspace_id: 'workspace-test', duration_seconds: 60, width: 1280, height: 720, fps: 30, original_filename: 'synthetic-test.mp4', content_type: 'video/mp4', size_bytes: 1000, sha256: 'a'.repeat(64), status: 'probed', is_synthetic: true, created_at: '2025-01-01' };
const job = { id: 'job', upload_id: upload.id, match_id: testMatch.id, updated_at: '2025-01-01', status: 'needs_review', stage: 'detector_complete', processing_coverage: { sample_interval_seconds: .5, sampled_frames: 120, completed_chunks: 2, planned_chunks: 3 } } as JobRead;
const observation = { id: 'observation', upload_id: upload.id, match_id: testMatch.id, is_synthetic: true, track_id: 'chunk:segment:player-1', processing_chunk_id: 'segment', timestamp_seconds: 1, bbox: [.1, .2, .3, .6] as [number, number, number, number], label: 'player', confidence: .85, model_version: 'synthetic-test-only' };
const window: TimelineWindow = { job_id: job.id, upload_id: upload.id, start_seconds: 0, end_seconds: 15, truncated: false, display_geometry: { transform: 'identity', aspect_ratio: 16 / 9 }, items: [observation], segments: [{ start_seconds: 0, end_seconds: 30, status: 'completed' }, { start_seconds: 30, end_seconds: 60, status: 'failed' }] };

beforeEach(() => { vi.mocked(api).mockReset(); vi.mocked(api).mockImplementation(async (path) => path.includes('action-value') ? unavailableValue : window); });
function setup(identities: Identity[] = [], width = 1280, height = 720) {
  const videoRef = createRef<HTMLVideoElement>();
  const onReviewTrack = vi.fn();
  const view = render(<VideoEvidence videoRef={videoRef} match={testMatch} upload={upload} job={job} identities={identities} players={[testPlayer]} events={[{ ...testEvent, timestamp_seconds: 2.5 }]} onReviewTrack={onReviewTrack} />);
  Object.defineProperties(videoRef.current!, { videoWidth: { value: width }, videoHeight: { value: height } });
  fireEvent.loadedMetadata(videoRef.current!);
  const time = (seconds: number) => { videoRef.current!.currentTime = seconds; fireEvent.timeUpdate(videoRef.current!); };
  return { ...view, videoRef, time, onReviewTrack };
}

describe('synchronized evidence workspace', () => {
  it('shows real stored boxes, source provenance and explicit anonymous identity', async () => {
    const user = userEvent.setup(); const { time, onReviewTrack } = setup();
    await waitFor(() => expect(api).toHaveBeenCalledWith('/jobs/job/timeline?start=0&end=15', expect.anything()));
    time(1);
    await user.click(await screen.findByRole('button', { name: 'Inspect Player · player-1' }));
    expect(screen.getByText('synthetic-test-only')).toBeVisible();
    expect(screen.getByText('Unresolved identity — not a player profile')).toBeVisible();
    expect(screen.queryByRole('link', { name: /Open persistent/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Review track identity/ }));
    expect(onReviewTrack).toHaveBeenCalledWith(observation);
    await user.click(screen.getByRole('tab', { name: 'Models' }));
    expect(screen.getByText('Automatic soccer-event extraction is not implemented.')).toBeVisible();
    expect(screen.getByText('Speed / distance')).toBeVisible();
    expect(screen.getByText(/persisted outputs, not a live model stream/)).toBeVisible();
  });
  it('gives a reviewer-confirmed identity a persistent profile link only in its reviewed interval', async () => {
    const user = userEvent.setup();
    const { time } = setup([{ id: 'assignment', upload_id: 'upload', track_id: observation.track_id, player_id: testPlayer.id, start_seconds: 1, end_seconds: 1.2, status: 'confirmed', revision: 2, reviewed_by: 'reviewer', reviewed_at: '2025-01-01', reason: 'Fictional test decision' }]);
    time(1);
    await user.click(await screen.findByRole('button', { name: `Inspect ${testPlayer.display_name}` }));
    expect(screen.getByRole('link', { name: /Open persistent player record/ })).toHaveAttribute('href', `#/players/${testPlayer.id}`);
    expect(screen.getByText('Reviewer-confirmed · revision 2')).toBeVisible();
    time(2);
    expect(screen.queryByRole('link', { name: /Open persistent/ })).not.toBeInTheDocument();
  });
  it('labels held boxes and lets the reviewer disable hold, overlays and score filters', async () => {
    const user = userEvent.setup(); const { time } = setup(); time(1.2);
    expect(await screen.findByText(/held position, not interpolated/)).toBeVisible();
    await user.click(screen.getByLabelText('Hold recent sample ≤0.5s'));
    expect(screen.queryByRole('button', { name: 'Inspect Player · player-1' })).not.toBeInTheDocument();
    time(1);
    await user.selectOptions(screen.getByLabelText('Min. detector score'), '0.9');
    expect(screen.queryByRole('button', { name: 'Inspect Player · player-1' })).not.toBeInTheDocument();
    expect(screen.getByText(/Current filters hide/)).toBeVisible();
    await user.selectOptions(screen.getByLabelText('Min. detector score'), '0');
    await user.click(screen.getByLabelText('Boxes'));
    expect(screen.queryByRole('button', { name: 'Inspect Player · player-1' })).not.toBeInTheDocument();
    expect(screen.getByText('Anonymous · identity unresolved')).toBeVisible();
  });
  it('clears stale overlays immediately when seeking into another source window', async () => {
    const { time } = setup(); time(1);
    expect(await screen.findByRole('button', { name: 'Inspect Player · player-1' })).toBeVisible();
    vi.mocked(api).mockReturnValue(new Promise(() => {}));
    time(21);
    expect(screen.queryByRole('button', { name: 'Inspect Player · player-1' })).not.toBeInTheDocument();
    await waitFor(() => expect(api).toHaveBeenCalledWith('/jobs/job/timeline?start=15&end=35', expect.anything()));
  });
  it('removes a selected trail and inspector when its track is filtered out', async () => {
    vi.mocked(api).mockResolvedValue({ ...window, items: [{ ...observation, id: 'prior', timestamp_seconds: .5 }, observation] });
    const user = userEvent.setup(); const { time, container } = setup(); time(1);
    await user.click(await screen.findByRole('button', { name: 'Inspect Player · player-1' }));
    expect(container.querySelector('.ev-trail')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Class'), 'ball');
    expect(container.querySelector('.ev-trail')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Review track identity/ })).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Class'), 'all');
    expect(container.querySelector('.ev-trail')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Min. detector score'), '0.9');
    expect(container.querySelector('.ev-trail')).not.toBeInTheDocument();
  });
  it('rotates boxes into a portrait presentation instead of overlaying raw landscape coordinates', async () => {
    vi.mocked(api).mockResolvedValue({ ...window, display_geometry: { transform: 'rotate_90_ccw', aspect_ratio: 9 / 16 } });
    const { time } = setup([], 720, 1280); time(1);
    const box = (await screen.findByRole('button', { name: 'Inspect Player · player-1' })).querySelector('rect')!;
    expect(Number(box.getAttribute('x'))).toBeCloseTo(200);
    expect(Number(box.getAttribute('y'))).toBeCloseTo(.7 * 1000 * 16 / 9);
    expect(Number(box.getAttribute('width'))).toBeCloseTo(400);
  });
  it.each([null, { transform: 'rotate_90_ccw', aspect_ratio: 9 / 16 }])('hides unknown or browser-mismatched source geometry: %j', async (display_geometry) => {
    vi.mocked(api).mockResolvedValue({ ...window, display_geometry });
    const { time } = setup(); time(1);
    expect(await screen.findByRole('alert')).toHaveTextContent('source orientation / pixel geometry is unknown');
    expect(screen.queryByRole('button', { name: 'Inspect Player · player-1' })).not.toBeInTheDocument();
  });
  it('revalidates legacy source metadata and reloads observations without changing review evidence', async () => {
    vi.mocked(api).mockResolvedValueOnce({ ...window, display_geometry: null }).mockResolvedValueOnce({}).mockResolvedValue(window);
    const { time } = setup(); time(1);
    await userEvent.click(await screen.findByRole('button', { name: 'Revalidate source geometry' }));
    expect(api).toHaveBeenCalledWith(`/jobs/${job.id}/revalidate-source`, { method: 'POST' });
    expect(await screen.findByRole('button', { name: 'Inspect Player · player-1' })).toBeVisible();
  });
  it('does not draw truncated or wrong-source evidence', async () => {
    vi.mocked(api).mockResolvedValue({ ...window, truncated: true });
    const { time } = setup(); time(1);
    expect(await screen.findByRole('alert')).toHaveTextContent('exceeds 5,000');
    expect(screen.queryByRole('button', { name: 'Inspect Player · player-1' })).not.toBeInTheDocument();
  });
  it('seeks from a timestamped event and displays its actual notes and review provenance', async () => {
    const user = userEvent.setup(); const { videoRef } = setup();
    await user.click(screen.getByRole('button', { name: 'Inspect approved pass at 00:02.50' }));
    expect(videoRef.current!.currentTime).toBe(2.5);
    expect(screen.getByText(testEvent.notes)).toBeVisible();
    expect(await screen.findByText('Action value unavailable')).toBeVisible();
    expect(screen.getByText('No model attribution')).toBeVisible();
  });
});
