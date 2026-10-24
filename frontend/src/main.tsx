import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const entry = import.meta.env.MODE === 'demo' ? import('./PublicDemo') : import('./App');
void entry.then(({ default: App }) => {
  createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
});
