import { useEffect, useRef, useState } from 'react';
import { IdentityReview } from './IdentityReview';
import { EventSemantics } from './EventSemantics';
import { EventValue, PlayerKnowledge, knowledgeQuery } from './Knowledge';
import type { FormEvent } from 'react';
import { ArrowLeft, CheckCheck, Clock3, Crosshair, FileVideo2, History, Pencil, Play, Plus, Save, Trash2, Users, X } from 'lucide-react';
import { api, optional } from './api';
import { useAction, useResource } from './hooks';
import { ConsentForm, RosterEditor, UploadPanel } from './ReviewSetup';
import { MatchForm } from './Matches';
import type { AppearanceRead, AttestationRead, AuditRead, EventRead, EventWrite, JobRead, MatchRead, Mode, PlayerRead, UploadRead } from './types';
import { EVENT_KINDS } from './types';
import { Empty, ErrorBox, Field, JobCard, Loading, PageHeading, Panel, Pitch, StatusBadge, Success } from './ui';
import { clock, dateLabel, eventWrite, humanize, inMode, localVideoPath, percent } from './utils';

function EventEditor({ match, roster, players, editing, maxTime, getCurrentTime, onSaved, onCancel }: { match: MatchRead; roster: AppearanceRead[]; players: PlayerRead[]; editing: EventRead | null; maxTime: number; getCurrentTime: () => number; onSaved: () => void; onCancel: () => void }) {
  const [draft, setDraft] = useState<EventWrite>(() => editing ? eventWrite(editing) : { player_id: '', kind: 'pass', outcome: 'unknown', timestamp_seconds: getCurrentTime(), end_timestamp_seconds: null, status: 'draft', opponent_player_id: null, start_x: null, start_y: null, end_x: null, end_y: null, notes: '' });
  const action = useAction();
  const set = (patch: Partial<EventWrite>) => setDraft((old) => ({ ...old, ...patch }));
  const actorSide = roster.find((row) => row.player_id === draft.player_id)?.side;
  const submit = (e: FormEvent) => { e.preventDefault(); void action.run(async () => { await api(editing ? `/events/${editing.id}` : `/matches/${match.id}/events`, { method: editing ? 'PUT' : 'POST', body: draft }); onSaved(); }); };
  return <form onSubmit={submit} className="form-grid event-editor">
    <div className="span-2 row-between"><h3>{editing ? 'Correct observation' : 'New observation'}</h3><button type="button" className="icon-button" onClick={onCancel} aria-label="Close event editor"><X size={18} /></button></div>
    <Field label="Actor · authorized roster"><select value={draft.player_id} required onChange={(e) => set({ player_id: e.target.value, opponent_player_id: null })}><option value="">Select the observed player…</option>{roster.map((r) => <option key={r.player_id} value={r.player_id}>{r.jersey_number !== null ? `#${r.jersey_number} ` : ''}{players.find((p) => p.id === r.player_id)?.display_name ?? 'Roster player'} · {r.side}</option>)}</select></Field>
    <Field label="Event type"><select value={draft.kind} onChange={(e) => set({ kind: e.target.value as EventWrite['kind'], contest_id: null, shot_outcome: null })}>{EVENT_KINDS.map((kind) => <option key={kind} value={kind}>{humanize(kind)}</option>)}</select></Field>
    <Field label="Start timestamp (seconds)" hint="Relative to this uploaded file, not the broadcast clock."><div className="input-with-button"><input type="number" min={0} max={maxTime} step="any" required value={draft.timestamp_seconds} onChange={(e) => set({ timestamp_seconds: Number(e.target.value) })} /><button type="button" className="icon-button" aria-label="Use current video time" title="Use current video time" onClick={() => set({ timestamp_seconds: getCurrentTime() })}><Crosshair size={17} /></button></div></Field>
    <Field label="End timestamp (seconds, optional)"><input type="number" min={draft.timestamp_seconds} max={maxTime} step="any" value={draft.end_timestamp_seconds ?? ''} onChange={(e) => set({ end_timestamp_seconds: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
    <Field label="Observed outcome"><select value={draft.outcome} onChange={(e) => set({ outcome: e.target.value as EventWrite['outcome'] })}><option value="unknown">Unknown / needs review</option><option value="successful">Successful</option><option value="unsuccessful">Unsuccessful</option></select></Field>
    <Field label="Review decision"><select value={draft.status} onChange={(e) => set({ status: e.target.value as EventWrite['status'] })}><option value="draft">Draft · excluded from metrics</option><option value="approved">Approved · included in metrics</option><option value="rejected">Rejected · excluded from metrics</option></select></Field>
    <Field label="Opponent actor (optional)" className="span-2"><select disabled={!actorSide} value={draft.opponent_player_id ?? ''} onChange={(e) => set({ opponent_player_id: e.target.value || null })}><option value="">Not linked</option>{roster.filter((r) => actorSide && r.side !== actorSide).map((r) => <option key={r.player_id} value={r.player_id}>{players.find((p) => p.id === r.player_id)?.display_name ?? 'Roster player'} · {r.side}</option>)}</select></Field>
    <Field label="Evidence and review notes" className="span-2"><textarea value={draft.notes} onChange={(e) => set({ notes: e.target.value })} maxLength={2000} rows={3} placeholder="What did you observe? Add context, not a prediction of potential." /></Field>
    <EventSemantics draft={draft} set={set} />
    {draft.player_id && <details className="span-2"><summary>Selected player’s current reviewed history and relationships</summary><PlayerKnowledge id={draft.player_id} query={knowledgeQuery(match, match.is_synthetic ? 'demo' : 'real', 'all_roles', match.match_date)} mode={match.is_synthetic ? 'demo' : 'real'} /></details>}
    {editing && <details className="span-2"><summary>Current saved event’s research action value</summary><EventValue eventId={editing.id} /></details>}
    <p className="small muted span-2">Approving records your account and timestamp. A track link never silently approves an event. An observed outcome is not xG, xT or a talent score. Coordinates require an explicit provenance and convention.</p>
    {draft.status === 'approved' && draft.outcome === 'unknown' && <p className="small warning-text span-2">Choose a known outcome before approving this event.</p>}
    <div className="span-2"><ErrorBox message={action.error} /><div className="button-row"><button className="button primary" disabled={action.busy || !draft.player_id || (draft.status === 'approved' && draft.outcome === 'unknown')}><Save size={16} />{action.busy ? 'Saving…' : editing ? 'Save correction' : 'Save observation'}</button><button className="button secondary" type="button" onClick={onCancel}>Cancel</button></div></div>
  </form>;
}

export function MatchReview({ id, mode }: { id: string; mode: Mode }) {
  const [tab, setTab] = useState<'review' | 'setup' | 'audit'>('review');
  const [editingDetails, setEditingDetails] = useState(false);
  const [editor, setEditor] = useState<{ event: EventRead | null; key: number } | null>(null);
  const [statusFilter, setStatusFilter] = useState('all');
  const [videoError, setVideoError] = useState('');
  const [permissionCheck, checkPermission] = useState(0);
  const video = useRef<HTMLVideoElement>(null); const polls = useRef(0);
  const action = useAction();
  const resource = useResource(async (signal) => {
    const [match, roster, players, events, upload, attestation, jobs, audit] = await Promise.all([
      api<MatchRead>(`/matches/${id}`, { signal }), api<AppearanceRead[]>(`/matches/${id}/roster`, { signal }), api<PlayerRead[]>(`/players?mode=${mode}`, { signal }), api<EventRead[]>(`/matches/${id}/events`, { signal }), optional<UploadRead>(`/matches/${id}/upload`, signal), optional<AttestationRead>(`/matches/${id}/attestation`, signal), api<JobRead[]>(`/jobs?mode=${mode}`, { signal }), api<AuditRead[]>(`/matches/${id}/audit`, { signal }),
    ]);
    if (match.is_synthetic !== (mode === 'demo')) throw new Error('This match belongs to the other data mode. Change the workspace data mode to view it.');
    return { match, roster, players: inMode(players, mode), events: inMode(events, mode), upload, attestation, jobs: inMode(jobs, mode).filter((j) => j.match_id === id).sort((a, b) => b.created_at.localeCompare(a.created_at)), audit: inMode(audit, mode) };
  }, `${id}:${mode}`);
  const retentionDeadline = resource.data?.upload && resource.data.attestation ? Date.parse(resource.data.upload.created_at) + resource.data.attestation.retention_days * 86400000 : NaN;
  useEffect(() => {
    const delay = retentionDeadline - Date.now();
    if (!Number.isFinite(delay) || delay <= 0) return;
    // Unmount playback and any open editor when retention expires, even on an idle page.
    const timer = window.setTimeout(() => checkPermission((n) => n + 1), Math.min(delay, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [retentionDeadline, permissionCheck]);
  const activeJobs = resource.data?.jobs.some((j) => ['queued', 'running'].includes(j.status));
  const reloadJobs = resource.reload;
  useEffect(() => { if (!activeJobs || polls.current >= 120) return; const timer = window.setTimeout(() => { polls.current += 1; reloadJobs(); }, 5000); return () => window.clearTimeout(timer); }, [activeJobs, resource.data, reloadJobs]);
  const seek = (seconds: number) => { if (video.current) { video.current.currentTime = Math.max(0, Math.min(seconds, Number.isFinite(video.current.duration) ? video.current.duration : seconds)); video.current.focus(); } };
  if (!resource.data) return <><a href="#/matches" className="back-link"><ArrowLeft size={15} />Match library</a>{resource.loading ? <Loading label="Loading match evidence…" /> : <ErrorBox message={resource.error} retry={resource.reload} />}</>;
  const { match, roster, players, events, upload, attestation, jobs, audit } = resource.data;
  const approved = events.filter((e) => e.status === 'approved');
  const hasPermission = !!attestation && Number.isFinite(retentionDeadline) && Date.now() < retentionDeadline;
  const canReview = roster.length > 0 && (match.is_synthetic || (hasPermission && upload?.status === 'probed' && upload.duration_seconds !== null));
  const reviewBlock = !upload || roster.length === 0 || upload.status !== 'probed' ? 'Save a roster and upload valid, probed footage before recording real evidence.' : !attestation ? 'Confirm current footage permission in Roster, permission & upload before playback or review.' : !hasPermission ? 'The footage retention period has expired or cannot be verified. Delete the match and private footage, or review the permission declaration.' : 'Valid source duration is required before review.';
  const displayed = events.filter((e) => statusFilter === 'all' || e.status === statusFilter).sort((a, b) => a.timestamp_seconds - b.timestamp_seconds);
  return <>
    <a href="#/matches" className="back-link"><ArrowLeft size={15} />Match library</a>
    <PageHeading eyebrow={`${match.age_group} / ${match.competition} / ${match.season}`} title={match.title} description={`${dateLabel(match.match_date)} · ${match.team_name} vs ${match.opponent_name} · ${clock(match.duration_seconds)} planned`} action={<button className="button secondary" onClick={() => setEditingDetails(!editingDetails)}><Pencil size={15} />Match details</button>} />
    <ErrorBox message={resource.error} retry={resource.reload} /><ErrorBox message={action.error} /><Success message={action.success} />
    {editingDetails && <Panel title="Edit match details" className="form-panel"><MatchForm match={match} onSaved={() => { setEditingDetails(false); resource.reload(); }} onCancel={() => setEditingDetails(false)} /></Panel>}
    <div className="match-summary"><span><Users size={17} /><strong>{roster.length}</strong> roster players</span><span><CheckCheck size={17} /><strong>{approved.length}</strong> approved observations</span><span><Clock3 size={17} />{upload?.duration_seconds ? `${clock(upload.duration_seconds)} uploaded footage` : match.is_synthetic ? 'Synthetic fixture · no real video' : 'Footage not yet probed'}</span></div>
    <div className="tab-list" role="tablist" aria-label="Match workspace"><button role="tab" aria-selected={tab === 'review'} aria-controls="match-tabpanel" onClick={() => setTab('review')} id="tab-review"><Play size={16} />Review room</button><button role="tab" aria-selected={tab === 'setup'} aria-controls="match-tabpanel" onClick={() => setTab('setup')} id="tab-setup"><Users size={16} />Roster, permission & upload</button><button role="tab" aria-selected={tab === 'audit'} aria-controls="match-tabpanel" onClick={() => setTab('audit')} id="tab-audit"><History size={16} />Audit trail <span>{audit.length}</span></button></div>
    <div id="match-tabpanel" role="tabpanel" aria-labelledby={`tab-${tab}`}>
      {tab === 'setup' && <div className="setup-stack">
        <Panel title="01 / Authorized roster & identity" subtitle="Link players across matches using their roster identity." className="form-panel"><RosterEditor key={JSON.stringify(roster)} match={match} roster={roster} players={players} onSaved={resource.reload} /></Panel>
        <Panel title="02 / Permission to process" subtitle="Explicit permission comes before footage." className="form-panel"><ConsentForm matchId={id} attestation={attestation} hasRoster={roster.length > 0} onSaved={resource.reload} /></Panel>
        <Panel title="03 / Private match footage" subtitle="No public media URLs. No automatic identity inference." className="form-panel">{match.is_synthetic ? <Empty icon={<FileVideo2 size={24} />} title="Synthetic fixture, not real footage" description="This demo intentionally contains no real minors’ videos. Switch to your real workspace to upload authorized footage." /> : <UploadPanel matchId={id} upload={upload} permitted={!!attestation && roster.length > 0} onSaved={resource.reload} />}</Panel>
        <Panel title="Delete match & evidence" className="form-panel"><p className="small muted">Permanently delete this match, its private footage and linked evidence. This affects current player statistics. Exports already downloaded are not deleted.</p><button className="button danger-button" disabled={action.busy} onClick={() => { if (window.confirm(`Permanently delete “${match.title}”, its footage and evidence? This cannot be undone.`)) void action.run(async () => { await api(`/matches/${id}`, { method: 'DELETE' }); window.location.hash = '/matches'; }); }}><Trash2 size={16} />Delete match & private footage</button></Panel>
      </div>}
      {tab === 'review' && <><div className="review-grid"><div className="review-main">
        <Panel title="Evidence player" subtitle={upload ? 'Private footage · timestamps are relative to the source file' : 'Review the source, not a prediction'}>
          <div className="video-shell">{upload && canReview ? <video ref={video} src={localVideoPath(upload.id)} controls preload="metadata" aria-label={`Private match video: ${match.title}`} onError={() => setVideoError('The private video could not be loaded. Refresh the match or check that the file is supported.')} onLoadedMetadata={() => { setVideoError(''); const time = Number(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('t')); if (Number.isFinite(time) && time >= 0) seek(time); }} /> : <div className="video-placeholder"><Pitch decorative /><div><span className="video-play-outline"><Play size={23} /></span><h3>{match.is_synthetic ? 'Synthetic evidence workspace' : 'Bring the match into focus'}</h3><p>{match.is_synthetic ? 'Demo observations only. No real player footage is included.' : 'Link the roster, record permission and upload authorized footage.'}</p>{!match.is_synthetic && <button className="button mint" onClick={() => setTab('setup')}>Set up private footage<ArrowLeft size={15} className="rotate-180" /></button>}</div></div>}</div>
          <ErrorBox message={videoError} /><div className="video-caption"><span><span className="status-dot" />{upload && canReview ? 'Authenticated source video' : 'No accessible video source'}</span><span>Private · not publicly shared</span></div>
        </Panel>
        {jobs.map((job) => <JobCard key={job.id} job={job} busy={action.busy} onRetry={() => void action.run(async () => { await api(`/jobs/${job.id}/retry`, { method: 'POST' }); polls.current = 0; resource.reload(); }, 'Processing retry queued.')} />)}
        {activeJobs && <button className="text-button" onClick={() => { polls.current = 0; resource.reload(); }}>{polls.current >= 120 ? 'Automatic polling paused. Refresh status' : 'Refresh processing status'}</button>}
        <Panel title="Reviewed pitch evidence" subtitle="Only approved events with explicit pitch coordinates appear."><Pitch events={approved} /><p className="footnote pad-inline">No heatmap, calibrated movement or inferred tracking is generated by metadata probing.</p></Panel>
      </div><aside className="review-side">
        <Panel title="Observation desk" subtitle="One actor. One moment. A traceable decision."><div className="pad"><div className="review-principles"><span>01 <strong>Identify the roster actor</strong></span><span>02 <strong>Check source & outcome</strong></span><span>03 <strong>Approve with context</strong></span></div><button className="button primary full" disabled={!canReview} onClick={() => setEditor({ event: null, key: Date.now() })}><Plus size={17} />Add observation</button>{!canReview && <p className="small warning-text">{reviewBlock}</p>}<p className="small muted">Draft and rejected events never contribute to player metrics. You remain the reviewer.</p></div></Panel>
        {editor && canReview && <Panel><EventEditor key={editor.key} match={match} roster={roster} players={players} editing={editor.event} maxTime={upload?.duration_seconds ?? match.duration_seconds} getCurrentTime={() => video.current?.currentTime ?? 0} onCancel={() => setEditor(null)} onSaved={() => { setEditor(null); resource.reload(); }} /></Panel>}
      </aside></div>
      <IdentityReview key={`${id}-${mode}-${events.map((e) => e.updated_at).join()}`} match={match} mode={mode} roster={roster} players={players} events={events} upload={canReview ? upload : null} job={jobs[0]} onSaved={resource.reload} />
      <Panel title="Observation log" subtitle="Corrections update current metrics. Every change is audited." action={<label className="compact-select"><span className="sr-only">Filter review status</span><select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}><option value="all">All decisions</option><option value="approved">Approved</option><option value="draft">Draft</option><option value="rejected">Rejected</option></select></label>}>
        {displayed.length === 0 ? <Empty title="No observations in this view" description="Review an actor and a moment in the footage, then record what actually happened. Only approved events count." /> : <div className="table-scroll"><table><thead><tr><th>Source time</th><th>Actor & event</th><th>Outcome</th><th>Review decision</th><th>Provenance & confidence</th><th>Actions</th></tr></thead><tbody>{displayed.map((event) => <tr key={event.id}>
          <td><button className="timestamp-button" disabled={!upload || !canReview} onClick={() => seek(event.timestamp_seconds)} aria-label={`Seek to ${clock(event.timestamp_seconds)}`}><Play size={12} />{clock(event.timestamp_seconds)}</button>{event.end_timestamp_seconds !== null && <small>to {clock(event.end_timestamp_seconds)}</small>}</td>
          <td><a className="strong-link" href={`#/players/${event.player_id}`}>{players.find((p) => p.id === event.player_id)?.display_name ?? 'Roster player'}</a><small>{humanize(event.kind)}{event.opponent_player_id && ` · vs ${players.find((p) => p.id === event.opponent_player_id)?.display_name ?? 'roster opponent'}`}</small>{event.notes && <details className="event-notes"><summary>Review notes</summary><p>{event.notes}</p></details>}</td>
          <td>{humanize(event.outcome)}</td><td><StatusBadge status={event.status} /><small>{event.reviewed_at ? `Reviewed ${dateLabel(event.reviewed_at)}` : 'Not yet approved'}</small>{event.reviewed_by && <small title={event.reviewed_by}>Reviewer {event.reviewed_by.slice(0, 8)}</small>}</td>
          <td>{humanize(event.provenance)}<small>{event.confidence === null ? 'Model confidence: unavailable' : `Model score: ${percent(event.confidence, true)} · not accuracy`}</small>{event.model_version && <small>{event.model_version}</small>}{event.source_track_id && <small>Source track: {event.source_track_id} · identity revision {event.identity_assignment_revision ?? 'unlinked'}</small>}</td>
          <td><div className="button-row"><button className="icon-button" disabled={!canReview} aria-label={`Edit ${event.kind} at ${clock(event.timestamp_seconds)}`} onClick={() => setEditor({ event, key: Date.now() })}><Pencil size={15} /></button><button className="icon-button danger" disabled={action.busy} aria-label={`Delete ${event.kind} at ${clock(event.timestamp_seconds)}`} onClick={() => { if (window.confirm('Delete this observation? It will be removed from current metrics and the correction will be audited.')) void action.run(async () => { await api(`/events/${event.id}`, { method: 'DELETE' }); if (editor?.event?.id === event.id) setEditor(null); resource.reload(); }, 'Observation removed; current metrics will reflect the correction.'); }}><Trash2 size={15} /></button></div></td>
        </tr>)}</tbody></table></div>}
      </Panel></>}
      {tab === 'audit' && <Panel title="Review history" subtitle="A record of who changed the evidence and when.">{audit.length === 0 ? <Empty title="No changes recorded yet" description="Roster declarations, permission and evidence corrections appear here when recorded by the API." /> : <div className="audit-list">{[...audit].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((entry) => <article key={entry.id}><span className="audit-icon"><History size={17} /></span><div><strong>{humanize(entry.action)} · {humanize(entry.entity_type)}</strong><p>{entry.reason || 'Workspace change recorded.'}</p><small>Account {entry.account_id.slice(0, 8)} · {new Date(entry.created_at).toLocaleString()}</small><details><summary>Change details</summary><div className="audit-diff"><div><h4>Before</h4><pre>{JSON.stringify(entry.before, null, 2) ?? '—'}</pre></div><div><h4>After</h4><pre>{JSON.stringify(entry.after, null, 2) ?? '—'}</pre></div></div></details></div></article>)}</div>}</Panel>}
    </div>
  </>;
}
