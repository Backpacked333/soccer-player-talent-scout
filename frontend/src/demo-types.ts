import type { AppearanceRead, EventRead, LeaderboardResponse, MatchRead, PlayerProfile, PlayerRead } from './types';
import type { DuelReport, Knowledge, Pairwise } from './intelligence-types';

export type DemoEvent = Pick<EventRead, 'id' | 'match_id' | 'player_id' | 'kind' | 'outcome' | 'timestamp_seconds' | 'status' | 'opponent_player_id' | 'is_synthetic'>;
export interface DemoFixture { match: MatchRead; roster: AppearanceRead[]; events: DemoEvent[] }
export interface DemoData {
  schema_version: 1; is_synthetic: true; read_only: true; notice: string;
  players: PlayerRead[]; fixtures: DemoFixture[];
  profiles: Record<string, PlayerProfile>;
  histories: Record<string, Record<string, Knowledge>>;
  leaderboards: Record<'midfielder' | 'defender', LeaderboardResponse>;
  duels: DuelReport; pairs: Record<string, Pairwise>;
}

export function validateDemo(value: unknown): DemoData {
  const data = value as DemoData | null;
  if (!data || data.schema_version !== 1 || data.is_synthetic !== true || data.read_only !== true
    || !Array.isArray(data.players) || data.players.length !== 4 || !data.players.every((p) => p.is_synthetic === true)
    || !Array.isArray(data.fixtures) || data.fixtures.length !== 3
    || !data.fixtures.every((f) => f.match.is_synthetic === true && f.roster.every((r) => r.is_synthetic === true) && f.events.every((e) => e.is_synthetic === true))
    || !data.profiles || !data.histories || !data.leaderboards || data.duels?.scope.mode !== 'demo'
    || !data.players.every((p) => data.profiles[p.id]?.player.is_synthetic === true && data.profiles[p.id].metrics.every((m) => m.is_synthetic === true) && Object.values(data.histories[p.id] ?? {}).length === 3 && Object.values(data.histories[p.id]).every((k) => k.scope.mode === 'demo'))
    || !Object.values(data.leaderboards).every((board) => board.mode === 'demo' && board.is_synthetic === true && board.entries.every((e) => e.is_synthetic === true))) {
    throw new Error('This demonstration only accepts its fictional, read-only data snapshot.');
  }
  return data;
}
