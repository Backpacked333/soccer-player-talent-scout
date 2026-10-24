import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DemoNotice, JobCard, MetricCard, Pitch, PrivateNote, StatusBadge } from './ui';
import type { JobRead } from './types';
import { SYNTHETIC_NOTICE } from './types';
import { testEvent, testMetric } from './test-fixtures';

const job: JobRead = {
  id: 'job-test', workspace_id: 'workspace-test', match_id: 'match-test', upload_id: 'upload-test',
  status: 'needs_configuration', stage: 'metadata_ready', progress: 1,
  message: 'Metadata probe completed; no player analysis was performed.', attempts: 1,
  result: { analysis_available: false, candidate_count: 0, observation_count: 0 },
  is_synthetic: false, created_at: '2025-01-01T00:00:00Z', updated_at: '2025-01-01T00:00:01Z',
  started_at: '2025-01-01T00:00:00Z', finished_at: '2025-01-01T00:00:01Z',
};

describe('evidence-first interface', () => {
  it('shows cohort, sample, coverage, eligibility and limitations next to every score', () => {
    render(<MetricCard metric={testMetric} />);
    expect(screen.getByText(/U15.*Midfielder.*Synthetic development league.*2025/)).toBeInTheDocument();
    expect(screen.getByText('3 / 4 attempts')).toBeInTheDocument();
    expect(screen.getByText('Approved events').nextElementSibling).toHaveTextContent('4');
    expect(screen.getByText('Matches').nextElementSibling).toHaveTextContent('1');
    expect(screen.getByText('Declared coverage').nextElementSibling).toHaveTextContent('33.3%');
    expect(screen.getByText('Cohort rank').nextElementSibling).toHaveTextContent('Unranked');
    expect(screen.getByText('Provisional')).toBeInTheDocument();
    expect(screen.getByText('Needs more reviewed matches')).toBeInTheDocument();
    expect(screen.getByText(/Uncertainty estimates are not included/)).toBeInTheDocument();
    expect(screen.getByText(SYNTHETIC_NOTICE)).toBeInTheDocument();
  });
  it('does not turn missing opportunities or coverage into zero performance', () => {
    render(<MetricCard metric={{ ...testMetric, pass_attempts: 0, successful_passes: 0, pass_completion_pct: null, coverage_fraction: null, approved_event_count: 0, eligibility: 'unranked' }} />);
    expect(screen.getByText('0 / 0 attempts')).toBeInTheDocument();
    expect(screen.getByText('Declared coverage').nextElementSibling).toHaveTextContent('—');
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument();
    expect(screen.getByText('Cohort rank').nextElementSibling).toHaveTextContent('Unranked');
  });
  it('plots only approved finite reviewer coordinates, never invented tracking', () => {
    render(<Pitch events={[
      testEvent,
      { ...testEvent, id: 'valid', start_x: 0.5, start_y: 0.5 },
      { ...testEvent, id: 'draft', start_x: 0.4, start_y: 0.4, status: 'draft' },
      { ...testEvent, id: 'rejected', start_x: 0.2, start_y: 0.2, status: 'rejected' },
      { ...testEvent, id: 'nonfinite', start_x: Number.NaN, start_y: 0.5 },
      { ...testEvent, id: 'outside', start_x: 1.1, start_y: 0.5 },
    ]} />);
    expect(screen.getByRole('img', { name: 'Pitch with 1 approved, reviewer-positioned events' })).toBeInTheDocument();
  });
  it('distinguishes empty evidence and decorative soccer illustration from tracking', () => {
    render(<><Pitch /><Pitch decorative /></>);
    expect(screen.getByText(/Position mapping is unavailable until reviewed/)).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Pitch with 0 approved, reviewer-positioned events' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Illustrated soccer pitch, not player tracking' })).toBeInTheDocument();
  });
  it('explicitly labels synthetic data and private access boundaries', () => {
    render(<><DemoNotice /><PrivateNote /></>);
    expect(screen.getByText('Synthetic demo')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(SYNTHETIC_NOTICE);
    expect(screen.getByText('Private workspace · authorized access only')).toBeInTheDocument();
  });
});

describe('honest metadata-only processing', () => {
  it('separates metadata from operator-gated optional detection', () => {
    render(<JobCard job={job} />);
    expect(screen.getByText(/needs configuration/i)).toBeInTheDocument();
    expect(screen.getByText(/100% of processing stage/i)).toBeInTheDocument();
    expect(screen.getByText(/Video metadata is not player analysis/)).toHaveTextContent('it is disabled by default and never approves identities or events');
    expect(screen.queryByText(/analyzed|analysed/i)).not.toBeInTheDocument();
  });
  it('keeps stored and probed footage labels separate from analysis claims', () => {
    render(<><StatusBadge status="stored" /><StatusBadge status="probed" /><JobCard job={{ ...job, status: 'queued', stage: 'queued', progress: 0, message: '' }} /></>);
    expect(screen.getByText('Stored')).toBeInTheDocument();
    expect(screen.getByText('Probed')).toBeInTheDocument();
    expect(screen.getByText('Waiting for the local worker.')).toBeInTheDocument();
    expect(screen.queryByText(/analyzed|analysed/i)).not.toBeInTheDocument();
  });
});
