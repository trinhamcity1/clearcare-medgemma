import pandas as pd
import config


def market_regime(spy: pd.DataFrame, vix: pd.DataFrame, idx: int) -> str:
    if idx < config.SPY_SMA_PERIOD:
        return 'RED'

    spy_close = spy['Close']
    spy_sma = spy_close.rolling(config.SPY_SMA_PERIOD).mean()

    spy_ok = spy_close.iloc[idx] > spy_sma.iloc[idx]

    vix_ok = True
    if not vix.empty:
        try:
            vix_date = spy.index[idx]
            if vix_date in vix.index:
                vix_val = vix['Close'].loc[vix_date]
            else:
                vix_val = vix['Close'].iloc[
                    vix.index.get_indexer([vix_date], method='pad')[0]]
            vix_ok = float(vix_val) < config.VIX_THRESHOLD
        except Exception:
            vix_ok = True

    return 'GREEN' if (spy_ok and vix_ok) else 'RED'


def sector_in_uptrend(sector_etf: pd.DataFrame, idx: int,
                       period: int = 20) -> bool:
    if idx < period or sector_etf.empty:
        return True
    close = sector_etf['Close']
    ma = close.rolling(period).mean()
    return bool(close.iloc[idx] > ma.iloc[idx])
