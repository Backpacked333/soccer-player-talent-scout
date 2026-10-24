import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { Leaderboard } from './Leaderboard';
import type { LeaderboardResponse } from './types';
import { SYNTHETIC_NOTICE } from './types';
import { testMatch, testMetric, testPlayer, testRate } from './test-fixtures';

vi.mock('./api', () => ({ api: vi.fn() }));
beforeEach(() => { vi.mocked(api).mockReset(); });
const result: LeaderboardResponse = {
  cohort: testMetric.cohort, mode: 'demo', is_synthetic: true,
  policy: { min_matches: 3, min_pass_attempts: 30, min_reviewed_seconds: 1800, min_coverage_fraction: 0.5, ranking_metric: 'pass_completion_posterior_lower_95', min_duel_attempts: 20, max_match_opportunity_share: 0.6, prior_alpha: 1, prior_beta: 1, credible_mass: 0.95, methodology_version: testRate.methodology_version, description: 'Order by the server-supplied lower credible endpoint; provisional records remain unranked.' },
  entries: [{ ...testMetric, display_name: testPlayer.display_name, team_name: testPlayer.team_name, pass_rate: testRate, comparison_score: null }],
  methodology_version: testRate.methodology_version,
  unavailable_metrics: ['xG: no validated shot model'], disclaimer: 'Selected reviewed actions only; not talent. No opponent adjustment.',
};
function setup(response = result) {
  vi.mocked(api).mockImplementation(async (path) => path.startsWith('/matches?') ? [testMatch] : response);
}
async function compare(season = true) {
  const user = userEvent.setup();
  await user.selectOptions(await screen.findByLabelText('Age group'), 'U15');
  await user.selectOptions(screen.getByLabelText('Competition'), testMatch.competition);
  if (season) await user.selectOptions(screen.getByLabelText('Season'), '2025');
  await user.click(screen.getByRole('button', { name: 'Compare cohort' }));
}

describe('cohort comparison', () => {
  it('requires explicit cohort selection and sends all selected dimensions to the API', async () => {
    setup(); render(<Leaderboard mode="demo" />);
    await screen.findByLabelText('Age group');
    expect(vi.mocked(api).mock.calls.some(([path]) => path.startsWith('/leaderboard?'))).toBe(false);
    await compare();
    await waitFor(() => expect(api).toHaveBeenCalledWith('/leaderboard?mode=demo&age_group=U15&role=midfielder&competition=Synthetic+development+league&season=2025', expect.anything()));
    expect(await screen.findByText('Order: lower endpoint of the 95% pass-completion credible interval')).toBeVisible();
    expect(screen.getByText(/≤60.0% from one match/)).toBeVisible();
    expect(screen.getByText(/Beta\(1, 1\).*evidence sufficiency/)).toBeVisible();
    expect(screen.getByText('28.4% – 94.7%')).toBeVisible();
    expect(screen.getByText(SYNTHETIC_NOTICE)).toBeVisible();
    expect(screen.getByText('Lower endpoint for ordering: withheld')).toBeVisible();
    expect(screen.getByText('Unranked')).toBeVisible();
    expect(screen.getByText('xG: no validated shot model')).toBeVisible();
  });
  it('will not turn a provisional row into a rank even if the server supplies one', async () => {
    setup({ ...result, entries: [{ ...result.entries[0], rank: 1 }] });
    render(<Leaderboard mode="demo" />); await compare(false);
    const row = (await screen.findByRole('link', { name: /Synthetic roster 01/ })).closest('tr')!;
    expect(within(row).getByText('Unranked')).toBeVisible();
    expect(within(row).queryByText('01', { selector: '.rank' })).not.toBeInTheDocument();
    expect(vi.mocked(api).mock.calls.find(([path]) => path.startsWith('/leaderboard?'))?.[0]).not.toContain('season=');
  });
  it('refuses a comparison returned in the other data mode', async () => {
    setup({ ...result, mode: 'real', is_synthetic: false });
    render(<Leaderboard mode="demo" />); await compare();
    expect(await screen.findByRole('alert')).toHaveTextContent('different data mode');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
