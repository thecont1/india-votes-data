# India Votes Data

A Python-based web scraping tool for extracting and processing election results data from the Election Commission of India's website.

## Overview

This project provides an automated tool to collect, clean, and store Indian parliamentary and assembly election results in structured formats (CSV and JSON). It focuses on extracting detailed constituency-wise voting data including candidate information, party affiliations, and vote counts.

## Features

- Automated scraping of election results from ECI website

- Support for multiple states in a single unified script (coming soon)

- Data extraction for parliamentary and assembly constituencies

- Data extraction for bye-election sets (coming soon)

- Output in both CSV and JSON formats

- Robust error handling and retry mechanisms

- Headless browser operation for efficient scraping. Run it and grab a quick coffee!

## Data Format

All datasets are also available at the [dedicated Kaggle repository](https://www.kaggle.com/datasets/maheshshantaram/indian-elections-fresh-data/) for **India Votes Data**.

### CSV Format

The data is stored in CSV files (e.g., `2024ACHR.csv`, `2024ACJH.csv`) with the following columns:

- Election Year

- Election Type (Parliamentary or Assembly)

- Election State

- Serial Number (of candidate within their constituency)

- Candidate Name

- Party Affiliation

- EVM Votes

- Postal Votes

### JSON Format

The JSON files (e.g., `2024ACHR.json`, `2024ACJH.json`) contain detailed constituency-wise data including:

- Assembly Constituency Name

- Voting Tally (per candidate)
  - Serial Number
  - Candidate Details
  - Party Information
  - Vote Counts (EVM and Postal)

## Requirements

- Python 3.x

- Selenium WebDriver

- Chrome Browser

- Required Python packages:
  - selenium
  - csv
  - json

## Setup

1.Clone the repository:

```bash
git clone https://github.com/thecont1/india-votes-data.git
```

2.Install required packages:

```bash
pip install selenium
```

3.Ensure Chrome browser is installed on your system

## Usage

Run the scraper script:

```bash
python eci-scraper2.py
```

The script will automatically handle data collection for multiple states and generate corresponding CSV and JSON output files.

## Data Files

The repository includes processed data files for various states:

- Haryana: `2024ACHR.csv`, `2024ACHR.json`

- Jharkhand: `2024ACJH.csv`, `2024ACJH.json`

- Jammu & Kashmir: `2024ACJK.csv`, `2024ACJK.json`

- Maharashtra: `2024ACMH.csv`, `2024ACMH.json`

## Implementation Details

The scraper uses Selenium WebDriver with the following optimizations:

- Headless mode for better performance

- Disabled image loading

- Custom user agent

- Robust error handling and timeouts

- Automatic retry mechanisms

## License

(Still under consideration)
