/**
 * Money primitives. Everything in this package is currently denominated in
 * floating-point US dollars, which is what the migration ticket is about.
 */

/** Round a dollar amount to two decimal places. */
export function round2(dollars: number): number {
  return Math.round(dollars * 100) / 100;
}

/** Render a dollar amount as "$1,234.56" (negatives as "-$1,234.56"). */
export function formatUsd(dollars: number): string {
  const negative = dollars < 0;
  const fixed = Math.abs(round2(dollars)).toFixed(2);
  const [whole, frac] = fixed.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}$${grouped}.${frac}`;
}
