"""Yahoo Finance market data provider -- fetches via httpx (no yfinance dependency).

Supports stocks, ETFs, indices, forex, crypto (Yahoo-style tickers).
Example tickers: AAPL, SPY, BTC-USD, EURUSD=X, GLD, TLT
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from core.models.market import MarketData

logger = logging.getLogger(__name__)

PLUGIN_META = {
    "name": "yahoo_finance",
    "display_name": "Yahoo Finance",
    "description": "Stocks, ETFs, crypto, forex -- free, no API key required",
    "category": "market_data",
    "protocols": ["market_data"],
    "class_name": "YahooFinanceProvider",
    "pip_dependencies": [],
    "setup_instructions": """
Yahoo Finance requires no API key -- it's free and public.
Just add the tickers you want to track.

Supported ticker formats:
  Stocks/ETFs: AAPL, NVDA, SPY, QQQ
  Crypto:      BTC-USD, ETH-USD, SOL-USD
  Forex:       EURUSD=X, GBPUSD=X
  Commodities: GC=F (gold), CL=F (oil)
  Bonds:       TLT, ^TNX (10Y yield)
""",
    "config_fields": [
        {
            "key": "tickers",
            "label": "Tickers to track",
            "type": "list",
            "required": True,
            "default": ["AAPL", "NVDA", "SPY", "QQQ", "BTC-USD"],
            "description": "Comma-separated list of ticker symbols",
            "placeholder": "AAPL, NVDA, SPY, BTC-USD, ETH-USD",
        },
    ],
}

# Yahoo Finance chart API endpoint
_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# Map interval strings to Yahoo API periods
_RANGE_MAP = {
    "1d": "1d",
    "5d": "5d",
    "1mo": "1mo",
    "3mo": "3mo",
    "6mo": "6mo",
    "1y": "1y",
    "2y": "2y",
    "5y": "5y",
    "max": "max",
}


class YahooFinanceProvider:
    """Fetches market data from Yahoo Finance via their public chart API.

    Implements the MarketDataProvider protocol.
    """

    def __init__(self, tickers: list[str] | None = None) -> None:
        self._tickers = set(tickers or [])
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "OpenSuperFin/0.1"},
        )

    @property
    def name(self) -> str:
        return "yahoo_finance"

    def _normalize_ticker(self, ticker: str) -> str:
        """Map common shorthand symbols to Yahoo's expected format."""
        upper = ticker.upper()
        aliases = {
            "BTC": "BTC-USD",
            "ETH": "ETH-USD",
            "SOL": "SOL-USD",
        }
        return aliases.get(upper, upper)

    def supports(self, ticker: str) -> bool:
        """Yahoo Finance supports most traditional tickers."""
        normalized = self._normalize_ticker(ticker)
        if self._tickers:
            normalized_list = {self._normalize_ticker(t) for t in self._tickers}
            return normalized in normalized_list
        # Yahoo supports almost anything, so default to True
        return True

    async def fetch(
        self,
        tickers: list[str],
        start: datetime,
        end: datetime,
    ) -> list[MarketData]:
        """Fetch historical daily OHLCV data for the given tickers."""
        results: list[MarketData] = []

        for ticker in tickers:
            try:
                data = await self._fetch_ticker(ticker, start, end)
                results.extend(data)
            except Exception:
                logger.exception("Failed to fetch data for %s", ticker)

        return results

    async def _fetch_ticker(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
    ) -> list[MarketData]:
        """Fetch data for a single ticker."""
        params = {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "includePrePost": "false",
        }

        query_ticker = self._normalize_ticker(ticker)
        url = _CHART_URL.format(ticker=query_ticker)
        response = await self._client.get(url, params=params)

        if response.status_code != 200:
            logger.warning(
                "Yahoo Finance returned %d for %s", response.status_code, query_ticker
            )
            return []

        data = response.json()
        chart = data.get("chart", {})
        result = chart.get("result")

        if not result:
            error = chart.get("error", {})
            logger.warning("Yahoo Finance error for %s: %s", query_ticker, error)
            return []

        return self._parse_chart_result(ticker, result[0])

    def _parse_chart_result(self, ticker: str, result: dict) -> list[MarketData]:
        """Parse Yahoo Finance chart API response into MarketData objects."""
        timestamps = result.get("timestamp", [])
        indicators = result.get("indicators", {})
        quote = indicators.get("quote", [{}])[0]
        adj_close_data = indicators.get("adjclose", [{}])

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        records: list[MarketData] = []

        for i, ts in enumerate(timestamps):
            close = closes[i] if i < len(closes) else None
            if close is None:
                continue  # skip days with no data

            dt = datetime.fromtimestamp(ts, tz=timezone.utc)

            # available_at = market close time (same as timestamp for daily data)
            records.append(MarketData(
                ticker=ticker,
                timestamp=dt,
                available_at=dt,
                open=opens[i] if i < len(opens) else None,
                high=highs[i] if i < len(highs) else None,
                low=lows[i] if i < len(lows) else None,
                close=close,
                volume=float(volumes[i]) if i < len(volumes) and volumes[i] else None,
                source="yahoo_finance",
                data_type="price",
            ))

        return records

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
