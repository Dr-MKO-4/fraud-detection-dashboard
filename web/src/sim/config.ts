/**
 * Paramètres repris de MOMTSIM/config/fraudScenariosConfig.json (valeurs identiques)
 * et de MOMTSIM/config/paramFiles (types de transactions, profil horaire, montants).
 */
export const FRAUD_CONFIG = {
  global: { fraud_target_min: 0.2, fraud_target_max: 0.26, scenarios_equal_share: true },
  ato: { B_min: 5000, n_min: 2, n_max: 8, frag_min: 0.15, frag_max: 0.35, lambda_ato: 2 },
  refund: { p_refund_threshold: 0.15, delay_min_hours: 1, delay_max_hours: 48, k_max: 3, ratio_legit: 0.3 },
  fake_credentials: {
    dormance_min_days: 7,
    dormance_max_days: 30,
    n_leg_min: 3,
    n_leg_max: 8,
    m_leg_max: 2500,
    m_exp_ratio_min: 0.5,
  },
  split_deposit: {
    epsilon_max: 5000,
    T_split_min_sec: 60,
    T_split_max_sec: 120,
    tariff_grid: [
      { threshold: 0, commission: 0 },
      { threshold: 1000, commission: 50 },
      { threshold: 5000, commission: 150 },
      { threshold: 20000, commission: 400 },
      { threshold: 50000, commission: 750 },
      { threshold: 100000, commission: 1200 },
    ],
  },
  smurfing: {
    n_mules_min: 3,
    n_mules_max: 8,
    pct_conscious: 0.3,
    pct_unconscious: 0.4,
    S_seuil: 500000,
    delta_min: 0.02,
    delta_max: 0.05,
    delay_mule_min_hours: 1,
    delay_mule_max_hours: 12,
    operation_interval_days: 10,
    n_leg_mule_min: 5,
    n_leg_mule_max: 20,
  },
  legit_refund: { p_refund: 0.1 },
} as const;

/** transactionsTypes.csv (+ REFUND, produit par les marchands). */
export const ACTIONS = ['CASH_IN', 'CASH_OUT', 'DEBIT', 'PAYMENT', 'TRANSFER', 'REFUND'] as const;
export type Action = (typeof ACTIONS)[number];

/**
 * Parts et montants moyens par action, agrégés depuis aggregatedTransactions.csv
 * (count, avg). TRANSFER est ramené à une échelle de solde client plausible pour
 * une population réduite (le fichier source mélange des virements d'entreprise).
 */
export const ACTION_PROFILE: Record<Exclude<Action, 'REFUND'>, { share: number; mean: number; cv: number }> = {
  CASH_IN: { share: 0.2, mean: 60000, cv: 0.9 },
  CASH_OUT: { share: 0.34, mean: 45000, cv: 0.9 },
  DEBIT: { share: 0.01, mean: 5165, cv: 0.6 },
  PAYMENT: { share: 0.35, mean: 12806, cv: 0.8 },
  TRANSFER: { share: 0.1, mean: 40000, cv: 1.0 },
};

/** Profil horaire de volume (part par heure 0..23), aggregatedTransactions.csv toutes actions. */
export const HOURLY_PROFILE = [
  0.0329, 0.0118, 0.0028, 0.0003, 0.0, 0.0, 0.0006, 0.0035, 0.0095, 0.014, 0.0117, 0.0163, 0.0246, 0.0358,
  0.0441, 0.0443, 0.0612, 0.0697, 0.1057, 0.1294, 0.1288, 0.1024, 0.0852, 0.0652,
];

export const SCENARIOS = ['ATO', 'REFUND', 'FAKE_CRED', 'SPLIT_DEP', 'SMURFING'] as const;
export type Scenario = (typeof SCENARIOS)[number];

export const SCENARIO_INFO: Record<Scenario, { label: string; short: string; signature: string }> = {
  ATO: {
    label: 'Prise de contrôle de compte',
    short: 'ATO',
    signature: 'Rafale de 2 à 8 TRANSFER vers des mules jamais contactées, chacun 15 à 35 % du solde initial, espacés de Δt ~ Exp(λ = 2 min).',
  },
  REFUND: {
    label: 'Fraude au remboursement',
    short: 'REFUND',
    signature: 'Boucles PAYMENT vers un marchand vulnérable puis REFUND différé de 1 à 48 h, k_max = 3 cycles par marchand, 30 % de paiements de camouflage.',
  },
  FAKE_CRED: {
    label: 'Fausses accréditations',
    short: 'FAKE_CRED',
    signature: 'Compte dormant 7 à 30 jours avec 3 à 8 petits paiements (≤ 2 500 FCFA), puis exfiltration unique de 50 à 100 % du plafond.',
  },
  SPLIT_DEP: {
    label: 'Fractionnement de dépôt agent',
    short: 'SPLIT_DEP',
    signature: "Dépôt CASH_IN découpé en k fragments par l'agent pour maximiser la commission de la grille tarifaire, en 60 à 120 s.",
  },
  SMURFING: {
    label: 'Structuration multi-mules',
    short: 'SMURFING',
    signature: 'Émetteur → 3 à 8 mules (fragments à 70-99 % du seuil S = 500 000 FCFA), puis mule → receveur différé de 1 à 12 h, moins 2 à 5 % de commission.',
  },
};
