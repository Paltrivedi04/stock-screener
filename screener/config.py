"""
Your screening rules. To change a rule, edit the number here and save.
Everything else updates automatically on the next daily run.
"""

# ---------- Stage 1: screened directly on InvestSights ----------
MARKET_CAP_MIN_CR = 2000        # Market cap above Rs 2,000 Cr
MARKET_CAP_MAX_CR = 70000       # Market cap below Rs 70,000 Cr
ROE_MIN = 18                    # ROE above 18%
ROCE_MIN = 18                   # ROCE above 18%

# ---------- Stage 2: checked company by company ----------
DE_MAX = 2.0                    # Debt to Equity below 2
OCF_POSITIVE_YEARS = 1          # Operating cash flow positive in the latest year (FY26)

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

# ---------- Caching and limits ----------
STATEMENTS_CACHE_DAYS = 7       # annual statements re-fetched weekly
CONCALL_CACHE_DAYS = 7          # concall summary re-fetched weekly
DAILY_REQUEST_BUDGET = 560      # free plan allows 600/day; keep a safety margin
SECONDS_BETWEEN_REQUESTS = 1.0  # free plan allows 120 per minute


def stage1_filters():
    return {
        "market_cap": {"gte": MARKET_CAP_MIN_CR, "lte": MARKET_CAP_MAX_CR},
        "roe": {"gt": ROE_MIN},
        "roce": {"gt": ROCE_MIN},
        "sector": {"not_in": EXCLUDED_SECTORS},
    }


def missing_data_filters():
    """Same as stage 1 but without ROE and ROCE, to find companies where
    those numbers are blank (for example recent IPOs)."""
    f = stage1_filters()
    for k in ("roe", "roce"):
        f.pop(k)
    return f


def rules_text():
    """Human readable list shown on the website."""
    years = "the latest year (FY26)" if OCF_POSITIVE_YEARS == 1 else f"each of the last {OCF_POSITIVE_YEARS} years"
    return [
        f"Market cap between Rs {MARKET_CAP_MIN_CR:,} Cr and Rs {MARKET_CAP_MAX_CR:,} Cr",
        f"ROE above {ROE_MIN}%",
        f"ROCE above {ROCE_MIN}%",
        f"Operating cash flow positive in {years}",
        f"Debt to Equity below {DE_MAX:g}",
        "Banks and NBFCs excluded",
    ]
