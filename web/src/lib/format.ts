const nf0 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1, minimumFractionDigits: 1 });

export const fcfa = (v: number) => `${nf0.format(Math.round(v))} FCFA`;
export const int = (v: number) => nf0.format(Math.round(v));
export const dec1 = (v: number) => nf1.format(v);
export const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits).replace('.', ',')} %`;

/** Montant compact : 1,2 M / 45 k. */
export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1).replace('.', ',')} Md`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1).replace('.', ',')} M`;
  if (a >= 1e4) return `${Math.round(v / 1e3)} k`;
  return nf0.format(Math.round(v));
}

/** Valeur brute d'une variable, lisible. */
export function featValue(v: number): string {
  if (!Number.isFinite(v)) return '∞';
  const a = Math.abs(v);
  if (a >= 1e6) return compact(v);
  if (a >= 1000) return nf0.format(Math.round(v));
  if (a >= 10) return v.toFixed(1).replace('.', ',');
  return v.toFixed(2).replace('.', ',');
}

export const signed = (v: number, d = 2) => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(d).replace('.', ',')}`;
