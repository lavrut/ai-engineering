import { createProgressStore } from './progress';

/** The one progress store shared by every island (sidebar, quiz, ...). */
export const progress = createProgressStore();
