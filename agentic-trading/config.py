UNIVERSE = [
    # Space & Aviation (strong catalysts, narrative stocks)
    'RKLB', 'ASTS', 'LUNR', 'JOBY', 'ACHR', 'RCAT',
    # Quantum Computing
    'IONQ', 'RGTI', 'QUBT', 'QBTS',
    # AI & Software
    'PLTR', 'BBAI', 'SOUN', 'AI', 'PATH', 'GTLB', 'DOCN', 'KTOS',
    # Semis & Hardware
    'NVDA', 'AMD', 'SMCI', 'WOLF', 'MRVL', 'ON', 'AMAT',
    # EV (only the ones with real volume & price)
    'TSLA', 'RIVN',
    # Crypto & Bitcoin Miners
    'MSTR', 'COIN', 'MARA', 'CLSK', 'RIOT', 'HUT', 'CORZ', 'IREN', 'WULF',
    # Fintech & Consumer
    'HOOD', 'SOFI', 'UPST', 'AFRM', 'LMND', 'NU', 'SQ', 'PYPL',
    # Biotech & Health
    'RXRX', 'BEAM', 'CRSP', 'NTLA', 'HIMS', 'CELH', 'NVCR',
    # High-Vol Growth
    'CRWD', 'ENPH', 'CAVA', 'APLD', 'W', 'DKNG', 'OPEN',
    # Large vol growth
    'SHOP', 'SNOW', 'DDOG', 'NET', 'MDB',
]

SECTOR_ETFS = ['ARKK', 'SMH', 'QQQ', 'BITO']

# Capital
STARTING_CAPITAL = 100_000.0
MAX_POSITION_PCT = 0.20
MAX_POSITIONS = 4
COMMISSION_PCT = 0.001

# Entry thresholds
MIN_ENTRY_SIGNALS = 2
MIN_RR_RATIO = 0.75   # measured to T1; real edge is in the runner
MIN_PRICE = 8

# Tranches as % of total intended position
TRANCHE_ENTRY = 0.35
TRANCHE_ADD1  = 0.25
TRANCHE_ADD2  = 0.20
TRANCHE_ADD3  = 0.15
TRANCHE_BREAKOUT = 0.05
MAX_ADDS = 4

# Profit levels
PROFIT_T1 = 0.05
PROFIT_T2 = 0.18
PROFIT_T3 = 0.2
SELL_FRACTION = 0.2
RUNNER_A_FRACTION = 0.20
RUNNER_B_FRACTION = 0.20

# Trailing stop ATR multipliers
RUNNER_A_ATR = 2.0
RUNNER_B_ATR = 4.0
SQUEEZE_ATR  = 4.0

# Stop losses
HARD_STOP_PCT       = 0.06
PORTFOLIO_HEAT_MAX  = 0.3
AVG_COST_STOP_PCT   = 0.4
DEAD_MONEY_DAYS     = 3
DEAD_MONEY_MIN_GAIN = 0.03

# Time limits (trading days)
MAX_ADD_DAYS  = 40   # ~8 weeks
REEVAL_DAYS   = 60   # ~12 weeks

# Regime
VIX_THRESHOLD  = 25.0
SPY_SMA_PERIOD = 50

# Technical indicators
RSI_PERIOD    = 14
RSI_LOW       = 35
RSI_HIGH      = 65
STOCH_K       = 14
STOCH_D       = 3
ATR_PERIOD    = 14
BB_PERIOD     = 20
BB_STD        = 2.0
KC_PERIOD     = 20
KC_MULT       = 1.5
VOL_SMA_SHORT = 10
VOL_SMA_LONG  = 20

# Catalyst detection (proxy signals)
CATALYST_PRICE_MOVE = 0.04
CATALYST_VOL_MULT   = 1.8
PULLBACK_LOOKBACK   = 20

# Support detection
SUPPORT_ZONE_PCT    = 0.02   # 2% zone for confluence
SWING_WINDOW        = 5
FIBO_LEVELS         = [0.236, 0.382, 0.5, 0.618, 0.786]

# Backtest
START_DATE = '2025-01-01'
END_DATE   = '2026-06-17'
