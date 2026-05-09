# India Votes Data

A Python-based tool for extracting, processing and archiving election results data from the [Election Commission of India](https://www.eci.gov.in/) website.

## Overview

This project provides an automated tool to collect, clean, and store Indian parliamentary and assembly election results in structured formats (CSV and JSON). It focuses on extracting detailed constituency-wise voting data including candidate information, party affiliations, and vote counts.

## Features

- Automated extraction of election results from ECI website within seconds

- Support for multiple states in a single unified script

- Output in both CSV and JSON formats

- Data extraction for bye-election sets (coming soon)

- Robust error handling with automatic termination

- Headless browser operation for maximum efficiency. ~~Run the script and go grab a ☕~~

- Superfast multi-threaded scraping (up to 5 workers) - no time for coffee!

<!--
Throughput comparison:
- Before multi-threading: ~2.4 constituencies/second (140 in 59s)
- After multi-threading: ~6.5 constituencies/second (824 in 126s)
- That's ~2.7x faster throughput!
-->

## Data Format

All datasets are also available at the [dedicated Kaggle repository](https://www.kaggle.com/datasets/maheshshantaram/indian-elections-fresh-data/) for **India Votes Data**.

A handy Kaggle notebook to provide for quick data analysis is over here:
[https://www.kaggle.com/code/maheshshantaram/elections-analysis-ready-reckoner](https://www.kaggle.com/code/maheshshantaram/elections-analysis-ready-reckoner)

### CSV Format

The data is stored in CSV files (e.g., `2024Assembly-HR.csv`, `2024Assembly-JH.csv`) with the following columns:

- `election_year`: Year of the election

- `election_type`: Type of election (Assembly/Parliamentary)

- `election_state`: Full name of the state

- `state_code`: Two-letter state code

- `constituency`: Name of the constituency

- `serial_no`: Candidate's serial number

- `candidate`: Candidate's name

- `party`: Party affiliation

- `evm_votes`: Votes from Electronic Voting Machines

- `postal_votes`: Postal ballot votes

### JSON Format

The JSON files (e.g., `2024Assembly-HR.json`, `2024Assembly-JH.json`) contain detailed constituency-wise data including:

- `election_year`: Year of the election

- `election_type`: Type of election

- `election_state`: State code

- `constituencywise_results`: Constituency-wise results

  - `constituency_number`: Numeric ID

  - `constituency`: Name of constituency

  - `voting_tally`: Voting tally

    - `serial_no`: Candidate's serial number

    - `candidate`: Candidate's name

    - `party`: Party affiliation

    - `evm_votes`: EVM votes received

    - `postal_votes`: Postal votes received

## Requirements

- Python 3.x

- Selenium WebDriver

- Chrome Browser

- Required Python packages: (refer `requirements.txt`)
  - selenium
  - pandas
  - csv
  - json

## Setup

1. Clone the repository:

```bash
git clone https://github.com/thecont1/india-votes-data.git
cd india-votes-data
```

2. Create and activate a virtual environment (recommended):

```bash
python -m venv venv

# On Windows
venv\Scripts\activate

# On macOS/Linux
source venv/bin/activate
```

3. Install required packages:

```bash
pip install -r requirements.txt
```

4. If the Chrome browser isn't already installed on your system, Selenium will automatically do the job. Give it a minute or two.

## Usage

### Basic Usage

Configure and run the program via command line:

```bash
# Required: Provide the party-wise results URL
python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm"

# Optional: Specify number of constituencies (default: 3)
python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" 50

# Optional: Use --respect mode for single-threaded respectful scraping
python eci-scraper2.py --url "https://results.eci.gov.in/ResultAcGenMay2026/partywiseresult-S22.htm" --respect
```

The program intelligently detects when it has processed all available constituencies and stops automatically.

### Output

The script will generate two types of files in the `results` directory:

- CSV file: `YYYYAssembly-XX.csv` (e.g., `2024Assembly-HR.csv`)

- JSON file: `YYYYAssembly-XX.json` (e.g., `2024Assembly-HR.json`)

where:

- `YYYY`: Election year

- `XX`: Two-letter state code

## Data Files

The repository includes processed data files for various states:

- Haryana: `2024Assembly-HR.csv`, `2024Assembly-HR.json`

- Jharkhand: `2024Assembly-JH.csv`, `2024Assembly-JH.json`

- Jammu & Kashmir: `2024Assembly-JK.csv`, `2024Assembly-JK.json`

- Maharashtra: `2024Assembly-MH.csv`, `2024Assembly-MH.json`

- NCT of Delhi: `2025Assembly-DL.csv`, `2025Assembly-DL.json`

## Implementation Details

The scraper uses Selenium WebDriver with the following optimizations and features:

- Multi-threaded scraping (default: up to 5 concurrent workers)
- Single-threaded `--respect` mode for server-friendly operation (1s pause every 10 URLs)
- Headless mode for better performance
- Disabled image loading
- Custom user agent
- Robust error handling and timeouts
- Automatic detection of end-of-results (404 page)
- Results sorted by constituency_no (ascending) and serial_no (ascending)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

You are free to:
- Use this code commercially

- Modify the code

- Distribute the code

- Use it privately

Under the following conditions:

- Include the original license and copyright notice
