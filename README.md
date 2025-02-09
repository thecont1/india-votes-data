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

- Headless browser operation for maximum efficiency. Grab your ☕ before you run the script!

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

Configure and run the program:

1. Set the base URL for your target state:

```python
# Example: Delhi Assembly Election results
base_url = "https://results.eci.gov.in/ResultAcGenFeb2025/ConstituencywiseU05"
```

2. Optionally, adjust the constituency limit:

```python
# Set a high number - program will auto-stop when done
seq_limit = 1000  # Will stop automatically when no more constituencies are found
```

The program intelligently detects when it has processed all available constituencies and stops automatically.

### Output

The script will generate two types of files in the `results` directory:

- CSV file: `YYYYAssembly-XX.csv` (e.g., `2024Assembly-HR.csv`)

- JSON file: `YYYYAssembly-XX.json` (e.g., `2024Assembly-HR.json`)

where:

- `YYYY`: Election year

- `XX`: Two-letter state code

### Superfast Pull & Push

To automatically download election results and quickly commit to GitHub, use the provided shell script:

```bash
./results_updater.sh
```

This script will:

- Run the scraper for Delhi

- Add new results to git

- Commit with timestamp

- Push to GitHub

## Data Files

The repository includes processed data files for various states:

- Haryana: `2024Assembly-HR.csv`, `2024Assembly-HR.json`

- Jharkhand: `2024Assembly-JH.csv`, `2024Assembly-JH.json`

- Jammu & Kashmir: `2024Assembly-JK.csv`, `2024Assembly-JK.json`

- Maharashtra: `2024Assembly-MH.csv`, `2024Assembly-MH.json`

- NCT of Delhi: `2025Assembly-DL.csv`, `2025Assembly-DL.json`

## Implementation Details

The scraper uses Selenium WebDriver with the following optimizations and features:

- Headless mode for better performance

- Disabled image loading

- Custom user agent

- Robust error handling and timeouts

- Automatic retry mechanisms

- Pandas-based data processing

- State code validation against master data

- Standardized file naming convention

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

You are free to:
- Use this code commercially

- Modify the code

- Distribute the code

- Use it privately

Under the following conditions:

- Include the original license and copyright notice
