import { useEffect, useState } from 'preact/hooks';
import { progress } from '../lib/progressStore';
import { score, type QuizItem } from '../lib/quiz';

type Props = { lessonId: string; quiz: QuizItem[] };

export default function Quiz({ lessonId, quiz }: Props) {
  const [picks, setPicks] = useState<(number | null)[]>(() => quiz.map(() => null));
  const [checked, setChecked] = useState(false);

  const result = score(quiz, picks);

  function check() {
    setChecked(true);
    progress.recordScore(lessonId, result.correct, result.total);
  }

  function reset() {
    setPicks(quiz.map(() => null));
    setChecked(false);
  }

  return (
    <section class="quiz" aria-label="Check your understanding">
      <h2 class="quiz-title">Check your understanding</h2>
      {quiz.map((q, qi) => {
        const right = picks[qi] === q.answer;
        return (
          <div class="quiz-q" key={qi}>
            <p class="quiz-question" id={`${lessonId}-q${qi}`}>
              {q.question}
            </p>
            <div role="radiogroup" aria-labelledby={`${lessonId}-q${qi}`} class="quiz-options">
              {q.options.map((opt, oi) => (
                <label class="quiz-option" key={oi}>
                  <input
                    type="radio"
                    name={`${lessonId}-q${qi}`}
                    checked={picks[qi] === oi}
                    disabled={checked}
                    onChange={() => setPicks(picks.map((p, i) => (i === qi ? oi : p)))}
                  />
                  <span>{opt}</span>
                </label>
              ))}
            </div>
            {checked && (
              <p class={`quiz-feedback ${right ? 'is-right' : 'is-wrong'}`} role="status">
                <strong>{right ? 'Correct.' : 'Not quite.'}</strong> {q.explanation}
              </p>
            )}
          </div>
        );
      })}
      <div class="quiz-actions">
        {checked ? (
          <>
            <p class="quiz-result" role="status">{`${result.correct} of ${result.total} correct`}</p>
            <button type="button" class="btn" onClick={reset}>
              Try again
            </button>
          </>
        ) : (
          <button type="button" class="btn btn-primary" onClick={check}>
            Check answers
          </button>
        )}
      </div>
      <style>{css}</style>
    </section>
  );
}

/** Manual completion for lessons the reader finishes without a perfect quiz. */
export function MarkDone({ lessonId }: { lessonId: string }) {
  const [mounted, setMounted] = useState(false);
  const [, setVersion] = useState(0);
  useEffect(() => {
    setMounted(true);
    return progress.subscribe(() => setVersion((v) => v + 1));
  }, []);
  const done = mounted && progress.isDone(lessonId);
  return (
    <div class="markdone">
      <button type="button" class="btn" disabled={done} onClick={() => progress.markDone(lessonId)}>
        {done ? 'Lesson done' : 'Mark lesson done'}
      </button>
      <style>{css}</style>
    </div>
  );
}

const css = `
.quiz-title { margin-top: 0; }
.quiz-q { margin: 0 0 24px; }
.quiz-question { margin: 0 0 8px; font-weight: 600; }
.quiz-options { display: grid; gap: 4px; }
.quiz-option {
  display: flex; align-items: flex-start; gap: 8px; padding: 8px 12px;
  border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); cursor: pointer;
}
.quiz-option:has(input:checked) { border-color: var(--accent); }
.quiz-option input { margin-top: 5px; accent-color: var(--accent); }
.quiz-option input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.quiz-feedback { margin: 8px 0 0; padding-left: 12px; border-left: 3px solid var(--line); color: var(--muted); }
.quiz-feedback.is-right { border-left-color: var(--good); }
.quiz-feedback.is-wrong { border-left-color: var(--bad); }
.quiz-feedback strong { color: var(--ink); }
.quiz-actions { display: flex; align-items: center; gap: 16px; }
.quiz-result { margin: 0; font-family: var(--font-mono); font-size: var(--text-label); }
.btn {
  font: inherit; font-weight: 500; padding: 8px 16px; color: var(--ink); background: var(--surface-2);
  border: 1px solid var(--line); border-radius: var(--radius); cursor: pointer;
}
.btn-primary { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.btn:disabled { color: var(--muted); cursor: default; }
.btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.markdone { margin-top: 24px; }
`;
