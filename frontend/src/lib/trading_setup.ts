export type TradeMode = "PAPER" | "LIVE";

export type MasterSetup = {
  goLive: boolean;
  engineRunning: boolean;
  brokerConnected: boolean;
  sharedApiConnected: boolean;
  platformApiOnline: boolean;
  mode: TradeMode;
  maxTrades: number;
  dailyLossLimit: number;
  /** Backend: zerodha | fyers | null */
  activeBroker?: string | null;
  /** "connected" = Admin direct Kite, "shared" = using shared API, "none" = unavailable */
  kiteStatus?: "connected" | "shared" | "none";
};

export type ZerodhaCredentials = {
  apiKey: string;
  apiSecret: string;
  userId: string;
  password: string;
  totpSecret: string;
  requestToken: string;
  accessToken: string;
};

export type CapitalRiskSetup = {
  initialCapital: number;
  maxInvestmentPerTrade: number;
  maxProfitDay: number;
  maxLossDay: number;
  maxTradesDay: number;
  maxParallelTrades: number;
  chargesPerTrade: number;
};

export type TradingParametersSetup = {
  lots: number;
  /** NIFTY (and similar) index options — contract multiplier per lot */
  lotSize: number;
  /** Bank Nifty index options — NSE contract size (verify on nseindia.com) */
  bankniftyLotSize: number;
  maxStrikeDistanceFromAtm: number;
  maxPremium: number;
  minPremium: number;
  minEntryStrengthPct: number;
  slType: "Fixed Points" | "Percent";
  slPoints: number;
  breakevenTriggerPct: number;
  targetPoints: number;
  trailingSlPoints: number;
};

export type StrategyDetails = {
  displayName?: string;
  description?: string;
  indicators?: Record<string, unknown>;
  scoreThreshold?: number;
  scoreDescription?: string;
  positionIntent?: "long_premium" | "short_premium";
  [key: string]: unknown;
};

export type StrategySetup = {
  strategyName: string;
  strategyVersion?: string;
  timeframe: "1-min" | "3-min" | "5-min" | "15-min";
  indices: {
    NIFTY: boolean;
    BANKNIFTY: boolean;
    FINNIFTY: boolean;
    MIDCPNIFTY: boolean;
  };
  tradeStart: string;
  tradeEnd: string;
  autoPauseAfterLosses: number;
  details?: StrategyDetails;
  fromSettings?: {
    timeframe: string;
    targetPoints: number;
    slPoints: number;
    trailingSlPoints: number;
  };
};

export type TradingSetup = {
  master: MasterSetup;
  credentials: ZerodhaCredentials;
  capitalRisk: CapitalRiskSetup;
  tradingParameters: TradingParametersSetup;
  strategy: StrategySetup;
  updatedAt: string;
};

export const TRADING_SETUP_KEY = "s004_trading_setup";

export const DEFAULT_TRADING_SETUP: TradingSetup = {
  master: {
    goLive: false,
    engineRunning: false,
    brokerConnected: false,
    sharedApiConnected: true,
    platformApiOnline: true,
    mode: "PAPER",
    maxTrades: 4,
    dailyLossLimit: 2000,
  },
  credentials: {
    apiKey: "1**********k",
    apiSecret: "6**********d",
    userId: "U****4",
    password: "l*******@",
    totpSecret: "9****3",
    requestToken: "",
    accessToken: "",
  },
  capitalRisk: {
    initialCapital: 100000,
    maxInvestmentPerTrade: 50000,
    maxProfitDay: 5000,
    maxLossDay: 2000,
    maxTradesDay: 4,
    maxParallelTrades: 3,
    chargesPerTrade: 20,
  },
  tradingParameters: {
    lots: 1,
    lotSize: 65,
    bankniftyLotSize: 30,
    maxStrikeDistanceFromAtm: 5,
    maxPremium: 200,
    minPremium: 30,
    minEntryStrengthPct: 0,
    slType: "Fixed Points",
    slPoints: 15,
    breakevenTriggerPct: 50,
    targetPoints: 10,
    trailingSlPoints: 20,
  },
  strategy: {
    strategyName: "TrendSnap - Momentum crossover strategy",
    timeframe: "3-min",
    indices: {
      NIFTY: true,
      BANKNIFTY: false,
      FINNIFTY: false,
      MIDCPNIFTY: false,
    },
    tradeStart: "09:15",
    tradeEnd: "15:00",
    autoPauseAfterLosses: 3,
  },
  updatedAt: new Date().toISOString(),
};

export function loadTradingSetup(): TradingSetup {
  if (typeof window === "undefined") return DEFAULT_TRADING_SETUP;
  try {
    const raw = window.localStorage.getItem(TRADING_SETUP_KEY);
    if (!raw) return DEFAULT_TRADING_SETUP;
    const parsed = JSON.parse(raw) as TradingSetup;
    return {
      ...DEFAULT_TRADING_SETUP,
      ...parsed,
      master: { ...DEFAULT_TRADING_SETUP.master, ...parsed.master },
      credentials: { ...DEFAULT_TRADING_SETUP.credentials, ...parsed.credentials },
      capitalRisk: { ...DEFAULT_TRADING_SETUP.capitalRisk, ...parsed.capitalRisk },
      tradingParameters: { ...DEFAULT_TRADING_SETUP.tradingParameters, ...parsed.tradingParameters },
      strategy: { ...DEFAULT_TRADING_SETUP.strategy, ...parsed.strategy },
    };
  } catch {
    return DEFAULT_TRADING_SETUP;
  }
}

export function saveTradingSetup(setup: TradingSetup): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    TRADING_SETUP_KEY,
    JSON.stringify({
      ...setup,
      updatedAt: new Date().toISOString(),
    })
  );
}
