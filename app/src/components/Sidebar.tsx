import { useEffect, useState } from 'preact/hooks';
import { progress } from '../lib/progressStore';
import { type ThemeChoice, readTheme, saveTheme, watchHostTheme } from '../lib/theme';

const THEMES: { id: ThemeChoice; label: string }[] = [
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'auto', label: 'Auto' },
];

export type SidebarLesson = { id: string; title: string; order: number };
export type SidebarTrack = {
  id: string;
  title: string;
  status: 'live' | 'planned';
  subsections: string[];
  lessons: SidebarLesson[];
};

type Props = { tracks: SidebarTrack[] };

function currentHash() {
  return typeof location === 'undefined' ? '' : location.hash.replace(/^#/, '');
}

export default function Sidebar({ tracks }: Props) {
  const [open, setOpen] = useState(false);
  // Progress lives in localStorage, which the server render cannot see; read it
  // only after mount so hydration matches the static HTML.
  const [mounted, setMounted] = useState(false);
  const [, setVersion] = useState(0);
  const [active, setActive] = useState('');
  const [theme, setTheme] = useState<ThemeChoice>('auto');

  useEffect(() => {
    setMounted(true);
    setActive(currentHash());
    setTheme(readTheme());
    const stopWatching = watchHostTheme();
    const onHash = () => {
      setActive(currentHash());
      setOpen(false);
    };
    window.addEventListener('hashchange', onHash);
    const unsubscribe = progress.subscribe(() => setVersion((v) => v + 1));
    return () => {
      window.removeEventListener('hashchange', onHash);
      unsubscribe();
      stopWatching();
    };
  }, []);

  const isDone = (id: string) => mounted && progress.isDone(id);
  const chooseTheme = (choice: ThemeChoice) => {
    saveTheme(choice);
    setTheme(choice);
  };

  return (
    <div class="sidebar" data-open={open ? 'true' : 'false'}>
      <div class="sidebar-head">
        <a class="wordmark" href="#home">
          <span class="wordmark-title">AI Engineering</span>
          <span class="wordmark-sub">Field notes for building with models</span>
        </a>
        <button
          type="button"
          class="sidebar-toggle"
          aria-expanded={open}
          aria-controls="sidebar-nav"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? 'Close' : 'Menu'}
        </button>
      </div>
      <nav id="sidebar-nav" class="sidebar-nav" aria-label="Tracks and lessons">
        <ol class="nav-tracks">
          {tracks.map((t) => {
            const ids = t.lessons.map((l) => l.id);
            const pct = mounted ? progress.percent(ids) : 0;
            if (t.status === 'live') {
              return (
                <li class="nav-track is-live" key={t.id}>
                  <div class="nav-track-head">
                    <span class="nav-track-title">{t.title}</span>
                    {ids.length > 0 && (
                      <span class="nav-pct">
                        {pct}%
                      </span>
                    )}
                  </div>
                  {ids.length > 0 && (
                    <ol class="nav-lessons">
                      {t.lessons.map((l) => {
                        const done = isDone(l.id);
                        return (
                          <li key={l.id}>
                            <a
                              href={`#${l.id}`}
                              class={done ? 'nav-lesson is-done' : 'nav-lesson'}
                              aria-current={active === l.id ? 'page' : undefined}
                            >
                              <span class="nav-num">{String(l.order).padStart(2, '0')}</span>
                              <span class="nav-lesson-title">{l.title}</span>
                              <span class="nav-tick" aria-hidden="true">
                                {done ? '✓' : ''}
                              </span>
                              {done && <span class="visually-hidden">(done)</span>}
                            </a>
                          </li>
                        );
                      })}
                    </ol>
                  )}
                </li>
              );
            }
            return (
              <li class="nav-track is-planned" key={t.id}>
                <div class="nav-track-head">
                  <span class="nav-track-title">{t.title}</span>
                  <span class="chip">Planned</span>
                </div>
                {t.subsections.length > 0 && (
                  <ul class="nav-subs">
                    {t.subsections.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
        <div class="theme-switch" role="group" aria-label="Color theme">
          <span class="theme-switch-label" aria-hidden="true">
            Theme
          </span>
          {THEMES.map((t) => (
            <button
              type="button"
              key={t.id}
              aria-pressed={mounted && theme === t.id}
              onClick={() => chooseTheme(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </nav>
    </div>
  );
}
