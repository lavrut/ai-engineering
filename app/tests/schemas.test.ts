import { describe, expect, it } from 'vitest';
import { lessonSchema, trackSchema } from '../src/content/schemas';

const quizItem = {
  question: 'What ends the loop?',
  options: ['A', 'B', 'C'],
  answer: 1,
  explanation: 'Because B.',
};

const lesson = {
  track: 'harness',
  order: 1,
  slug: 'the-loop',
  title: 'The Loop',
  goal: 'Understand the agent loop.',
  minutes: 8,
  quiz: [quizItem, { ...quizItem, answer: 0 }],
};

describe('lessonSchema', () => {
  it('parses a valid lesson', () => {
    expect(lessonSchema.parse(lesson).slug).toBe('the-loop');
  });
  it('rejects an answer index out of range', () => {
    const bad = { ...lesson, quiz: [{ ...quizItem, answer: 3 }, quizItem] };
    expect(lessonSchema.safeParse(bad).success).toBe(false);
  });
  it('rejects a one-item quiz', () => {
    expect(lessonSchema.safeParse({ ...lesson, quiz: [quizItem] }).success).toBe(false);
  });
  it('rejects an invalid slug', () => {
    expect(lessonSchema.safeParse({ ...lesson, slug: 'Loop!' }).success).toBe(false);
  });
});

describe('trackSchema', () => {
  const track = {
    id: 'ml',
    title: 'Machine Learning',
    order: 3,
    status: 'planned',
    summary: 'Ship models.',
    subsections: ['MLOps'],
  };
  it('parses a planned track with subsections', () => {
    expect(trackSchema.parse(track).subsections).toEqual(['MLOps']);
  });
  it('rejects an unknown status', () => {
    expect(trackSchema.safeParse({ ...track, status: 'draft' }).success).toBe(false);
  });
});
