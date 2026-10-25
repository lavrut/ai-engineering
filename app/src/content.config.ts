import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { lessonSchema, trackSchema } from './content/schemas';

const tracks = defineCollection({
  loader: glob({ pattern: '*.yaml', base: 'src/content/tracks' }),
  schema: trackSchema,
});

const lessons = defineCollection({
  loader: glob({ pattern: '**/*.mdx', base: 'src/content/lessons' }),
  schema: lessonSchema,
});

export const collections = { tracks, lessons };
