import type { Identity, Observation } from './intelligence-types';

export interface TimelineWindow {
  job_id: string;
  upload_id: string;
  start_seconds: number;
  end_seconds: number;
  truncated: boolean;
  items: (Observation & { upload_id: string; match_id: string; is_synthetic: boolean })[];
  segments: { start_seconds: number; end_seconds: number; status: string }[];
}

export function validateTimeline(data: TimelineWindow, jobId: string, uploadId: string, matchId: string, synthetic: boolean, start: number, end: number) {
  if (data.job_id !== jobId || data.upload_id !== uploadId || data.start_seconds !== start || data.end_seconds !== end || typeof data.truncated !== 'boolean' || !Array.isArray(data.items) || data.items.length > 5000 || !Array.isArray(data.segments) || data.segments.length > 1440 || data.items.some((row) =>
    row.upload_id !== uploadId || row.match_id !== matchId || row.is_synthetic !== synthetic || !Number.isFinite(row.timestamp_seconds) || row.timestamp_seconds < start || row.timestamp_seconds >= end ||
    !Array.isArray(row.bbox) || row.bbox.length !== 4 || row.bbox.some((n) => !Number.isFinite(n) || n < 0 || n > 1) || row.bbox[2] <= row.bbox[0] || row.bbox[3] <= row.bbox[1]
  )) throw new Error('The evidence response did not match this source window. Overlays are hidden.');
  return data;
}

export function sampleAt(items: Observation[], time: number, fps: number, cadence: number | null, hold: boolean) {
  let timestamp = -Infinity;
  for (const row of items) if (row.timestamp_seconds <= time + 0.000001) timestamp = Math.max(timestamp, row.timestamp_seconds);
  const age = Math.max(0, time - timestamp);
  const frame = 1 / (Number.isFinite(fps) && fps > 0 ? fps : 30);
  const lifetime = hold && cadence !== null && cadence > 0 ? Math.max(frame, Math.min(0.5, cadence)) : frame;
  if (!Number.isFinite(timestamp) || age >= lifetime) return { items: [], timestamp: null, age: null, held: false };
  return { items: items.filter((row) => Math.abs(row.timestamp_seconds - timestamp) < 0.000001), timestamp, age, held: age >= frame };
}

export function confirmedIdentity(row: Observation, identities: Identity[], uploadId: string) {
  const matches = identities.filter((identity) => identity.upload_id === uploadId && identity.track_id === row.track_id && identity.status === 'confirmed' && identity.reviewed_by !== null && identity.reviewed_at !== null && row.timestamp_seconds >= identity.start_seconds && row.timestamp_seconds <= identity.end_seconds);
  return matches.length === 1 ? matches[0] : null;
}

export function trackTrail(items: Observation[], selected: Observation, cadence: number | null) {
  if (cadence === null || cadence <= 0) return [];
  const history = items.filter((row) => row.track_id === selected.track_id && row.processing_chunk_id === selected.processing_chunk_id && row.label === selected.label && row.timestamp_seconds <= selected.timestamp_seconds && row.timestamp_seconds >= selected.timestamp_seconds - 4).sort((a, b) => a.timestamp_seconds - b.timestamp_seconds);
  let start = 0;
  for (let i = 1; i < history.length; i++) if (history[i].timestamp_seconds - history[i - 1].timestamp_seconds > Math.min(0.75, cadence * 1.5)) start = i;
  return history.slice(start).slice(-16);
}

export function sourceTime(seconds: number) {
  const safe = Math.max(0, Number.isFinite(seconds) ? seconds : 0);
  const centiseconds = Math.round(safe * 100);
  return `${String(Math.floor(centiseconds / 6000)).padStart(2, '0')}:${((centiseconds % 6000) / 100).toFixed(2).padStart(5, '0')}`;
}
