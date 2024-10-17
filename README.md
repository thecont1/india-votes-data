# india-votes-data
Purpose: 
Generate clean and fresh data from Indian parliamentary and assembly election results published by the Election Commission of India

Detailed description:
This program is a web scraper that extracts election results from the Election Commission of India’s website for various assembly constituencies. Here’s what it’s doing step by step:

	1.	Fetching URLs: The source_url(seq_no) function generates URLs corresponding to specific constituencies based on the sequence number (seq_no). The base URL points to the October 2024 results page, and the sequence number helps iterate through different constituency pages.

	2.	Extracting Data: The extract_results(driver) function uses Selenium to extract constituency-level voting data from the webpage. It retrieves the constituency name and a voting tally for each candidate, including serial number, candidate name, party, and votes from electronic voting machines (EVMs) and postal votes.
	
    3.	Main Loop: The main() function starts the Selenium WebDriver and loads a constituency page. It scrapes the election results, processes them, and stores the data in both JSON and CSV formats. The loop continues for a range of constituencies defined by seq_limit, scraping multiple pages until it either finishes or encounters an error (like a 404).