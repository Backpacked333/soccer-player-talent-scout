import type { EventWrite } from './types';
import { Field } from './ui';

export function EventSemantics({ draft, set }: { draft: EventWrite; set: (patch: Partial<EventWrite>) => void }) {
  return <details className="span-2"><summary>Explicit contest, shot and spatial semantics</summary>
    <p className="small">Never infer a goal from “successful”. An explicit contest ID groups the same reviewed duel across inverse annotations; never reuse it for distinct contests. Pitch coordinates are optional, manually qualified evidence, not detector boxes.</p>
    <div className="form-grid">
      {draft.kind === 'duel' && <Field label="Explicit duel contest ID"><input maxLength={80} pattern="[A-Za-z0-9_.:\-]+" value={draft.contest_id ?? ''} onChange={(e) => set({ contest_id: e.target.value || null })} /></Field>}
      <Field label="Period"><input type="number" min={1} max={10} value={draft.period ?? ''} onChange={(e) => set({ period: e.target.value ? Number(e.target.value) : null })} /></Field>
      <Field label="Possession retained"><select value={draft.possession_retained == null ? 'unknown' : String(draft.possession_retained)} onChange={(e) => set({ possession_retained: e.target.value === 'unknown' ? null : e.target.value === 'true' })}><option value="unknown">Unknown</option><option value="true">Explicitly retained</option><option value="false">Explicitly lost</option></select></Field>
      {draft.kind === 'shot' && <Field label="Explicit shot outcome"><select value={draft.shot_outcome ?? ''} onChange={(e) => set({ shot_outcome: e.target.value === '' ? null : e.target.value as 'goal' | 'no_goal' })}><option value="">Unknown (not assumed a goal)</option><option value="goal">Goal</option><option value="no_goal">No goal</option></select></Field>}
      <Field label="Pitch coordinate convention"><select value={draft.coordinate_system ?? 'unknown'} onChange={(e) => set({ coordinate_system: e.target.value as EventWrite['coordinate_system'] })}><option value="unknown">Unqualified / unavailable</option><option value="attack_normalized">0–1 pitch, attack-aligned</option><option value="pitch_normalized">0–1 pitch, fixed reference</option></select></Field>
      <Field label="Attacking direction"><select value={draft.attacking_direction ?? ''} onChange={(e) => set({ attacking_direction: e.target.value === '' ? null : e.target.value as 'left_to_right' | 'right_to_left' })}><option value="">Unknown</option><option value="left_to_right">Left to right</option><option value="right_to_left">Right to left</option></select></Field>
      {(['start_x', 'start_y', 'end_x', 'end_y'] as const).map((key) => <Field key={key} label={`Normalized ${key}`}><input type="number" min={0} max={1} step="any" value={draft[key] ?? ''} onChange={(e) => set({ [key]: e.target.value === '' ? null : Number(e.target.value) })} /></Field>)}
      <Field label="Coordinate provenance / calibration reference" className="span-2"><input maxLength={500} value={draft.coordinate_provenance ?? ''} onChange={(e) => set({ coordinate_provenance: e.target.value || null })} placeholder="Explain reviewed manual pitch mark or calibration, frame and direction; not image-box coordinates." /></Field>
      <button type="button" className="text-button" onClick={() => set({ start_x: null, start_y: null, end_x: null, end_y: null, coordinate_system: 'unknown', coordinate_provenance: null, attacking_direction: null })}>Clear spatial evidence</button>
    </div>
  </details>;
}
