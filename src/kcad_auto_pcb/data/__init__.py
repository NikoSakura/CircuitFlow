"""Data layer for PCB design knowledge.

Architecture: multi-tier data sources with fallback chain.
- Tier 1: Local SQLite cache (instant, offline)
- Tier 2: Bundled knowledge base (ships with package)
- Tier 3: External APIs (online, requires API keys)
- Tier 4: Community/open data
"""
