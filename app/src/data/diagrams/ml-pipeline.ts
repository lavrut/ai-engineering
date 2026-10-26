import type { Diagram } from '../../lib/diagram';

// Layout (default node 150x56). Row 1 runs left to right: Complaint, Tokenizer, TF-IDF,
// Scores, Label. The model file sits under Scores: its weights go straight up into Scores,
// and its vocabulary and idf run left along row 2 and up into the bottom of TF-IDF, so the
// two never cross. Code links point at ml/python/train.py (Python only).
export const mlPipeline: Diagram = {
  id: 'ml-pipeline',
  title: 'One complaint, from text to label',
  width: 1170,
  height: 330,
  nodes: [
    { id: 'complaint', label: 'Complaint text', role: 'dataset', x: 30, y: 60 },
    { id: 'tokenizer', label: 'Tokenizer', role: 'tool', x: 270, y: 60 },
    { id: 'tfidf', label: 'TF-IDF vector', role: 'tool', x: 510, y: 60 },
    { id: 'model-file', label: 'Model file (v1.json)', role: 'memory', x: 735, y: 230, w: 180 },
    { id: 'scores', label: 'Scores (softmax)', role: 'model', x: 750, y: 60 },
    { id: 'label', label: 'Label', role: 'harness', x: 990, y: 60 },
  ],
  edges: [
    { id: 'e-text', from: 'complaint', to: 'tokenizer', label: 'text' },
    { id: 'e-tokens', from: 'tokenizer', to: 'tfidf', label: 'tokens' },
    { id: 'e-vocab', from: 'model-file', to: 'tfidf', label: 'vocabulary, idf' },
    { id: 'e-weights', from: 'model-file', to: 'scores', label: 'weights' },
    { id: 'e-vector', from: 'tfidf', to: 'scores', label: 'vector' },
    { id: 'e-label', from: 'scores', to: 'label', label: 'top' },
  ],
  steps: [
    {
      n: 1,
      highlight: ['complaint', 'e-text', 'tokenizer'],
      caption:
        'A complaint arrives as plain text. The model learned from 1,800 complaints sent in 2019–2021 and was tested on 600 from 2022, never on a random mix of years.',
      code: { file: 'python', section: 2, source: 'ml-train' },
    },
    {
      n: 2,
      highlight: ['tokenizer', 'e-tokens', 'tfidf'],
      caption:
        'The tokenizer lowercases A–Z, splits on every character outside a–z, 0–9 and the apostrophe, and drops the XX and XXXX marks that hide personal details.',
      code: { file: 'python', section: 3, source: 'ml-train' },
    },
    {
      n: 3,
      highlight: ['model-file', 'e-vocab', 'tfidf'],
      caption:
        'TF-IDF counts each word the model knows, multiplies the count by the word’s idf (rare words weigh more) and scales the row to length 1. Unknown words are left out.',
      code: { file: 'python', section: 3, source: 'ml-train' },
    },
    {
      n: 4,
      highlight: ['model-file', 'e-weights', 'scores'],
      caption:
        'The model file holds what training learned: one weight per category and word, plus an intercept per category. It is plain JSON, so Go and the browser read it too.',
      code: { file: 'python', section: 4, source: 'ml-train' },
    },
    {
      n: 5,
      highlight: ['tfidf', 'e-vector', 'scores'],
      caption:
        'Each category scores weights · vector + intercept. Softmax turns the six scores into probabilities that add up to 1.',
      code: { file: 'python', section: 6, source: 'ml-train' },
    },
    {
      n: 6,
      highlight: ['scores', 'e-label', 'label'],
      caption:
        'The label is the most probable category. Below 0.5, or with no known word at all, the answer is flagged low confidence.',
      code: { file: 'python', section: 6, source: 'ml-train' },
    },
  ],
};
