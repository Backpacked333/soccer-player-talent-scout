import { useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowUpRight, ChartNoAxesCombined, CircleSlash2, Filter, Info, ShieldCheck } from 'lucide-react';
import { IntervalEstimate } from './Evidence';
import { DuelEvidence } from './Knowledge';
import { useApi } from './hooks';
import type { LeaderboardResponse, MatchRead, Mode } from './types';
import { ROLES, SYNTHETIC_NOTICE } from './types';
import { Empty, ErrorBox, Field, Loading, PageHeading, Panel, StatusBadge } from './ui';
import { cohortLabel, humanize, inMode, initials, minutes, percent } from './utils';

function ComparisonResults({ query, mode }: { query: string; mode: Mode }) {
  const resource = useApi<LeaderboardResponse>(`/leaderboard?${query}`);
  if (!resource.data) return resource.loading ? <Loading label="Building the cohort comparison…" /> : <ErrorBox message={resource.error} retry={resource.reload} />;
  const data = resource.data;
  const entries = inMode(data.entries, mode);
  const policy = data.policy;
  const uncertaintyRanking = policy.ranking_metric === 'pass_completion_posterior_lower_95';
  if (data.mode !== mode || data.is_synthetic !== (mode === 'demo')) return <ErrorBox message="The server returned a different data mode. No comparison is displayed." />;
  return <>
    <div className="comparison-summary"><div><span className="eyebrow">Your selected cohort</span><h2>{cohortLabel(data.cohort)}</h2><p>Observed successful-pass outcomes · not talent, potential or opponent-adjusted performance</p></div><span className="count-label">{entries.length} players</span></div>
    <Panel title="Within-cohort comparison" subtitle="Rank is withheld until the evidence meets the declared policy.">
      <div className="policy-strip"><ShieldCheck size={17} /><span>Eligibility: <strong>{policy.min_matches} relevant matches</strong> · <strong>{policy.min_pass_attempts} pass attempts</strong> · <strong>{minutes(policy.min_reviewed_seconds)} reviewed</strong> · <strong>{percent(policy.min_coverage_fraction, true)} coverage</strong>{policy.max_match_opportunity_share !== undefined && <> · <strong>≤{percent(policy.max_match_opportunity_share, true)} from one match</strong></>}</span></div>
      <div className="comparison-method"><strong>{uncertaintyRanking ? 'Order: lower endpoint of the 95% pass-completion credible interval' : 'Order: observed pass completion (legacy server policy)'}</strong><p>{policy.description}</p>{policy.prior_alpha !== undefined && policy.prior_beta !== undefined && <p>Disclosed prior: Beta({policy.prior_alpha}, {policy.prior_beta}). Confidence labels indicate evidence sufficiency, not validated accuracy. Eligibility thresholds are engineering safeguards, not validated scouting science.</p>}<small>Methodology: {data.methodology_version ?? policy.methodology_version ?? 'Not supplied by this server'}</small></div>
      {entries.length === 0 ? <Empty icon={<ChartNoAxesCombined size={25} />} title="No evidence in this cohort yet" description="Try a cohort represented in your match library. Observations must be approved, with roster-declared coverage, before they support a comparison." /> : <div className="table-scroll"><table className="comparison-table"><thead><tr><th scope="col">Rank</th><th scope="col">Player</th><th scope="col">Observed completion & uncertainty</th><th scope="col">Evidence sample</th><th scope="col">Reviewed / played</th><th scope="col">Coverage</th><th scope="col">Eligibility & confidence</th></tr></thead><tbody>{entries.map((row) => {
        const ranked = row.eligibility === 'eligible' && row.rank !== null;
        return <tr key={row.player_id}>
          <td><span className={`rank ${ranked ? '' : 'no-rank'}`}>{ranked ? String(row.rank).padStart(2, '0') : '—'}</span><small>{ranked ? 'Within cohort' : 'Unranked'}</small></td>
          <td><a href={`#/players/${row.player_id}`} className="player-cell"><span className="player-avatar small-avatar">{initials(row.display_name)}</span><span><strong>{row.display_name}</strong><small>{row.team_name || 'Team unspecified'}</small></span><ArrowUpRight size={14} /></a><small>{cohortLabel(row.cohort)}</small>{row.is_synthetic && <small>Synthetic demonstration</small>}</td>
          <td><strong className="table-score">{percent(row.pass_completion_pct)}</strong><small>{row.successful_passes} successful / {row.pass_attempts} attempts</small>{row.pass_rate ? <IntervalEstimate rate={row.pass_rate} compact /> : <small>Uncertainty not supplied</small>}{uncertaintyRanking && <small>Lower endpoint for ordering: {ranked ? percent(row.comparison_score ?? null) : 'withheld'}</small>}</td>
          <td><strong>{row.pass_rate?.relevant_match_count ?? row.match_count} relevant matches</strong><small>{row.approved_event_count} approved events</small>{row.pass_rate && <small>Largest match share: {percent(row.pass_rate.max_match_opportunity_share, true)}</small>}</td>
          <td>{minutes(row.reviewed_seconds)} / {minutes(row.played_seconds)}</td>
          <td>{percent(row.coverage_fraction, true)}<small>Reviewer-declared</small></td>
          <td><StatusBadge status={row.eligibility} /><small>{humanize(row.confidence)} confidence</small>{row.eligibility_reasons.length > 0 && <details><summary>Why {humanize(row.eligibility).toLowerCase()}?</summary><ul>{row.eligibility_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></details>}</td>
        </tr>;
      })}</tbody></table></div>}
      {data.is_synthetic && <p className="pad-inline synthetic-inline">{SYNTHETIC_NOTICE}</p>}
    </Panel>
    <p className="footnote">{data.disclaimer}</p>
    {data.cohort.season && <DuelEvidence query={new URLSearchParams({ mode, season: data.cohort.season, age_group: data.cohort.age_group, competition: data.cohort.competition, role: data.cohort.role }).toString()} mode={mode} />}
    <Panel title="Not measured is not zero" subtitle="These dimensions remain unavailable until their evidence and validated methods exist."><div className="unavailable-grid">{data.unavailable_metrics.map((metric) => <div key={metric}><CircleSlash2 size={16} /><span>{metric}</span><span className="badge neutral">Unavailable</span></div>)}</div></Panel>
  </>;
}

export function Leaderboard({ mode }: { mode: Mode }) {
  const matches = useApi<MatchRead[]>(`/matches?mode=${mode}`);
  const [query, setQuery] = useState('');
  const rows = inMode(matches.data ?? [], mode);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const params = new URLSearchParams({ mode, age_group: String(data.get('age_group')), role: String(data.get('role')), competition: String(data.get('competition')) });
    if (data.get('season')) params.set('season', String(data.get('season')));
    setQuery(params.toString());
  };
  return <>
    <PageHeading eyebrow="Comparisons, with boundaries" title="Context is the whole point." description="Compare observed pass outcomes within one age group, role and competition. Never a universal talent rank." />
    <div className="info-strip"><Info size={18} /><span>A higher completion rate is not proof of a better player. Role, risk, opportunity and the strength of the evidence matter.</span></div>
    <Panel title="Define a fair comparison" subtitle="Choose your cohort before viewing any rankings." className="form-panel">
      <ErrorBox message={matches.error} retry={matches.reload} />
      {matches.loading ? <Loading /> : <form className="cohort-form" onSubmit={submit}>
        <Field label="Age group"><select name="age_group" required><option value="">Select age group…</option>{[...new Set(rows.map((match) => match.age_group))].sort().map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="Appearance role"><select name="role" defaultValue="midfielder" required>{ROLES.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}</select></Field>
        <Field label="Competition"><select name="competition" required><option value="">Select competition…</option>{[...new Set(rows.map((match) => match.competition))].sort().map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <Field label="Season"><select name="season"><option value="">All seasons (explicit)</option>{[...new Set(rows.map((match) => match.season))].sort().map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
        <button className="button primary" disabled={rows.length === 0}><Filter size={16} />Compare cohort</button>
      </form>}
    </Panel>
    {query ? <ComparisonResults key={query} query={query} mode={mode} /> : <Panel><Empty icon={<ChartNoAxesCombined size={27} />} title={rows.length ? 'Same context. More meaningful evidence.' : 'A comparison begins with match evidence.'} description={rows.length ? 'Select a cohort above. Small or incomplete samples are shown as provisional or unranked, never dressed up as a definitive score.' : 'Add authorized match rosters and reviewed observations before comparing players.'} /></Panel>}
  </>;
}
