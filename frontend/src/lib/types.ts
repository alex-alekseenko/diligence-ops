export interface PipelineProgress {
  run_id: string;
  stage: "bronze" | "silver" | "gold" | "workstreams" | "complete" | "error";
  agent: string;
  message: string;
  progress_pct: number;
  timestamp?: string;
}

export interface CompanyInfo {
  ticker: string;
  company_name: string;
  cik: string;
  sic: string;
  sic_description: string;
  fiscal_year_end: string;
  exchanges: string[];
  entity_type: string;
  category: string;
  latest_10k_date: string | null;
}

export interface FinancialKPIs {
  revenue: number | null;
  revenue_prior: number | null;
  revenue_yoy_change: number | null;
  net_income: number | null;
  net_income_prior: number | null;
  gross_profit: number | null;
  gross_margin: number | null;
  operating_income: number | null;
  operating_margin: number | null;
  total_assets: number | null;
  total_liabilities: number | null;
  stockholders_equity: number | null;
  debt_to_equity: number | null;
  long_term_debt: number | null;
  cash_and_equivalents: number | null;
  current_ratio: number | null;
  operating_cash_flow: number | null;
  free_cash_flow: number | null;
  eps_basic: number | null;
  fiscal_year: number;
  period_end: string;
  currency: string;
  source_tags: Record<string, string>;
  anomalies: string[];
}

export interface RiskDimension {
  dimension: string;
  score: number;
  reasoning: string;
  key_metrics: string[];
}

export interface RedFlag {
  flag: string;
  severity: string;
  evidence: string;
}

export interface RiskAssessment {
  dimensions: RiskDimension[];
  composite_score: number;
  risk_level: string;
  red_flags: RedFlag[];
}

export interface DiligenceMemo {
  executive_summary: string;
  company_overview: string;
  financial_analysis: string;
  risk_assessment: string;
  key_findings: string[];
  recommendation: string;
  sections: { title: string; content: string; citations: string[] }[];
  generated_at: string;
}

// --- v0.2 Workstream Types ---

export interface RiskFactorItem {
  category: string;
  title: string;
  summary: string;
  severity: number;
  is_novel: boolean;
}

export interface InsiderTransaction {
  insider_name: string;
  title: string;
  tx_date: string;
  tx_code: string;
  shares: number;
  price: number | null;
  value: number | null;
}

export interface InsiderSignal {
  total_buys: number;
  total_sells: number;
  net_shares: number;
  buy_sell_ratio: number | null;
  cluster_detected: boolean;
  cluster_description: string | null;
  signal: string;
}

export interface InstitutionalHolder {
  holder_name: string;
  shares: number;
  value: number | null;
  change_pct: number | null;
  holder_type: string;
}

export interface MaterialEvent {
  filing_date: string;
  item_code: string;
  item_description: string;
  severity: number;
  summary: string | null;
}

export interface DirectorInfo {
  name: string;
  is_independent: boolean | null;
  committees: string[];
  role: string;
  age: number | null;
  director_since: number | null;
}

export interface NEOCompensation {
  name: string;
  title: string;
  total_comp: number | null;
  salary: number | null;
  stock_awards: number | null;
  non_equity_incentive: number | null;
  other_comp: number | null;
  fiscal_year: number | null;
}

export interface GovernanceData {
  ceo_name: string | null;
  ceo_total_comp: number | null;
  ceo_comp_prior: number | null;
  ceo_pay_growth: number | null;
  median_employee_pay: number | null;
  ceo_pay_ratio: number | null;
  board_size: number | null;
  independent_directors: number | null;
  board_independence_pct: number | null;
  directors: DirectorInfo[];
  has_poison_pill: boolean | null;
  has_staggered_board: boolean | null;
  has_dual_class: boolean | null;
  anti_takeover_provisions: string[];
  neo_compensation: NEOCompensation[];
  governance_flags: string[];
}

export interface CrossWorkstreamFlag {
  rule_name: string;
  severity: string;
  description: string;
  evidence: string[];
}

export interface PipelineResults {
  run_id: string;
  ticker: string;
  status: string;
  company_info?: CompanyInfo;
  kpis?: FinancialKPIs;
  risk_scores?: RiskAssessment;
  memo?: DiligenceMemo;
  risk_factors?: RiskFactorItem[];
  insider_signal?: InsiderSignal;
  insider_trades?: InsiderTransaction[];
  institutional_holders?: InstitutionalHolder[];
  material_events?: MaterialEvent[];
  governance?: GovernanceData;
  cross_workstream_flags?: CrossWorkstreamFlag[];
  deal_recommendation?: string;
  files?: {
    bronze_csv: string | null;
    silver_csv: string | null;
    gold_csv: string | null;
    memo_md: string | null;
    risk_factors_csv: string | null;
    insider_trades_csv: string | null;
    institutional_csv: string | null;
    events_csv: string | null;
    governance_csv: string | null;
  };
  confidence?: number;
  errors?: string[];
}
