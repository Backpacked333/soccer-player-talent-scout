import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api';

export function useResource<T>(loader: (signal: AbortSignal) => Promise<T>, key: string) {
  const loaderRef = useRef(loader); loaderRef.current = loader;
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ key: string; data: T | null; error: string; loading: boolean }>({ key, data: null, error: '', loading: true });
  useEffect(() => {
    const controller = new AbortController();
    setState((old) => ({ key, data: old.key === key ? old.data : null, error: '', loading: true }));
    loaderRef.current(controller.signal).then((data) => {
      if (!controller.signal.aborted) setState({ key, data, error: '', loading: false });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ key, data: null, error: error instanceof Error ? error.message : 'Something went wrong.', loading: false });
    });
    return () => controller.abort();
  }, [key, revision]);
  const reload = useCallback(() => setRevision((n) => n + 1), []);
  return { data: state.key === key ? state.data : null, loading: state.key !== key || state.loading, error: state.key === key ? state.error : '', reload };
}

export function useApi<T>(path: string) { return useResource<T>((signal) => api<T>(path, { signal }), path); }

export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const active = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const run = async (action: () => Promise<void>, message = '') => {
    if (active.current) return;
    active.current = true; setBusy(true); setError(''); setSuccess('');
    try { await action(); if (mounted.current) setSuccess(message); }
    catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : 'The action could not be completed.'); }
    finally { active.current = false; if (mounted.current) setBusy(false); }
  };
  return { busy, error, success, run, setError };
}

export function useRoute() {
  const current = () => window.location.hash.slice(1) || '/';
  const [route, setRoute] = useState(current);
  useEffect(() => { const change = () => { setRoute(current()); window.scrollTo?.(0, 0); }; window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  return route;
}
