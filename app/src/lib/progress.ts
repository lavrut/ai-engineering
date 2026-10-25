const KEY = 'aie:progress:v1';

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
type Data = { done: string[]; scores: Record<string, number> };

function defaultStorage(): StorageLike | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function load(storage: StorageLike | null): Data {
  const empty: Data = { done: [], scores: {} };
  if (!storage) return empty;
  try {
    const raw = storage.getItem(KEY);
    if (!raw) return empty;
    const p = JSON.parse(raw);
    return {
      done: Array.isArray(p?.done) ? p.done.filter((x: unknown) => typeof x === 'string') : [],
      scores: p?.scores && typeof p.scores === 'object' && !Array.isArray(p.scores) ? p.scores : {},
    };
  } catch {
    return empty;
  }
}

export function createProgressStore(storage: StorageLike | null = defaultStorage()) {
  const data = load(storage);
  const listeners = new Set<() => void>();

  function commit() {
    try {
      storage?.setItem(KEY, JSON.stringify(data));
    } catch {
      // memory only
    }
    listeners.forEach((fn) => fn());
  }

  function markDoneQuiet(id: string) {
    if (!data.done.includes(id)) data.done.push(id);
  }

  return {
    isDone: (id: string) => data.done.includes(id),
    markDone(id: string) {
      markDoneQuiet(id);
      commit();
    },
    bestScore: (id: string): number | null => data.scores[id] ?? null,
    recordScore(id: string, correct: number, total: number) {
      const frac = total > 0 ? correct / total : 0;
      if (!(id in data.scores) || frac > data.scores[id]) data.scores[id] = frac;
      if (total > 0 && correct === total) markDoneQuiet(id);
      commit();
    },
    percent(ids: string[]) {
      if (ids.length === 0) return 0;
      return Math.round((ids.filter((id) => data.done.includes(id)).length / ids.length) * 100);
    },
    subscribe(fn: () => void) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
  };
}
