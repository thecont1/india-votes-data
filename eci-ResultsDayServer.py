"""
ECI Results Scraper - Backward Compatibility Module

This module re-exports functions from core/ for backward compatibility.
New code should import directly from core.scraper.
"""

# Re-export commonly used functions
from core.scraper import (
    build_constituency_url,
    build_roundwise_url,
    extract_results,
    extract_roundwise_results,
    get_state_code,
    parse_partywise_url,
    scrape_ac_rounds_core,
    scrape_constituency_sync,
)
from core.browser import create_chrome_driver

__all__ = [
    'build_constituency_url',
    'build_roundwise_url',
    'create_chrome_driver',
    'extract_results',
    'extract_roundwise_results',
    'get_state_code',
    'parse_partywise_url',
    'scrape_ac_rounds_core',
    'scrape_constituency_sync',
]