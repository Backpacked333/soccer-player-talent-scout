import { useEffect, useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { Activity, ArrowUpRight, ChevronLeft, ChevronRight, Crosshair, Eye, Layers, Maximize, Pause, Play, ScanLine, ShieldCheck, Volume2, VolumeX } from 'lucide-react';
import { api } from './api';
import { useResource } from './hooks';
import { EventValue } from './Knowledge';
import type { Identity, Observation } from './intelligence-types';
import type { EventRead, JobRead, MatchRead, PlayerRead, UploadRead } from './types';
import { humanize, localVideoPath, percent } from './utils';
import { confirmedIdentity, displayBox, displayPoint, sampleAt, sourceTime, trackTrail, validateTimeline } from './video-evidence';
import type { TimelineWindow } from './video-evidence';
import './video-evidence.css';

interface Props {
  videoRef: RefObject<HTMLVideoElement | null>;
  match: MatchRead;
  upload: UploadRead;
  job?: JobRead;
  identities: Identity[];
  players: PlayerRead[];
  events: EventRead[];
  onReviewTrack: (observation: Observation) => void;
}

const colors: Record<string, string> = { player: '#a6f7ce', goalkeeper: '#d7bcff', referee: '#8fcdff', ball: '#f5d77a' };
const trackLabel = (id: string) => id.split(':').at(-1) ?? id;

export function VideoEvidence({ videoRef, match, upload, job, identities, players, events, onReviewTrack }: Props) {
  const room = useRef<HTMLElement>(null);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [seeking, setSeeking] = useState(false);
  const [ready, setReady] = useState(false);
  const [presentationAspect, setPresentationAspect] = useState<number | null>(null);
  const [muted, setMuted] = useState(true);
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'tracks' | 'events' | 'pipeline'>('tracks');
  const [selected, select] = useState<string | null>(null);
  const [eventId, setEventId] = useState<string | null>(null);
  const [boxes, setBoxes] = useState(true);
  const [trails, setTrails] = useState(true);
  const [hold, setHold] = useState(true);
  const [threshold, setThreshold] = useState(0);
  const [classFilter, setClassFilter] = useState('all');
  const duration = upload.duration_seconds ?? 0;
  const fps = upload.fps && upload.fps > 0 ? upload.fps : 30;
  const cadence = job?.processing_coverage?.sample_interval_seconds ?? null;
  const windowStart = Math.max(0, Math.floor(time / 10) * 10 - 5);
  const windowEnd = Math.min(duration, Math.floor(time / 10) * 10 + 15);
  const evidence = useResource(async (signal) => {
    if (!job || windowEnd <= windowStart) return null;
    const result = await api<TimelineWindow>(`/jobs/${job.id}/timeline?start=${windowStart}&end=${windowEnd}`, { signal });
    return validateTimeline(result, job.id, upload.id, match.id, match.is_synthetic, windowStart, windowEnd);
  }, `${job?.id}:${job?.updated_at}:${upload.id}:${windowStart}:${windowEnd}`);
  const geometry = evidence.data?.display_geometry;
  const aligned = !!geometry && presentationAspect !== null && Math.abs(geometry.aspect_ratio / presentationAspect - 1) < .01;
  const aspect = presentationAspect ?? geometry?.aspect_ratio ?? (upload.width || 16) / (upload.height || 9);
  const imageHeight = 1000 / aspect;
  const items = useMemo(() => !evidence.loading && !evidence.error && !evidence.data?.truncated ? evidence.data?.items ?? [] : [], [evidence.loading, evidence.error, evidence.data]);
  const sample = sampleAt(seeking || !ready || error || !aligned ? [] : items, time, fps, cadence, hold);
  const visible = sample.items.filter((row) => row.confidence >= threshold && (classFilter === 'all' || row.label === classFilter));
  const selectedRow = visible.find((row) => row.track_id === selected);
  const identity = selectedRow ? confirmedIdentity(selectedRow, identities, upload.id) : null;
  const player = identity ? players.find((row) => row.id === identity.player_id) : null;
  const trail = selectedRow && trails ? trackTrail(items, selectedRow, cadence) : [];
  const sortedEvents = useMemo(() => [...events].sort((a, b) => a.timestamp_seconds - b.timestamp_seconds), [events]);
  const nearEvents = sortedEvents.filter((row) => Math.abs(row.timestamp_seconds - time) <= 2);
  const selectedEvent = events.find((row) => row.id === eventId);
  const segment = evidence.data?.segments.find((row) => row.start_seconds <= time && time < row.end_seconds);
  const sampleTimes = useMemo(() => [...new Set(items.map((row) => row.timestamp_seconds))].sort((a, b) => a - b), [items]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !video.requestVideoFrameCallback) return;
    let handle = 0;
    const frame: VideoFrameRequestCallback = (_now, metadata) => {
      setTime(metadata.mediaTime);
      handle = video.requestVideoFrameCallback(frame);
    };
    handle = video.requestVideoFrameCallback(frame);
    return () => video.cancelVideoFrameCallback(handle);
  }, [videoRef]);

  function seek(seconds: number) {
    const video = videoRef.current;
    if (!video || !ready) return;
    const next = Math.max(0, Math.min(duration, seconds));
    video.currentTime = next;
    setTime(next);
  }
  async function togglePlayback() {
    const video = videoRef.current;
    if (!video) return;
    try { if (video.paused) await video.play(); else video.pause(); }
    catch { setError('Playback could not start. Check that the source is available and supported.'); }
  }
  const nameFor = (row: Observation) => {
    const confirmed = confirmedIdentity(row, identities, upload.id);
    return confirmed ? players.find((p) => p.id === confirmed.player_id)?.display_name ?? 'Confirmed roster identity' : `${humanize(row.label)} · ${trackLabel(row.track_id)}`;
  };
  const currentStatus = !job ? 'No detector job' : evidence.error ? 'Evidence unavailable' : evidence.loading ? 'Loading source evidence' : !aligned ? 'Overlay geometry unavailable' : evidence.data?.truncated ? 'Evidence window too dense' : sample.items.length ? sample.held ? 'Last observed sample' : 'Observed source sample' : segment?.status === 'completed' ? 'No boxes at this moment' : 'No completed sample here';

  return <section className="evidence-room" ref={room} aria-label="Video analysis workspace">
    <header className="ev-heading"><div><span className="ev-kicker"><ScanLine size={14} /> SOURCE / EVIDENCE</span><h2>Inside the match.</h2><p>Every overlay has a source. Every unknown stays visible.</p></div><span className="ev-private"><ShieldCheck size={14} />Private review</span></header>
    <div className="ev-layout"><div className="ev-cinema">
      <div className="ev-stage" style={{ aspectRatio: aspect }}>
        <video ref={videoRef} src={localVideoPath(upload.id)} preload="metadata" playsInline muted={muted} aria-label={`Private match video: ${match.title}`}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
          onSeeking={() => setSeeking(true)} onSeeked={() => { setSeeking(false); if (videoRef.current) setTime(videoRef.current.currentTime); }}
          onTimeUpdate={() => { const video = videoRef.current; if (video && !video.requestVideoFrameCallback) setTime(video.currentTime); }}
          onError={() => { setError('The private video could not be loaded. Refresh or check the source format and permission.'); setReady(false); }}
          onLoadedMetadata={() => {
            setReady(true); setError('');
            const video = videoRef.current;
            setPresentationAspect(video && video.videoWidth > 0 && video.videoHeight > 0 ? video.videoWidth / video.videoHeight : null);
            const target = Number(new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('t'));
            if (videoRef.current && Number.isFinite(target) && target >= 0) { videoRef.current.currentTime = Math.min(duration, target); setTime(Math.min(duration, target)); }
          }} />
        <svg className="ev-overlay" viewBox={`0 0 1000 ${imageHeight}`} aria-label="Source image observations">
          {boxes && geometry && trail.length > 1 && <polyline className="ev-trail" points={trail.map((row) => { const [x, y] = displayPoint((row.bbox[0] + row.bbox[2]) / 2, row.bbox[3], geometry); return `${x * 1000},${y * imageHeight}`; }).join(' ')} />}
          {boxes && visible.map((row) => {
            const [x1, y1, x2, y2] = geometry ? displayBox(row.bbox, geometry) : row.bbox;
            const confirmed = confirmedIdentity(row, identities, upload.id);
            return <g key={row.id} role="button" tabIndex={0} aria-label={`Inspect ${nameFor(row)}`} aria-pressed={selected === row.track_id} className={`ev-box ${selected === row.track_id ? 'selected' : ''}`} style={{ color: colors[row.label] ?? '#fff' }} onClick={() => { select(row.track_id); setTab('tracks'); }} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(row.track_id); setTab('tracks'); } }}>
              <title>{nameFor(row)} · detector score {percent(row.confidence, true)} · sample {sourceTime(row.timestamp_seconds)} · {confirmed ? 'reviewer-confirmed identity' : 'anonymous track'}</title>
              <rect x={x1 * 1000} y={y1 * imageHeight} width={(x2 - x1) * 1000} height={(y2 - y1) * imageHeight} strokeDasharray={sample.held || !confirmed ? '5 4' : undefined} />
              <text x={Math.min(x1 * 1000, 760)} y={Math.max(22, y1 * imageHeight - 9)}>{nameFor(row).slice(0, 26)} · {Math.round(row.confidence * 100)}</text>
            </g>;
          })}
        </svg>
        <div className="ev-stage-top"><span className={`ev-sample-state ${sample.held ? 'held' : ''}`}><span />{currentStatus}</span><span className="ev-timecode">{sourceTime(time)}</span></div>
        <div className="ev-stage-bottom"><span>{sample.timestamp !== null ? `Sample ${sourceTime(sample.timestamp)} · ${sample.age!.toFixed(2)}s old${sample.held ? ' · held position, not interpolated' : ''}` : 'No positional claim for this frame'}</span><span>{visible.length} shown / {sample.items.length} stored boxes</span></div>
        {!ready && !error && <div className="ev-awaiting"><ScanLine size={32} /><span>Loading private source…</span></div>}
      </div>
      {ready && evidence.data && !evidence.loading && !evidence.error && !aligned && <p className="ev-error" role="alert">Boxes and trails are hidden: source orientation / pixel geometry is unknown or differs from the browser presentation. Reprocess legacy metadata, or upload a normalized export. No positional claims are made.</p>}
      {error && <p className="ev-error" role="alert">{error}</p>}
      {evidence.error && <p className="ev-error" role="alert">{evidence.error} <button onClick={evidence.reload}>Retry evidence</button></p>}
      {evidence.data?.truncated && <p className="ev-error" role="alert">This window exceeds 5,000 observations. Overlays are hidden rather than presenting an incomplete set.</p>}
      <div className="ev-controls">
        <button className="ev-play" disabled={!ready} onClick={() => void togglePlayback()} aria-label={playing ? 'Pause video' : 'Play video'}>{playing ? <Pause size={19} /> : <Play size={19} />}</button>
        <button disabled={!ready} aria-label="Previous video frame" onClick={() => { videoRef.current?.pause(); seek(time - 1 / fps); }}><ChevronLeft size={18} /></button>
        <button disabled={!ready} aria-label="Next video frame" onClick={() => { videoRef.current?.pause(); seek(time + 1 / fps); }}><ChevronRight size={18} /></button>
        <span className="ev-control-time">{sourceTime(time)} <span>/ {sourceTime(duration)}</span></span>
        <label className="ev-speed"><span className="sr-only">Playback speed</span><select defaultValue="1" onChange={(e) => { if (videoRef.current) videoRef.current.playbackRate = Number(e.target.value); }}>{[0.25, 0.5, 1, 1.5, 2].map((rate) => <option key={rate} value={rate}>{rate}×</option>)}</select></label>
        <button aria-label={muted ? 'Unmute video' : 'Mute video'} onClick={() => setMuted(!muted)}>{muted ? <VolumeX size={17} /> : <Volume2 size={17} />}</button>
        <button aria-label="Expand analysis workspace" onClick={() => { void room.current?.requestFullscreen?.().catch(() => setError('Fullscreen is not available in this browser.')); }}><Maximize size={17} /></button>
      </div>
      <div className="ev-timeline">
        <div className="ev-timeline-label"><strong>Source timeline</strong><span>File time, not match clock</span></div>
        <div className="ev-coverage-track" aria-label="Detector processing segments">{evidence.data?.segments.map((row, i) => <span key={i} className={`ev-segment ${row.status}`} style={{ left: `${row.start_seconds / duration * 100}%`, width: `${(row.end_seconds - row.start_seconds) / duration * 100}%` }} title={`${sourceTime(row.start_seconds)}–${sourceTime(row.end_seconds)}: ${humanize(row.status)} · sampled source only`} />)}</div>
        <input className="ev-scrubber" aria-label="Source playhead in seconds" type="range" min={0} max={duration} step={1 / fps} value={Math.min(time, duration)} disabled={!ready} onChange={(e) => seek(Number(e.target.value))} />
        <div className="ev-event-lane" aria-label="Timestamped event markers">{sortedEvents.filter((row) => row.timestamp_seconds <= duration).map((row) => <button key={row.id} className={`ev-event-pin ${row.status}`} style={{ left: `${Math.min(99, row.timestamp_seconds / duration * 100)}%` }} aria-label={`Inspect ${row.status} ${row.kind} at ${sourceTime(row.timestamp_seconds)}`} title={`${humanize(row.kind)} · ${row.status} · ${sourceTime(row.timestamp_seconds)}`} onClick={() => { setEventId(row.id); setTab('events'); seek(row.timestamp_seconds); }} />)}</div>
        <div className="ev-ticks">{[0, .25, .5, .75, 1].map((fraction) => <span key={fraction}>{sourceTime(duration * fraction).split('.')[0]}</span>)}</div>
        <div className="ev-legend"><span><i className="completed" />Sampled segment</span><span><i className="failed" />Failed segment</span><span><i className="draft" />Draft event</span><span><i className="approved" />Reviewed event</span><span><i className="rejected" />Rejected event</span></div>
      </div>
      <div className="ev-layer-bar"><span><Layers size={15} />Layers</span><label><input type="checkbox" checked={boxes} onChange={(e) => setBoxes(e.target.checked)} />Boxes</label><label><input type="checkbox" checked={trails} onChange={(e) => setTrails(e.target.checked)} />Selected trail</label><label><input type="checkbox" checked={hold} onChange={(e) => setHold(e.target.checked)} />Hold recent sample ≤0.5s</label></div>
      <p className="ev-disclosure">Dashed boxes are anonymous or held samples. Trails connect observed image positions, not calibrated pitch movement. Camera motion, cuts and occlusions can break association. Hidden or missed players are not absent players.</p>
    </div>
    <aside className="ev-inspector" aria-label="Evidence inspector">
      <div className="ev-inspector-heading"><Crosshair size={17} /><strong>Evidence inspector</strong><span>Not a prediction</span></div>
      <div className="ev-tabs" role="tablist" aria-label="Evidence inspector views">{(['tracks', 'events', 'pipeline'] as const).map((value) => <button key={value} id={`ev-tab-${value}`} role="tab" aria-selected={tab === value} aria-controls="ev-inspector-panel" onClick={() => setTab(value)}>{value === 'tracks' ? 'Tracks' : value === 'events' ? 'Events' : 'Models'}</button>)}</div>
      <div className="ev-inspector-body" id="ev-inspector-panel" role="tabpanel" aria-labelledby={`ev-tab-${tab}`}>
        {tab === 'tracks' && <>
          <div className="ev-section-title"><span>AT THE PLAYHEAD</span><strong>{visible.length.toString().padStart(2, '0')} tracks</strong></div>
          <div className="ev-filters"><label>Class<select value={classFilter} onChange={(e) => setClassFilter(e.target.value)}><option value="all">All classes</option>{Object.keys(colors).map((label) => <option key={label} value={label}>{humanize(label)}</option>)}</select></label><label>Min. detector score<select value={threshold} onChange={(e) => setThreshold(Number(e.target.value))}>{[0, .5, .75, .9].map((value) => <option value={value} key={value}>{value === 0 ? 'All stored scores' : `${value * 100}%`}</option>)}</select></label></div>
          <div className="ev-track-list">{visible.map((row) => <button key={row.id} aria-pressed={selected === row.track_id} onClick={() => select(row.track_id)}><i style={{ background: colors[row.label] }} /><span><strong>{nameFor(row)}</strong><small>{confirmedIdentity(row, identities, upload.id) ? 'Reviewer-confirmed identity' : 'Anonymous · identity unresolved'}</small></span><b>{percent(row.confidence, true)}</b></button>)}</div>
          {!visible.length && <div className="ev-empty"><Eye size={24} /><strong>{currentStatus}</strong><p>{sample.items.length ? 'Current filters hide these observations.' : 'Seek to a stored sample to inspect its raw evidence. No positions or identities are invented between observations.'}</p><div className="ev-sample-jumps"><button disabled={!sampleTimes.some((t) => t < time - .001)} onClick={() => { videoRef.current?.pause(); seek([...sampleTimes].reverse().find((t) => t < time - .001) ?? time); }}>Previous observed sample</button><button disabled={!sampleTimes.some((t) => t > time + .001)} onClick={() => { videoRef.current?.pause(); seek(sampleTimes.find((t) => t > time + .001) ?? time); }}>Next observed sample</button></div><small>Within the loaded {sourceTime(windowStart)}–{sourceTime(windowEnd)} window.</small></div>}
          {selectedRow ? <div className="ev-track-detail"><span className="ev-kicker">SELECTED SOURCE TRACK</span><h3>{player?.display_name ?? nameFor(selectedRow)}</h3><p className="ev-identity-status">{identity ? `Reviewer-confirmed · revision ${identity.revision}` : 'Unresolved identity — not a player profile'}</p><dl><div><dt>Detector score</dt><dd>{percent(selectedRow.confidence, true)} <small>not identity confidence or accuracy</small></dd></div><div><dt>Sample time / age</dt><dd>{sourceTime(selectedRow.timestamp_seconds)} / {sample.age?.toFixed(2)}s</dd></div><div><dt>Model version</dt><dd>{selectedRow.model_version}</dd></div><div><dt>Source track</dt><dd>{selectedRow.track_id}</dd></div><div><dt>Observation ID</dt><dd>{selectedRow.id}</dd></div><div><dt>Image box</dt><dd>{selectedRow.bbox.map((n) => n.toFixed(4)).join(' · ')}</dd></div><div><dt>Trail</dt><dd>{trail.length} stored samples · image-space only</dd></div></dl>{identity && <p className="ev-reason">{identity.reason}<small>Reviewer {identity.reviewed_by} · {identity.reviewed_at}</small></p>}{player && <a className="ev-link" href={`#/players/${player.id}`}>Open persistent player record<ArrowUpRight size={15} /></a>}<button className="ev-action" disabled={selectedRow.label !== 'player'} onClick={() => onReviewTrack(selectedRow)}>Review track identity<ArrowUpRight size={15} /></button></div> : selected && <p className="ev-disclosure">Selected track is not observed at this playhead. Its last position is not carried forward.</p>}
        </>}
        {tab === 'events' && <>
          <div className="ev-section-title"><span>NEAR THIS MOMENT · ±2s</span><strong>{nearEvents.length}</strong></div>
          {!nearEvents.length && <p className="ev-disclosure">No recorded events near this moment. That is not proof that nothing happened.</p>}
          <div className="ev-event-list">{nearEvents.map((row) => <button key={row.id} onClick={() => setEventId(row.id)} aria-pressed={eventId === row.id}><span>{sourceTime(row.timestamp_seconds)}</span><strong>{humanize(row.kind)}</strong><small>{row.status} · {humanize(row.provenance)}</small></button>)}</div>
          {selectedEvent ? <div className="ev-track-detail"><span className="ev-kicker">SELECTED EVENT · {selectedEvent.status.toUpperCase()}</span><h3>{humanize(selectedEvent.kind)} · {humanize(selectedEvent.outcome)}</h3><p>{players.find((p) => p.id === selectedEvent.player_id)?.display_name ?? 'Roster player'} at {sourceTime(selectedEvent.timestamp_seconds)}</p><blockquote>{selectedEvent.notes || 'No reviewer notes recorded.'}</blockquote><dl><div><dt>Provenance</dt><dd>{humanize(selectedEvent.provenance)}</dd></div><div><dt>Model</dt><dd>{selectedEvent.model_version ?? 'No model attribution'}</dd></div><div><dt>Review</dt><dd>{selectedEvent.reviewed_at ?? 'Not reviewed'}<small>{selectedEvent.reviewed_by}</small></dd></div><div><dt>Evidence ID</dt><dd>{selectedEvent.id}</dd></div></dl><button className="ev-action" onClick={() => seek(selectedEvent.timestamp_seconds)}>Return to event timestamp<ChevronRight size={15} /></button><EventValue key={selectedEvent.id} eventId={selectedEvent.id} /></div> : <div className="ev-empty"><Activity size={24} /><strong>Select an event marker</strong><p>Read the actual notes, attribution and review decision. Draft and rejected events do not count toward statistics.</p></div>}
        </>}
        {tab === 'pipeline' && <>
          <span className="ev-kicker">WHAT HAS ACTUALLY RUN</span><h3>Evidence, not effects.</h3>
          <ol className="ev-model-stages"><li><b>01</b><div><strong>Source validation</strong><span>{upload.width} × {upload.height} · {upload.fps ?? 'unknown'} fps · {sourceTime(duration)}</span></div><em>Probed</em></li><li><b>02</b><div><strong>Detection & tracking</strong><span>{job ? `${humanize(job.status)} · ${humanize(job.stage)}` : 'No processing job available'}</span><span>{cadence !== null ? `One sampled frame every ${cadence}s` : 'Sampling cadence unavailable'}</span></div><em>{job?.processing_coverage?.sampled_frames ?? 0} samples</em></li><li><b>03</b><div><strong>Player identity</strong><span>Roster + explicit reviewer decision. Not face recognition.</span></div><em>Human review</em></li><li><b>04</b><div><strong>Event interpretation</strong><span>Automatic soccer-event extraction is not implemented.</span></div><em>Manual</em></li><li><b>05</b><div><strong>Player statistics</strong><span>Only approved evidence contributes; histories live in the player record.</span></div><em>{events.filter((e) => e.status === 'approved').length} approved</em></li></ol>
          <div className="ev-coverage-summary"><strong>{job?.processing_coverage?.completed_chunks ?? 0} / {job?.processing_coverage?.planned_chunks ?? 0}</strong><span>completed source segments</span><p>{job?.message ?? 'Upload validation is not player analysis.'}</p></div>
          <h4>Not available from this pipeline</h4><div className="ev-unavailable"><span>Speed / distance</span><span>Calibrated pitch tracking</span><span>Ball possession</span><span>Automatic jersey identity</span><span>Future talent prediction</span></div>
          <p className="ev-disclosure">Detector coverage is not reviewed-event coverage. Sampling can miss brief actions. These are persisted outputs, not a live model stream or access to hidden model reasoning.</p>
          <details className="ev-raw"><summary>Inspect raw evidence for this window</summary><p>{sourceTime(windowStart)}–{sourceTime(windowEnd)} · {items.length} observations. First 10 shown.</p><pre>{JSON.stringify(items.slice(0, 10), null, 2)}</pre></details>
        </>}
      </div>
    </aside></div>
    <footer className="ev-footer"><span><ShieldCheck size={13} />Model output → reviewer decision → player record</span><span>Detection ≠ identity ≠ event understanding</span></footer>
  </section>;
}
