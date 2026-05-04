# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

India Votes Data is a Python-based web scraper that extracts election results from the Election Commission of India (ECI) website. The project uses Selenium WebDriver to automate data collection and outputs structured data in both CSV and JSON formats.

## Development Environment

### Setup
```bash
# Clone and navigate to repository
git clone https://github.com/thecont1/india-votes-data.git
cd india-votes-data

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### Running the Scraper

**Basic usage** (scrapes default 3 constituencies):
```bash
python eci-scraper2.py
```

**Scrape specific number of constituencies**:
```bash
python eci-scraper2.py 100  # Scrapes up to 100 constituencies
```

**Automated workflow** (scrape + commit + push):
```bash
./results_updater.sh
```

### Key Commands

**Code formatting**:
```bash
black eci-scraper2.py
```

**Code quality checks**:
```bash
pylint eci-scraper2.py
```

**Data analysis** (Jupyter notebook):
```bash
jupyter notebook analyse-this.ipynb
```

## Architecture

### Core Components

**eci-scraper2.py** - Main scraper script with three key functions:

1. `source_url(seq_no)` - Generates ECI constituency page URLs
   - Modify `base_url` variable (line 14) to target different states/elections
   - URL pattern: `https://results.eci.gov.in/ResultAcGenNov2025/ConstituencywiseS04{seq_no}.htm`

2. `get_state_code(state_name)` - Maps state names to 2-letter codes
   - Uses `states.csv` as reference data
   - Returns standardized state codes (e.g., "NCT of Delhi" → "DL")

3. `extract_results(driver)` - Parses constituency page HTML
   - Extracts: constituency number, name, candidate details, vote counts
   - Returns structured dictionary with voting tally

**main()** execution flow:
1. Initializes Chrome WebDriver with headless mode and optimizations
2. Scrapes first page to determine election metadata (year, type, state)
3. Iterates through constituency pages until 404 or limit reached
4. Outputs timestamped JSON and CSV files to `./results/` directory

### Data Flow

```
ECI Website → Selenium WebDriver → extract_results() → 
    → In-memory dict → JSON file (detailed) + CSV file (flattened)
```

### File Structure

- `eci-scraper2.py` - Main scraper (configurable for any state)
- `states.csv` - Master reference for state codes and metadata
- `results/` - Output directory for scraped data
- `analyse-this.ipynb` - Jupyter notebook for data analysis
- `results_updater.sh` - Automation script for scraping + Git workflow

### Output Format

**Filename convention**: `{YEAR}{ElectionType}-{StateCode}_{timestamp}.{ext}`
- Example: `2024Assembly-HR_20241116_203800.json`

**CSV columns**: `election_year`, `election_type`, `election_state`, `constituency`, `constituency_no`, `serial_no`, `candidate`, `party`, `evm_votes`, `postal_votes`

**JSON structure**: Hierarchical format with `constituencywise_results` array containing nested voting data per constituency

## Important Behaviors

### Scraper Configuration

**To scrape a different state/election**:
1. Update `base_url` in `source_url()` function (line 14)
2. Ensure URL pattern matches ECI website structure
3. Run scraper - state detection is automatic

**Auto-stop mechanism**: Scraper automatically terminates when:
- 404 page is encountered
- Specified limit is reached
- Fatal exception occurs

### Performance Optimizations

The Chrome WebDriver uses:
- `--headless=new` - Runs without GUI
- `--blink-settings=imagesEnabled=false` - Disables image loading
- Custom user agent for compatibility
- `WebDriverWait` with 10-second timeout for element loading

### Error Handling

Script terminates gracefully on:
- `NoSuchElementException` - Missing HTML elements
- `TimeoutException` - Page load timeout
- Network connectivity issues

Both JSON and CSV files are written in `finally` block to ensure data preservation even on partial scrapes.

## Dependencies

**Required**:
- `selenium>=4.15.0` - Web automation
- `pandas>=2.1.0` - Data processing (CSV operations, state code lookup)

**Optional** (for analysis):
- `jupyter>=1.0.0`
- `matplotlib>=3.8.0`
- `seaborn>=0.13.0`

**Development**:
- `black>=23.11.0` - Code formatting
- `pylint>=3.0.0` - Linting

## Data Sources

- **Election Commission of India**: https://results.eci.gov.in/
- **Kaggle Repository**: https://www.kaggle.com/datasets/maheshshantaram/indian-elections-fresh-data/
- **Analysis Notebook**: https://www.kaggle.com/code/maheshshantaram/elections-analysis-ready-reckoner

## Notes

- Chrome browser is auto-installed by Selenium if not present
- Virtual environment recommended to avoid dependency conflicts
- `states.csv` must exist for state code validation
- Results directory must exist before running scraper
- Git workflow assumes remote repository is configured
