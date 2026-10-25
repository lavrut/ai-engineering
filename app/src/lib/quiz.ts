export type QuizItem = {
  question: string;
  options: string[];
  answer: number;
  explanation: string;
};

export function score(quiz: QuizItem[], picks: (number | null)[]) {
  const correct = quiz.filter((q, i) => picks[i] === q.answer).length;
  return { correct, total: quiz.length, passed: correct === quiz.length };
}
