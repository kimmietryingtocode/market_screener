import yfinance as yf
import pandas as pd
from pathlib import Path

sectors = {
    'XLY': 'Consumer Cyclical', 
    'XLV': 'Healthcare', 
    'XLP': 'Consumer Defensive', 
    'XLE': 'Energy', 
    'XLF': 'Financial Services', 
    'XLI': 'Industrials', 
    'XLK': 'Technology', 
    'XLB': 'Materials', 
    'XLRE': 'Real Estate', 
    'XLU': 'Utilities', 
    'XLC': 'Communication Services'
}
One_month = 21
Three_months = 63
Six_months = 126
One_year = 252
data = yf.download(list(sectors.keys())+ ["SPY"], period="1y", interval="1d",auto_adjust=True, progress=False)
print(data.head())
print(data.columns)

def pct_returns(close: pd.Series, days: int):
    close = close.dropna()
    if len(close) <= days:
        return float("nan")
    return close.iloc[-1] / close.iloc[-days - 1] - 1

def annualized_volatility(close: pd.Series, days: int):
    '''Calculates volatility for risk based on Kissell’s book'''
    close = close.dropna()
    returns = close.pct_change().dropna()
    recent = returns.tail(days)
    return recent.std() * (252 ** 0.5)

def max_drawdown(close: pd.Series, days: int = None):
    '''Calculates max drawdown for risk based on Kissell’s book'''
    recent = close.dropna().tail(days)
    running_max = recent.cummax()
    drawdown = recent / running_max - 1
    return drawdown.min()


rows = []
for ticker, sector in sectors.items():
    close = data["Close"][ticker]
    return_1m = pct_returns(close, One_month)
    return_3m = pct_returns(close, Three_months)
    return_6m = pct_returns(close, Six_months)
    return_1y = pct_returns(close, One_year)
    volatility_3m = annualized_volatility(close, Three_months)
    drawdown_3m = max_drawdown(close, Three_months)
    spy_3m_return = pct_returns(data["Close"]["SPY"], Three_months)
    row = {
        "sector_etf": ticker,
        "sector": sector,
        "return_1m": return_1m,
        "return_3m": return_3m,
        "return_6m": return_6m,
        "return_1y": return_1y,
        "volatility_3m": volatility_3m,
        "drawdown_3m": drawdown_3m,
        "relative_strength_vs_spy": return_3m - spy_3m_return,
    }
    rows.append(row)
df = pd.DataFrame(rows)

output_path = Path("data/sector_etf_scores.csv")
output_path.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(output_path)
print(f"Wrote {output_path}")