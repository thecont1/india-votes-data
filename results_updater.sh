#!/bin/bash

# Run the scraper
python3 eci-scraper2-DL.py

# Get current time in HH:MM format
current_time=$(date +"%H:%M")

# Add files to git
git add ./results/2025AC-DL.csv
git add ./results/2025AC-DL.json

# Commit with timestamp
git commit -m "NCT of Delhi Results: $current_time"

# Push to GitHub
git push
