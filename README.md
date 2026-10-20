# Stock Screener

A daily shortlist of Indian listed companies that pass a quality-growth screen,
published at **https://paltrivedi04.github.io/stock-screener/**

Every weekday at about 6 PM IST, GitHub runs the screener:

1. **NSE**: official company list and closing prices.
2. **InvestSights**: screens the whole market on market cap, ROE, ROCE, promoter
   holding, P/E and 3-year sales growth.
3. For each company that passes: Debt to Equity, 3-year profit growth and
   operating cash flow from annual statements. **Yahoo Finance** fills cash flow gaps.
4. Concall summary (InvestSights) and transcript / presentation filings (NSE).
5. A daily sample is cross-checked against NSE and Yahoo.
6. The result is compared with the previous day; additions and removals are
   shown on the website and emailed.

Rules live in `screener/config.py`. Setup steps are in `SETUP_GUIDE.md`.

For research only. Not investment advice.
