// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/preact';
import { score, type QuizItem } from '../src/lib/quiz';
import Quiz from '../src/components/Quiz';
import { progress } from '../src/lib/progressStore';

const quiz: QuizItem[] = [
  { question: 'Q1?', options: ['a', 'b'], answer: 1, explanation: 'because one' },
  { question: 'Q2?', options: ['a', 'b', 'c'], answer: 0, explanation: 'because two' },
  { question: 'Q3?', options: ['a', 'b'], answer: 1, explanation: 'because three' },
];

describe('score', () => {
  it('passes when every answer is right', () => {
    expect(score(quiz, [1, 0, 1])).toEqual({ correct: 3, total: 3, passed: true });
  });
  it('does not pass when one is unanswered', () => {
    expect(score(quiz, [1, null, 1])).toEqual({ correct: 2, total: 3, passed: false });
  });
  it('does not pass when one is wrong', () => {
    expect(score(quiz, [0, 0, 1])).toMatchObject({ correct: 2, passed: false });
  });
});

describe('Quiz', () => {
  it('scores picks, shows the result, and marks the lesson done', () => {
    const correct = [1, 0, 1];
    render(<Quiz lessonId="quiz-test-b" quiz={quiz} />);
    correct.forEach((opt, q) => {
      const group = screen.getByRole('radiogroup', { name: quiz[q].question });
      fireEvent.click(group.querySelectorAll<HTMLInputElement>('input[type="radio"]')[opt]);
    });
    expect(progress.isDone('quiz-test-b')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Check answers' }));
    expect(screen.getByText('3 of 3 correct')).toBeTruthy();
    expect(screen.getByText('because one')).toBeTruthy();
    expect(progress.isDone('quiz-test-b')).toBe(true);
  });

  it('resets with Try again', () => {
    cleanup();
    render(<Quiz lessonId="quiz-test-c" quiz={quiz} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check answers' }));
    expect(screen.getByText('0 of 3 correct')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(screen.queryByText('0 of 3 correct')).toBeNull();
    expect(progress.isDone('quiz-test-c')).toBe(false);
  });
});
