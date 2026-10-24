import { describe, expect, it } from 'vitest';
import { appearanceWrite, clock, cohortLabel, eventWrite, inMode, initials, localVideoPath, percent, validateVideo } from './utils';
import { testAppearance, testEvent, testMetric, testPlayer } from './test-fixtures';

describe('honest data presentation', () => {
  it('never turns missing opportunities or nonfinite values into zero performance', () => {
    expect(percent(null)).toBe('—'); expect(percent(undefined)).toBe('—'); expect(percent(Number.NaN)).toBe('—');
    expect(percent(0)).toBe('0.0%'); expect(percent(0.6, true)).toBe('60.0%'); expect(percent(75)).toBe('75.0%');
  });
  it('formats source-relative timestamps without inventing broadcast time', () => {
    expect(clock(65.5)).toBe('1:05'); expect(clock(5400)).toBe('90:00'); expect(clock(null)).toBe('—'); expect(clock(-1)).toBe('0:00');
  });
  it('uses abstract initials and explicit cohort labels', () => {
    expect(initials('Synthetic roster 01')).toBe('SR'); expect(initials('')).toBe('—');
    expect(cohortLabel(testMetric.cohort)).toBe('U15 · Midfielder · Synthetic development league · 2025');
    expect(cohortLabel({ ...testMetric.cohort, season: null })).toContain('All recorded seasons');
  });
  it('keeps demo and real rows separate even when a bad list contains both', () => {
    const real = { ...testPlayer, id: 'real-test', is_synthetic: false };
    expect(inMode([testPlayer, real], 'real')).toEqual([real]); expect(inMode([testPlayer, real], 'demo')).toEqual([testPlayer]);
  });
});

describe('write-body and media boundaries', () => {
  it('strips server-controlled provenance, tenancy and review attribution from event PUT bodies', () => {
    const body = eventWrite(testEvent);
    expect(body).toMatchObject({ player_id: 'player-test', kind: 'pass', status: 'approved' });
    for (const field of ['id', 'workspace_id', 'match_id', 'provenance', 'confidence', 'model_version', 'created_by', 'reviewed_by', 'reviewed_at', 'is_synthetic']) expect(body).not.toHaveProperty(field);
    expect(body.timestamp_seconds).toBe(65.5);
  });
  it('sends only editable fields when replacing roster coverage', () => {
    expect(appearanceWrite(testAppearance)).toEqual({ player_id: 'player-test', jersey_number: 7, side: 'home', role: 'midfielder', played_seconds: 1800, reviewed_seconds: 600, notes: '' });
  });
  it('uses an authenticated local path without session tokens', () => {
    expect(localVideoPath('upload-test')).toBe('/api/uploads/upload-test/video');
    expect(localVideoPath('../bad?id=x')).toBe('/api/uploads/..%2Fbad%3Fid%3Dx/video');
  });
  it('rejects unsupported extension, unsupported MIME and empty video before transfer', () => {
    expect(validateVideo(new File(['x'], 'video.html', { type: 'video/mp4' }))).toContain('MP4');
    expect(validateVideo(new File(['x'], 'video.mp4', { type: 'text/html' }))).toContain('media type');
    expect(validateVideo(new File([], 'video.mp4', { type: 'video/mp4' }))).toContain('empty');
    expect(validateVideo(new File(['x'], 'video.MP4', { type: 'video/mp4' }))).toBeNull();
  });
});
