import { describe, expect, it } from 'vitest';
import type { Identity, Observation } from './intelligence-types';
import { confirmedIdentity, sampleAt, sourceTime, trackTrail, validateTimeline } from './video-evidence';
import type { TimelineWindow } from './video-evidence';

const observation: Observation = { id: 'one', track_id: 'chunk:a:1', processing_chunk_id: 'a', timestamp_seconds: 1, bbox: [.1, .2, .3, .4], label: 'player', confidence: .9, model_version: 'synthetic-only' };
const identity: Identity = { id: 'identity', upload_id: 'upload', track_id: observation.track_id, player_id: 'player', status: 'confirmed', start_seconds: 1, end_seconds: 2, reviewed_at: '2025-01-01', reviewed_by: 'reviewer', reason: 'Fictional test', revision: 1 };

describe('source-time overlay rules', () => {
  it('never displays future evidence, old boxes or another sample as a live position', () => {
    expect(sampleAt([observation], .9, 30, .5, true).items).toEqual([]);
    expect(sampleAt([observation], 1, 30, .5, true).held).toBe(false);
    expect(sampleAt([observation], 1.2, 30, .5, true).held).toBe(true);
    expect(sampleAt([observation], 1.5, 30, .5, true).items).toEqual([]);
    expect(sampleAt([observation], 1.2, 30, .5, false).items).toEqual([]);
    expect(sampleAt([observation], 1.05, 30, .05, true).items).toEqual([]);
    expect(sampleAt([observation], 1.6, 30, 10, true).items).toEqual([]);
    expect(sampleAt([observation], 1.2, 30, null, true).items).toEqual([]);
  });
  it('groups one timestamp without leaking future samples', () => {
    const second = { ...observation, id: 'two', timestamp_seconds: 1.5 };
    expect(sampleAt([second, observation], 1.5, 30, .5, true).items).toEqual([second]);
  });
  it('requires one reviewed, in-interval, same-upload identity', () => {
    expect(confirmedIdentity(observation, [identity], 'upload')).toBe(identity);
    for (const change of [{ status: 'proposed' as const }, { status: 'rejected' as const }, { upload_id: 'other' }, { start_seconds: 1.1 }, { reviewed_by: null }, { reviewed_at: null }]) expect(confirmedIdentity(observation, [{ ...identity, ...change }], 'upload')).toBeNull();
    expect(confirmedIdentity(observation, [identity, { ...identity, id: 'conflict' }], 'upload')).toBeNull();
    expect(confirmedIdentity({ ...observation, timestamp_seconds: 2.1 }, [identity], 'upload')).toBeNull();
  });
  it('never connects trails across chunk boundaries, long gaps or classes', () => {
    const rows = [0, .5, 2, 2.5].map((timestamp_seconds) => ({ ...observation, timestamp_seconds }));
    rows.push({ ...observation, timestamp_seconds: 2.2, processing_chunk_id: 'different' }, { ...observation, timestamp_seconds: 2.3, label: 'ball' });
    expect(trackTrail(rows, { ...observation, timestamp_seconds: 2.5 }, .5).map((row) => row.timestamp_seconds)).toEqual([2, 2.5]);
    expect(trackTrail(rows, observation, null)).toEqual([]);
  });
  it('fails closed on source/window/geometry mixups', () => {
    const data: TimelineWindow = { job_id: 'job', upload_id: 'upload', start_seconds: 0, end_seconds: 15, truncated: false, segments: [], items: [{ ...observation, upload_id: 'upload', match_id: 'match', is_synthetic: true }] };
    expect(validateTimeline(data, 'job', 'upload', 'match', true, 0, 15)).toBe(data);
    for (const patch of [{ job_id: 'wrong' }, { upload_id: 'wrong' }, { start_seconds: 10 }, { items: [{ ...data.items[0], is_synthetic: false }] }, { items: [{ ...data.items[0], bbox: [0, 0, -1, 1] as [number, number, number, number] }] }]) expect(() => validateTimeline({ ...data, ...patch }, 'job', 'upload', 'match', true, 0, 15)).toThrow(/did not match/);
  });
  it('preserves source time beyond ninety minutes without match-clock claims', () => {
    expect(sourceTime(5700.125)).toBe('95:00.13');
    expect(sourceTime(NaN)).toBe('00:00.00');
    expect(sourceTime(59.999)).toBe('01:00.00');
  });
});
