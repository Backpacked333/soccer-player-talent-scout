import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { EvidenceDetails, IntervalEstimate } from './Evidence';
import { MetricCard } from './ui';
import { testMetric, testRate } from './test-fixtures';
import type { OpponentBreakdown } from './types';

// Assertions intentionally use server-supplied fixture values, not a browser estimator.
describe('uncertainty and evidence transparency', () => {
  it('displays supplied credible bounds, posterior mean, prior and limitations', () => {
    render(<IntervalEstimate rate={testRate} />);
    expect(screen.getByRole('img', { name: '95% credible interval 28.4% to 94.7%; posterior mean 66.7%' })).toBeVisible();
    expect(screen.getByText('28.4% – 94.7%')).toBeVisible();
    expect(screen.getByText(/prior Beta\(1, 1\)/)).toBeVisible();
    expect(screen.getByText(/clustering, selection bias and annotation error are not modeled/)).toBeVisible();
  });
  it('does not expose a prior-only score or interval when no actions were observed', () => {
    render(<IntervalEstimate rate={{ ...testRate, attempts: 0, successes: 0, failures: 0, posterior_mean_pct: 50, credible_low_pct: 2.5, credible_high_pct: 97.5 }} />);
    expect(screen.getByText(/Uncertainty estimate unavailable: no reviewed opportunities/)).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.queryByText('50.0%')).not.toBeInTheDocument();
  });
  it('keeps contextual evidence around the interval and discloses methodology', async () => {
    const user = userEvent.setup();
    render(<MetricCard metric={{ ...testMetric, pass_rate: testRate, methodology_version: testRate.methodology_version, observed_events: { pass: { attempts: 4, successful: 3, unsuccessful: 1 } }, evidence_gaps: ['No opponent-strength adjustment'], exposure_source: 'Reviewer-declared review time; not inferred visibility' }} />);
    for (const text of ['Relevant matches / attempts', '1 / 4', 'Largest match share', '100.0%', 'Reviewed time / coverage', 'Limited', 'Observed attempts / 90 reviewed min']) expect(screen.getByText(text)).toBeVisible();
    await user.click(screen.getByText('Duel evidence, event mix & methodology'));
    expect(screen.getByText(testRate.methodology_version)).toBeVisible();
    expect(screen.getByText(/not a population percentile or a forecast/)).toBeVisible();
    expect(screen.getByText(/not inferred visibility/)).toBeVisible();
    expect(screen.getByRole('table')).toHaveTextContent('Pass43' + '1');
    expect(screen.getByText('No opponent-strength adjustment')).toBeVisible();
  });
  it('keeps explicitly linked opponent observations unranked with exposure unavailable', async () => {
    const user = userEvent.setup();
    const pair: OpponentBreakdown = { actor_player_id: 'player-test', opponent_player_id: 'opponent-test', kind: 'duel', cohort: testMetric.cohort, is_synthetic: true, approved_event_count: 2, match_count: 1, successful: 1, unsuccessful: 1, observed_success_pct: 50, posterior_mean_pct: 50, credible_low_pct: 9.43, credible_high_pct: 90.57, prior_alpha: 1, prior_beta: 1, credible_mass: 0.95, reviewed_seconds: null, coverage_fraction: null, eligibility: 'unranked', confidence: 'insufficient', rank: null, comparison_score: null, event_ids: ['event-1', 'event-2'], methodology_version: testRate.methodology_version, eligibility_reasons: ['Pairwise exposure is unavailable'] };
    render(<EvidenceDetails metric={{ ...testMetric, pass_rate: testRate, opponent_breakdowns: [pair] }} />);
    await user.click(screen.getByText('Explicitly linked opponent observations · always unranked'));
    expect(screen.getByText('Unranked')).toBeVisible();
    expect(screen.getByText(/50.0% observed · 1 successful \/ 2 approved actions · 1 matches/)).toBeVisible();
    expect(screen.getByText(/Reviewed pairwise time: unavailable · coverage: unavailable/)).toBeVisible();
    expect(screen.getByRole('link', { name: /Duel vs roster opponent/ })).toHaveAttribute('href', '#/players/opponent-test');
    expect(screen.getByText(/Directional recorded actions, not opponent-strength adjustment/)).toBeVisible();
  });
  it('does not invent uncertainty for a legacy response', () => {
    render(<EvidenceDetails metric={testMetric} />);
    expect(screen.getByText(/Uncertainty estimates are not included in this server response/)).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
  });
});
