import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, ChartNoAxesCombined, ChevronDown, CircleHelp, Clapperboard, FlaskConical, LayoutDashboard, LockKeyhole, LogOut, Menu, Settings2, ShieldCheck, Users, X } from 'lucide-react';
import { api, RequestError, setCsrfToken } from './api';
import { useAction, useRoute } from './hooks';
import { Brand, DemoNotice, ErrorBox, Field, Loading, Pitch, PrivateNote, Success } from './ui';
import type { DemoSeedResponse, Mode, SessionResponse } from './types';
import { Dashboard } from './Dashboard';
import { Matches } from './Matches';
import { MatchReview } from './MatchReview';
import { Players, PlayerDetail } from './Players';
import { Leaderboard } from './Leaderboard';
import { Settings } from './Settings';
import { initials } from './utils';

const nav = [
  { path: '/', label: 'Overview', icon: LayoutDashboard },
  { path: '/matches', label: 'Match library', icon: Clapperboard },
  { path: '/players', label: 'Player directory', icon: Users },
  { path: '/leaderboard', label: 'Cohort comparisons', icon: ChartNoAxesCombined },
  { path: '/settings', label: 'Integrations & settings', icon: Settings2 },
];

function Auth({ onSession }: { onSession: (session: SessionResponse) => void }) {
  const [signup, setSignup] = useState(false);
  const action = useAction();
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    void action.run(async () => {
      const body = { email: String(data.get('email')).trim(), password: String(data.get('password')), ...(signup ? { workspace_name: String(data.get('workspace_name')).trim() } : {}) };
      onSession(await api<SessionResponse>(signup ? '/auth/signup' : '/auth/login', { method: 'POST', body }));
    });
  };
  return <div className="auth-layout"><aside className="auth-story"><Brand /><div className="auth-story-copy"><div className="eyebrow">The private scouting workspace</div><h1>See the game.<br />Understand<br /><em>the player.</em></h1><p>Turn match footage into reviewed evidence. Put context, development and the person behind the player first.</p></div><div className="auth-pitch"><Pitch decorative /><span>Evidence before opinion.</span></div><div className="auth-story-footer"><ShieldCheck size={18} />Private by default. Built for responsible scouting.</div></aside><main className="auth-main"><div className="auth-mobile-brand"><Brand /></div><div className="auth-card"><span className="badge green"><LockKeyhole size={12} />Your workspace, protected</span><h2>{signup ? 'A clearer view starts here.' : 'Welcome to Touchline.'}</h2><p>{signup ? 'Create an empty, private workspace. Add only players and footage you are authorized to process.' : 'Sign in to your scouting workspace. Every observation stays in context.'}</p><form onSubmit={submit} className="form-stack">{signup && <Field label="Workspace name"><input name="workspace_name" required minLength={2} maxLength={100} autoComplete="organization" placeholder="e.g. Northside development" /></Field>}<Field label="Email address"><input name="email" type="email" required maxLength={254} autoComplete="email" placeholder="you@yourclub.org" /></Field><Field label="Password" hint={signup ? '12–128 characters. Use a unique password.' : undefined}><input name="password" type="password" required minLength={signup ? 12 : 1} maxLength={128} autoComplete={signup ? 'new-password' : 'current-password'} /></Field><ErrorBox message={action.error} /><button className="button primary full" disabled={action.busy}>{action.busy ? 'Please wait…' : signup ? 'Create private workspace' : 'Sign in to workspace'}<ArrowRight size={17} /></button></form><div className="auth-switch">{signup ? 'Already have a workspace?' : 'New to Touchline?'} <button className="text-button" onClick={() => { setSignup(!signup); action.setError(''); }}>{signup ? 'Sign in' : 'Create an account'}</button></div><div className="auth-note"><ShieldCheck size={20} /><p>No real minors’ data is preloaded. A clearly labeled synthetic demonstration is available after sign-in, only if you choose to seed it.</p></div></div><footer className="auth-footer">Human judgment. Traceable evidence. No universal talent score.</footer></main></div>;
}

export default function App() {
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState('');
  const [bootRevision, setBootRevision] = useState(0);
  const [mode, setMode] = useState<Mode>('real');
  const [dataRevision, setDataRevision] = useState(0);
  const [mobileOpen, setMobileOpen] = useState(false);
  const route = useRoute();
  const action = useAction();
  const acceptSession = (next: SessionResponse) => { setCsrfToken(next.csrf_token); setSession(next); setMode('real'); };
  useEffect(() => {
    const controller = new AbortController(); setBooting(true); setBootError('');
    api<SessionResponse>('/auth/me', { signal: controller.signal }).then((next) => { if (!controller.signal.aborted) acceptSession(next); }).catch((error: unknown) => {
      if (!controller.signal.aborted && !(error instanceof RequestError && error.status === 401)) setBootError(error instanceof Error ? error.message : 'Unable to load workspace.');
    }).finally(() => { if (!controller.signal.aborted) setBooting(false); });
    return () => controller.abort();
  }, [bootRevision]);
  useEffect(() => { const expired = () => { setCsrfToken(null); setSession(null); setMode('real'); }; window.addEventListener('touchline:session-expired', expired); return () => window.removeEventListener('touchline:session-expired', expired); }, []);
  useEffect(() => { setMobileOpen(false); }, [route]);
  useEffect(() => {
    if (!mobileOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') { setMobileOpen(false); document.querySelector<HTMLButtonElement>('[aria-label="Open navigation"]')?.focus(); } };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [mobileOpen]);
  const seedDemo = async () => { await api<DemoSeedResponse>('/demo/seed', { method: 'POST' }); setMode('demo'); setDataRevision((n) => n + 1); window.location.hash = '/'; };
  if (booting) return <div className="boot"><Brand /><Loading label="Opening your private workspace…" /></div>;
  if (bootError) return <div className="boot"><Brand /><ErrorBox message={bootError} retry={() => setBootRevision((n) => n + 1)} /></div>;
  if (!session) return <Auth onSession={acceptSession} />;
  const matchId = /^\/matches\/([^/?]+)(?:\?.*)?$/.exec(route)?.[1];
  const playerId = /^\/players\/([^/?]+)$/.exec(route)?.[1];
  const activeNav = nav.find((n) => n.path === '/' ? route === '/' : route.startsWith(n.path));
  const page = matchId ? <MatchReview id={matchId} mode={mode} /> : playerId ? <PlayerDetail id={playerId} mode={mode} /> : route === '/matches' ? <Matches mode={mode} /> : route === '/players' ? <Players mode={mode} /> : route === '/leaderboard' ? <Leaderboard mode={mode} /> : route === '/settings' ? <Settings session={session} mode={mode} seedDemo={seedDemo} /> : route === '/' ? <Dashboard mode={mode} seedDemo={seedDemo} /> : <div className="panel pad"><h1>Page not found</h1><a href="#/">Return to overview</a></div>;
  return <div className="app-shell"><a href="#main-content" className="skip-link" onClick={(e) => { e.preventDefault(); document.getElementById('main-content')?.focus(); }}>Skip to content</a>{mobileOpen && <button className="nav-backdrop" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}<aside className={`sidebar ${mobileOpen ? 'open' : ''}`}><div className="sidebar-brand"><Brand /><button className="icon-button mobile-only" aria-label="Close navigation" onClick={() => setMobileOpen(false)}><X size={22} /></button></div><div className="workspace-switch"><div className="workspace-avatar">{initials(session.workspace.name)}</div><div><strong>{session.workspace.name}</strong><span>Private scouting workspace</span></div><LockKeyhole size={14} /></div><div className="nav-label">Workspace</div><nav aria-label="Main navigation">{nav.map((item) => <a href={`#${item.path}`} key={item.path} className={activeNav?.path === item.path ? 'active' : ''} aria-current={activeNav?.path === item.path ? 'page' : undefined}><item.icon size={18} /><span>{item.label}</span>{activeNav?.path === item.path && <span className="nav-active-dot" />}</a>)}</nav><div className="sidebar-bottom"><div className="sidebar-principle"><span className="mini-pitch" aria-hidden="true" /><strong>More context.<br />Better conversations.</strong><p>Evidence supports development.<br />It doesn’t define a player.</p></div><a href="#/settings" className="sidebar-help"><CircleHelp size={17} />Privacy & methodology<ArrowRight size={14} /></a><button className="account-button" disabled={action.busy} onClick={() => void action.run(async () => { await api('/auth/logout', { method: 'POST' }); setCsrfToken(null); setSession(null); setMode('real'); window.location.hash = '/'; })}><span className="account-avatar">{initials(session.account.email)}</span><span><strong>Workspace owner</strong><small>Sign out securely</small></span><LogOut size={16} /></button></div></aside><div className="main-shell"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-only" aria-label="Open navigation" aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}><Menu size={21} /></button><span className="topbar-breadcrumb">Workspace <span>/</span> <strong>{activeNav?.label ?? 'Overview'}</strong></span></div><div className="topbar-right"><span className="private-top"><LockKeyhole size={13} />Private</span><label className={`mode-selector ${mode === 'demo' ? 'is-demo' : ''}`}><FlaskConical size={14} /><span className="sr-only">Workspace data mode</span><select value={mode} onChange={(e) => setMode(e.target.value as Mode)}><option value="real">Real workspace</option><option value="demo">Synthetic demo</option></select><ChevronDown size={13} /></label></div></header><main id="main-content" className="main-content" tabIndex={-1}>{mode === 'demo' && <DemoNotice />}<ErrorBox message={action.error} /><Success message={action.success} /><div key={`${session.workspace.id}:${mode}:${dataRevision}:${route}`}>{page}</div><footer className="workspace-footer"><PrivateNote /><span>Touchline · Evidence before opinion</span></footer></main></div></div>;
}
