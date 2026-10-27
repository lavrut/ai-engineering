import { useRef, useState } from 'preact/hooks';

/** Back / next / reset over `count` steps. Focus follows the button that just became disabled, so a
 *  keyboard reader is never dropped to the top of the page. */
export function useStepper(count: number) {
  const [at, setAt] = useState(0);
  const last = count - 1;
  const prevRef = useRef<HTMLButtonElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const resetRef = useRef<HTMLButtonElement>(null);
  const go = (to: number) => {
    const next = Math.max(0, Math.min(last, to));
    setAt(next);
    if (next >= last && document.activeElement === nextRef.current) resetRef.current?.focus();
    if (next <= 0 && document.activeElement === prevRef.current) nextRef.current?.focus();
  };
  return {
    at,
    last,
    prevRef,
    nextRef,
    resetRef,
    next: () => go(at + 1),
    prev: () => go(at - 1),
    reset: () => {
      setAt(0);
    },
    /** Arrow keys on the stepper's group. */
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault();
        go(at + 1);
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        go(at - 1);
      }
    },
  };
}
