// Shared by the ML aid modules: short category names, percentages and the 6-significant-figure
// rounding the reports' numbers go through. Pure and browser-safe.
import { roundSig } from './mlExplore';

/** Short category names for tight spaces (matrix headers, chips); the full name is always nearby. */
export const SHORT_NAMES: Record<string, string> = {
  'Checking or savings account': 'Checking/savings',
  'Credit card': 'Credit card',
  'Credit reporting or other personal consumer reports': 'Credit reporting',
  'Debt collection': 'Debt collection',
  'Money transfer, virtual currency, or money service': 'Money transfer',
  Mortgage: 'Mortgage',
};
export const shortName = (category: string): string => SHORT_NAMES[category] ?? category;

/** 0.4086 -> "40.9%". */
export const pct = (x: number, digits = 1): string => `${(x * 100).toFixed(digits)}%`;

// 6 significant figures: small, and shown to 3 decimals without double rounding.
export const sig = (x: number) => roundSig(x, 6);
