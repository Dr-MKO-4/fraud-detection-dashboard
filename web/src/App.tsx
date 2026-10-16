import { useEffect, useState } from 'react';
import { Live } from './pages/Live';
import { HowItWorks } from './pages/HowItWorks';
import { About } from './pages/About';
import { startLoop } from './ui/engineStore';

const ROUTES: Record<string, { title: string; el: () => JSX.Element }> = {
  '/': { title: 'Dashboard live', el: Live },
  '/comment-ca-marche': { title: 'Comment ça marche', el: HowItWorks },
  '/a-propos': { title: 'À propos', el: About },
};

const norm = (p: string) => (ROUTES[p.replace(/\/+$/, '') || '/'] ? p.replace(/\/+$/, '') || '/' : '/');

function usePath() {
  const [path, setPath] = useState(() => norm(window.location.pathname));
  useEffect(() => {
    const on = () => setPath(norm(window.location.pathname));
    window.addEventListener('popstate', on);
    return () => window.removeEventListener('popstate', on);
  }, []);
  const go = (p: string) => {
    if (p === path) return;
    window.history.pushState({}, '', p);
    setPath(p);
    window.scrollTo(0, 0);
    document.getElementById('contenu')?.focus({ preventScroll: true });
  };
  return [path, go] as const;
}

export function App() {
  const [path, go] = usePath();
  useEffect(() => {
    startLoop();
  }, []);
  useEffect(() => {
    document.title = `${ROUTES[path].title} · MKAN · MoMTSim`;
  }, [path]);
  const Page = ROUTES[path].el;
  const link = (p: string, label: string) => (
    <a
      href={p}
      aria-current={path === p ? 'page' : undefined}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey) return;
        e.preventDefault();
        go(p);
      }}
    >
      {label}
    </a>
  );
  return (
    <>
      <a className="skip" href="#contenu">
        Aller au contenu
      </a>
      <header className="app-header">
        <div className="bar">
          <a
            className="brand"
            href="/"
            onClick={(e) => {
              e.preventDefault();
              go('/');
            }}
          >
            <img src="/logo_mkan_light.png" alt="" width="26" height="34" />
            <span>
              <b>MKAN · MoMTSim</b>
              <small>LIVE · DÉMO WEB</small>
            </span>
          </a>
          <nav className="nav" aria-label="Navigation principale">
            {link('/', 'Dashboard live')}
            {link('/comment-ca-marche', 'Comment ça marche')}
            {link('/a-propos', 'À propos')}
          </nav>
          <div className="spacer" />
          <div className="demo-flag" role="note">
            <i aria-hidden="true" />
            Démo pédagogique : flux et scoreur simulés dans le navigateur, ce n'est pas le modèle entraîné.
          </div>
        </div>
      </header>
      <main id="contenu" tabIndex={-1}>
        <Page />
      </main>
      <footer className="app-footer">
        <span>MKAN · MoMTSim · Georges Miguel MOUKOKO · M2 ENSPD, Université de Douala · LIDIA Lab</span>
        <span>Démo pédagogique : aucun résultat du mémoire n'est affiché.</span>
        <span>Tout tourne dans votre navigateur, aucune donnée n'est envoyée.</span>
      </footer>
    </>
  );
}
