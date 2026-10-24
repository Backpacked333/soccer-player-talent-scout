import { ArrowUpRight, Check, ChevronRight, CircleAlert, LoaderCircle, LockKeyhole, ShieldCheck } from 'lucide-react';
import { useId } from 'react';
import { EvidenceDetails } from './Evidence';
import type { ReactNode } from 'react';
import type { EventRead, JobRead, MetricSummary } from './types';
import { SYNTHETIC_NOTICE } from './types';
import { cohortLabel, humanize, minutes, percent } from './utils';

export function Brand({ compact = false }: { compact?: boolean }) {
  return <a className="brand" href="#/" aria-label="Touchline home"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>{!compact && <span>touchline<span className="brand-dot">.</span></span>}</a>;
}
export function DemoNotice() { return <div className="demo-notice" role="note"><span className="badge amber">Synthetic demo</span><span>{SYNTHETIC_NOTICE}</span></div>; }
export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) { return message ? <div className="error-box" role="alert"><CircleAlert size={18} /><span>{message}</span>{retry && <button className="text-button" onClick={retry}>Try again</button>}</div> : null; }
export function Success({ message }: { message: string }) { return message ? <div className="success-box" role="status"><Check size={17} />{message}</div> : null; }
export function Loading({ label = 'Loading workspace…' }: { label?: string }) { return <div className="loading" role="status"><LoaderCircle size={22} className="spin" /><span>{label}</span></div>; }
export function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) { return <header className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action && <div className="heading-action">{action}</div>}</header>; }
export function Panel({ title, subtitle, action, children, className = '' }: { title?: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string }) { return <section className={`panel ${className}`}>{title && <header className="panel-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</header>}{children}</section>; }
export function Empty({ title, description, action, icon }: { title: string; description: string; action?: ReactNode; icon?: ReactNode }) { return <div className="empty-state"><div className="empty-icon">{icon ?? <ShieldCheck size={25} />}</div><h3>{title}</h3><p>{description}</p>{action}</div>; }
export function LinkButton({ href, children, secondary = false }: { href: string; children: ReactNode; secondary?: boolean }) { return <a href={`#${href}`} className={`button ${secondary ? 'secondary' : 'primary'}`}>{children}<ArrowUpRight size={16} /></a>; }
export function Field({ label, children, hint, className = '' }: { label: string; children: ReactNode; hint?: string; className?: string }) { return <label className={`field ${className}`}><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>; }
export function StatusBadge({ status }: { status: string }) { const green = ['ready', 'approved', 'eligible', 'completed', 'probed'].includes(status); const red = ['failed', 'rejected', 'license_blocked'].includes(status); return <span className={`badge ${green ? 'green' : red ? 'red' : ['running', 'queued'].includes(status) ? 'blue' : 'amber'}`}><span className="status-dot" />{humanize(status)}</span>; }
export function PrivateNote() { return <span className="private-note"><LockKeyhole size={13} />Private workspace · authorized access only</span>; }

export function JobCard({ job, onRetry, busy = false }: { job: JobRead; onRetry?: () => void; busy?: boolean }) { return <div className="job-card"><div className="row-between"><strong>{humanize(job.stage || job.status)}</strong><StatusBadge status={job.status} /></div><p>{job.message || 'Waiting for the local worker.'}</p><progress max={1} value={job.progress} aria-label="Processing progress" /><div className="row-between muted small"><span>Attempt {job.attempts} · {Math.round(job.progress * 100)}% of processing stage</span>{onRetry && ['failed', 'needs_configuration'].includes(job.status) && <button className="text-button" disabled={busy} onClick={onRetry}>Retry job <ChevronRight size={13} /></button>}</div>{job.status === 'needs_configuration' && <p className="small">Video metadata is not player analysis. Manual review is available. Optional CPU detection requires operator-reviewed local checkpoint, permissions and security opt-in; it is disabled by default and never approves identities or events.</p>}</div>; }

export function MetricCard({ metric }: { metric: MetricSummary }) { return <article className="metric-evidence"><div className="row-between"><div className="eyebrow">Observed pass completion</div><StatusBadge status={metric.eligibility} /></div><div className="metric-big">{percent(metric.pass_completion_pct)}<small>{metric.successful_passes} / {metric.pass_attempts} attempts</small></div><p className="cohort-label">{cohortLabel(metric.cohort)}</p><dl className="metric-grid"><div><dt>Approved events</dt><dd>{metric.approved_event_count}</dd></div><div><dt>Matches</dt><dd>{metric.match_count}</dd></div><div><dt>Reviewed / played</dt><dd>{minutes(metric.reviewed_seconds)} / {minutes(metric.played_seconds)}</dd></div><div><dt>Declared coverage</dt><dd>{percent(metric.coverage_fraction, true)}</dd></div><div><dt>Evidence confidence</dt><dd>{humanize(metric.confidence)}</dd></div><div><dt>Cohort rank</dt><dd>{metric.rank === null ? 'Unranked' : `#${metric.rank}`}</dd></div></dl>{metric.eligibility_reasons.length > 0 && <ul className="reason-list">{metric.eligibility_reasons.map((r) => <li key={r}>{r}</li>)}</ul>}<EvidenceDetails metric={metric} />{metric.is_synthetic && <p className="synthetic-inline">{SYNTHETIC_NOTICE}</p>}</article>; }

/** Only explicit reviewer-provided pitch coordinates are drawn, never pseudo tracking. */
export function Pitch({ events = [], decorative = false }: { events?: EventRead[]; decorative?: boolean }) {
  const patternId = useId();
  const plotted = events.filter((e) => e.status === 'approved' && e.start_x !== null && e.start_y !== null && Number.isFinite(e.start_x) && Number.isFinite(e.start_y) && e.start_x >= 0 && e.start_x <= 1 && e.start_y >= 0 && e.start_y <= 1);
  return <div className={`pitch-wrap ${decorative ? 'decorative-pitch' : ''}`}>
    <svg className="pitch" viewBox="0 0 600 380" role="img" aria-label={decorative ? 'Illustrated soccer pitch, not player tracking' : `Pitch with ${plotted.length} approved, reviewer-positioned events`}>
      <defs><pattern id={patternId} width="100" height="380" patternUnits="userSpaceOnUse"><rect width="50" height="380" fill="currentColor" opacity=".035" /></pattern></defs>
      <rect x="0" y="0" width="600" height="380" fill={`url(#${patternId})`} />
      <g fill="none" stroke="currentColor" strokeWidth="1.5" opacity=".35"><rect x="24" y="24" width="552" height="332" rx="2" /><path d="M300 24v332 M24 100h90v180H24 M576 100h-90v180h90 M24 148h32v84H24 M576 148h-32v84h32" /><circle cx="300" cy="190" r="49" /><circle cx="300" cy="190" r="2" /><path d="M114 149a50 50 0 0 1 0 82 M486 149a50 50 0 0 0 0 82" /></g>
      {plotted.map((e) => <g key={e.id}><title>{humanize(e.kind)} · {e.timestamp_seconds}s · {humanize(e.outcome)}</title>{e.end_x !== null && e.end_y !== null && <line x1={24 + e.start_x! * 552} y1={24 + e.start_y! * 332} x2={24 + e.end_x * 552} y2={24 + e.end_y * 332} stroke={e.outcome === 'successful' ? '#8ce0bc' : '#edb585'} strokeWidth="2" />}<circle cx={24 + e.start_x! * 552} cy={24 + e.start_y! * 332} r="5" fill={e.outcome === 'successful' ? '#8ce0bc' : '#edb585'} stroke="#173d35" strokeWidth="2" /></g>)}
    </svg>
    {!decorative && plotted.length === 0 && <div className="pitch-empty">No approved pitch coordinates<br /><small>Position mapping is unavailable until reviewed.</small></div>}
  </div>;
}
