"""
Your screening rules. To change a rule, edit the number here and save.
Everything else updates automatically on the next daily run.
"""

# ---------- Stage 1: screened directly on InvestSights ----------
MARKET_CAP_MIN_CR = 2000        # Market cap above Rs 2,000 Cr
MARKET_CAP_MAX_CR = 70000       # Market cap below Rs 70,000 Cr
ROE_MIN = 18                    # ROE above 18%
ROCE_MIN = 18                   # ROCE above 18%
PROMOTER_MIN = 40               # Promoter holding above 40%
PE_MAX = 75                     # P/E below 75 (and above 0)
SALES_CAGR_3Y_MIN = 25          # Sales growth, 3-year CAGR above 25%

# ---------- Stage 2: checked company by company ----------
DE_MAX = 1.0                    # Debt to Equity below 1
PROFIT_CAGR_3Y_MIN = 25         # Profit growth, 3-year CAGR above 25%
OCF_POSITIVE_YEARS = 3          # Operating cash flow positive in each of the last 3 years

# Sectors left out (banks, NBFCs and other financials)
EXCLUDED_SECTORS = [
    "Financial Services",
    "Non Banking Financial Company (NBFC)",
    "Other Financial Services",
    "Financial Technology (Fintech)",
]

# ---------- Cross-checks ----------
CROSSCHECK_SAMPLE_SIZE = 15     # companies sampled each day
MISMATCH_TOLERANCE_PCT = 10     # a gap above this is flagged

# ---------- Caching (saves your free InvestSights quota) ----------
STATEMENTS_CACHE_DAYS = 7       # annual statements re-fetched weekly
CONCALL_CACHE_DAYS = 7          # concall summary re-fetched weekly
DAILY_REQUEST_BUDGET = 95       # stop before the free plan's 100/day limit


def stage1_filters():
    return {
        "market_cap": {"gte": MARKET_CAP_MIN_CR, "lte": MARKET_CAP_MAX_CR},
        "roe": {"gt": ROE_MIN},
        "roce": {"gt": ROCE_MIN},
        "promoter_holding": {"gt": PROMOTER_MIN},
        "pe_ratio": {"gt": 0, "lt": PE_MAX},
        "revenue_growth_3y_cagr": {"gt": SALES_CAGR_3Y_MIN},
        "sector": {"not_in": EXCLUDED_SECTORS},
    }


def rules_text():
    """Human readable list shown on the website."""
    return [
        f"Market cap between Rs {MARKET_CAP_MIN_CR:,} Cr and Rs {MARKET_CAP_MAX_CR:,} Cr",
        f"ROE above {ROE_MIN}%",
        f"ROCE above {ROCE_MIN}%",
        f"Debt to Equity below {DE_MAX:g}",
        f"Promoter holding above {PROMOTER_MIN}%",
        f"P/E below {PE_MAX}",
        f"Sales growth (3-year CAGR) above {SALES_CAGR_3Y_MIN}%",
        f"Profit growth (3-year CAGR) above {PROFIT_CAGR_3Y_MIN}%",
        f"Operating cash flow positive in each of the last {OCF_POSITIVE_YEARS} years",
        "Banks and NBFCs excluded",
    ]
