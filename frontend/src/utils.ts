import type { AppearanceCreate, AppearanceRead, EventRead, EventWrite, Mode } from './types';

export const humanize = (value: string) => value.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
export const initials = (name: string) => name.trim().split(/\s+/).slice(0, 2).map((s) => s[0] ?? '').join('').toUpperCase() || '—';
export const percent = (value: number | null | undefined, fraction = false) => value == null || !Number.isFinite(value) ? '—' : `${(value * (fraction ? 100 : 1)).toFixed(1)}%`;
export const clock = (seconds: number | null | undefined) => seconds == null || !Number.isFinite(seconds) ? '—' : `${Math.floor(Math.max(0, seconds) / 60)}:${String(Math.floor(Math.max(0, seconds) % 60)).padStart(2, '0')}`;
export const minutes = (seconds: number) => `${(seconds / 60).toLocaleString(undefined, { maximumFractionDigits: 1 })} min`;
export const dateLabel = (date: string) => new Date(date.length === 10 ? `${date}T12:00:00` : date).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
export const cohortLabel = (cohort: { age_group: string; role: string; competition: string; season: string | null }) => `${cohort.age_group} · ${humanize(cohort.role)} · ${cohort.competition} · ${cohort.season || 'All recorded seasons'}`;
export const inMode = <T extends { is_synthetic: boolean }>(rows: T[], mode: Mode): T[] => rows.filter((row) => row.is_synthetic === (mode === 'demo'));

/** PUT bodies must not echo IDs, tenant fields, provenance or server-controlled review metadata. */
export function eventWrite(event: EventRead): EventWrite {
  const { player_id, kind, outcome, timestamp_seconds, end_timestamp_seconds, status, opponent_player_id, start_x, start_y, end_x, end_y, notes } = event;
  const { contest_id, period, coordinate_system, coordinate_provenance, attacking_direction, possession_retained, shot_outcome } = event;
  return { player_id, kind, outcome, timestamp_seconds, end_timestamp_seconds, status, opponent_player_id, start_x, start_y, end_x, end_y, notes, contest_id, period, coordinate_system, coordinate_provenance, attacking_direction, possession_retained, shot_outcome };
}
export function appearanceWrite(row: AppearanceRead): AppearanceCreate {
  const { player_id, jersey_number, side, role, played_seconds, reviewed_seconds, notes } = row;
  return { player_id, jersey_number, side, role, played_seconds, reviewed_seconds, notes };
}
export function localVideoPath(id: string) { return `/api/uploads/${encodeURIComponent(id)}/video`; }
export function validateVideo(file: File): string | null {
  if (!/\.(mp4|mov|webm)$/i.test(file.name)) return 'Choose an MP4, MOV or WebM video.';
  if (!['video/mp4', 'video/quicktime', 'video/webm'].includes(file.type)) return 'This file does not have a supported video media type.';
  if (file.size === 0) return 'This video file is empty.';
  return null; // Server-configured size, stream and duration limits remain authoritative.
}
