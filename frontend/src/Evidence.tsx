import { ArrowUpRight, Info } from 'lucide-react';
import type { Cohort, MetricSummary, RateEstimate } from './types';
import { SYNTHETIC_NOTICE } from './types';
import { cohortLabel, humanize, minutes, percent } from './utils';

/** Displays server-computed uncertainty, never computes or ranks player ability. */
export function IntervalEstimate({ rate, compact = false }: { rate: RateEstimate; compact?: boolean }) {
  const hasInterval = rate.attempts > 0 && rate.credible_low_pct !== null && rate.credible_high_pct !== null && rate.posterior_mean_pct !== null;
  const level = Math.round(rate.credible_mass * 100);
  return <div className={`interval-estimate ${compact ? 'compact' : ''}`}>
    {hasInterval ? <>
      <div className="interval-values"><span>{level}% credible interval</span><strong>{percent(rate.credible_low_pct)} – {percent(rate.credible_high_pct)}</strong></div>
      {!compact && <svg viewBox="0 0 320 40" className="interval-plot" role="img" aria-label={`${level}% credible interval ${percent(rate.credible_low_pct)} to ${percent(rate.credible_high_pct)}; posterior mean ${percent(rate.posterior_mean_pct)}`}>
        <line x1="8" x2="312" y1="12" y2="12" stroke="#e3e9dd" strokeWidth="4" strokeLinecap="round" />
        <line x1={8 + rate.credible_low_pct! * 3.04} x2={8 + rate.credible_high_pct! * 3.04} y1="12" y2="12" stroke="#92bba0" strokeWidth="7" strokeLinecap="round" />
        <circle cx={8 + rate.posterior_mean_pct! * 3.04} cy="12" r="4" fill="#2e6348" stroke="white" strokeWidth="1.5" />
        <text x="8" y="34">0%</text><text x="160" y="34" textAnchor="middle">50%</text><text x="312" y="34" textAnchor="end">100%</text>
      </svg>}
      <p className="posterior-label">Posterior mean <strong>{percent(rate.posterior_mean_pct)}</strong> · prior Beta({rate.prior_alpha}, {rate.prior_beta})</p>
    </> : <p className="small muted">Uncertainty estimate unavailable: no reviewed opportunities.</p>}
    {!compact && <p className="interval-caveat">Conditional on selected reviewed actions and an independence assumption. Not an interval for talent; clustering, selection bias and annotation error are not modeled.</p>}
  </div>;
}

function RateEvidence({ rate, cohort, synthetic }: { rate: RateEstimate; cohort: Cohort; synthetic: boolean }) {
  return <section className="rate-evidence">
    <div className="row-between"><h3>{rate.metric === 'pass_completion' ? 'Pass evidence & uncertainty' : 'Observed duel outcomes'}</h3><span className={`badge ${rate.eligibility === 'eligible' ? 'green' : 'amber'}`}>{humanize(rate.eligibility)}</span></div>
    <p className="cohort-label">{cohortLabel(cohort)}</p>
    {rate.metric === 'duel_success' && <div className="rate-observed"><strong>{percent(rate.observed_pct)}</strong><span>Observed success · {rate.successes} / {rate.attempts} approved attempts</span></div>}
    <IntervalEstimate rate={rate} />
    <dl className="rate-sample"><div><dt>Relevant matches / attempts</dt><dd>{rate.relevant_match_count} / {rate.attempts}</dd></div><div><dt>Largest match share</dt><dd>{percent(rate.max_match_opportunity_share, true)}</dd></div><div><dt>Reviewed time / coverage</dt><dd>{minutes(rate.reviewed_seconds)} / {percent(rate.coverage_fraction, true)}</dd></div><div><dt>Evidence confidence</dt><dd>{humanize(rate.confidence)}</dd></div><div><dt>Observed attempts / 90 reviewed min</dt><dd>{rate.observed_attempts_per_90_reviewed_minutes === null ? '—' : rate.observed_attempts_per_90_reviewed_minutes.toFixed(1)}</dd></div></dl>
    <p className="small muted">Per-90 uses declared review time, not full-match projection. {synthetic && SYNTHETIC_NOTICE}</p>
    {rate.metric === 'duel_success' && rate.eligibility_reasons.length > 0 && <ul className="reason-list">{rate.eligibility_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
  </section>;
}

export function EvidenceDetails({ metric }: { metric: MetricSummary }) {
  if (!metric.pass_rate) return <p className="small muted">Uncertainty estimates are not included in this server response. No additional scores are inferred.</p>;
  return <div className="evidence-details">
    <RateEvidence rate={metric.pass_rate} cohort={metric.cohort} synthetic={metric.is_synthetic} />
    <details className="evidence-disclosure"><summary>Duel evidence, event mix & methodology</summary>
      {metric.duel_rate && <RateEvidence rate={metric.duel_rate} cohort={metric.cohort} synthetic={metric.is_synthetic} />}
      {metric.observed_events && <div className="table-scroll"><table className="event-mix-table"><caption>Approved observed event counts · not inferred possessions</caption><thead><tr><th>Action</th><th>Observed</th><th>Successful</th><th>Unsuccessful</th></tr></thead><tbody>{Object.entries(metric.observed_events).map(([kind, value]) => <tr key={kind}><td>{humanize(kind)}</td><td>{value.attempts}</td><td>{value.successful}</td><td>{value.unsuccessful}</td></tr>)}</tbody></table></div>}
      <div className="methodology-note"><Info size={16} /><div><strong>{metric.methodology_version}</strong><p>{metric.pass_rate.interpretation}</p>{metric.policy && <p>{metric.policy.description}</p>}<p>{metric.exposure_source}</p></div></div>
      {metric.evidence_gaps && <ul className="reason-list">{metric.evidence_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>}
    </details>
    {!!metric.opponent_breakdowns?.length && <details className="evidence-disclosure"><summary>Explicitly linked opponent observations · always unranked</summary><p className="small muted">Directional recorded actions, not opponent-strength adjustment. Pairwise exposure and coverage are unavailable.</p>{metric.opponent_breakdowns.map((pair) => <article key={`${pair.opponent_player_id}:${pair.kind}`} className="opponent-evidence">
      <div className="row-between"><a className="text-link" href={`#/players/${pair.opponent_player_id}`}>{humanize(pair.kind)} vs roster opponent<ArrowUpRight size={13} /></a><span className="badge amber">Unranked</span></div>
      <p className="cohort-label">{cohortLabel(pair.cohort)}</p><p className="small">{percent(pair.observed_success_pct)} observed · {pair.successful} successful / {pair.approved_event_count} approved actions · {pair.match_count} matches</p>
      <p className="small">{Math.round(pair.credible_mass * 100)}% credible interval {percent(pair.credible_low_pct)}–{percent(pair.credible_high_pct)} · prior Beta({pair.prior_alpha}, {pair.prior_beta})</p>
      <p className="small muted">Reviewed pairwise time: unavailable · coverage: unavailable · {pair.confidence} confidence · no rank</p>
      <ul className="reason-list">{pair.eligibility_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
      {pair.is_synthetic && <p className="synthetic-inline">{SYNTHETIC_NOTICE}</p>}
    </article>)}</details>}
  </div>;
}
