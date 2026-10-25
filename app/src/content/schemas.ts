import { z } from 'astro/zod';

export const quizItemSchema = z
  .object({
    question: z.string().min(1),
    options: z.array(z.string().min(1)).min(2).max(5),
    answer: z.number().int().min(0),
    explanation: z.string().min(1),
  })
  .refine((q) => q.answer < q.options.length, {
    message: 'answer must be a valid index into options',
    path: ['answer'],
  });

export const lessonSchema = z.object({
  track: z.string().min(1),
  order: z.number().int(),
  slug: z.string().regex(/^[a-z0-9-]+$/),
  title: z.string().min(1),
  goal: z.string().min(1),
  minutes: z.number().positive(),
  quiz: z.array(quizItemSchema).min(2).max(3),
});

export const trackSchema = z.object({
  id: z.string().min(1),
  title: z.string().min(1),
  order: z.number().int(),
  status: z.enum(['live', 'planned']),
  summary: z.string().min(1),
  subsections: z.array(z.string()),
});

export type Lesson = z.infer<typeof lessonSchema>;
export type Track = z.infer<typeof trackSchema>;
