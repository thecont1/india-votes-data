import re
import time
import html
import os
import requests
import pandas as pd
from bs4 import BeautifulSoup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_CSV = os.path.join(SCRIPT_DIR, "parties-ac2022-review.csv")
OUTPUT_CSV = os.path.join(SCRIPT_DIR, "parties-ac2022-review.updated.csv")
USER_AGENT = "MaheshElectionTool/1.0 (contact: you@example.com)"

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

def clean_text(s):
    if s is None:
        return ""
    s = html.unescape(str(s))
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def wikipedia_search(title):
    if not title or not isinstance(title, str):
        return None
    params = {
        "action": "query",
        "list": "search",
        "srsearch": title,
        "srlimit": 1,
        "srprop": "",
        "format": "json"
    }
    r = session.get("https://en.wikipedia.org/w/api.php", params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    hits = data.get("query", {}).get("search", [])
    if not hits:
        return None
    hit = hits[0]
    page_title = hit["title"]
    # light sanity check: ensure all main tokens in order
    tokens = [t for t in re.split(r"\W+", title) if t]
    ptr = 0
    lower_title = page_title.lower()
    for tok in tokens:
        idx = lower_title.find(tok.lower(), ptr)
        if idx == -1:
            break
        ptr = idx + len(tok)
    else:
        return "https://en.wikipedia.org/wiki/" + page_title.replace(" ", "_")
    return None

def get_infobox(url):
    if not url or not url.startswith("https://en.wikipedia.org/"):
        return None
    r = session.get(url, timeout=10)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    infobox = soup.find("table", class_=lambda c: c and "infobox" in c)
    return infobox

def extract_from_infobox(infobox):
    if infobox is None:
        return {}
    data = {}

    def label_key(text):
        t = clean_text(text)
        t = t.lower()
        t = re.sub(r"\s+", " ", t)
        t = re.sub(r"[:：]+$", "", t)
        return t

    rows = infobox.find_all("tr")
    kv = {}
    for tr in rows:
        th = tr.find("th")
        td = tr.find("td")
        if not th or not td:
            continue
        key = label_key(th.get_text(" ", strip=True))
        val = clean_text(td.get_text(" ", strip=True))
        kv[key] = (val, td)

    # abbreviation
    for k in kv:
        if "abbreviation" in k:
            data["abbr"] = kv[k][0]
            break

    # chief / leader
    for label in ["leader", "president", "chairperson", "chairman", "general secretary", "convener"]:
        for k in kv:
            if label in k:
                data["chief"] = kv[k][0]
                break
        if "chief" in data:
            break
    if "chief" not in data:
        for k in kv:
            if "founder" in k:
                data["chief"] = kv[k][0]
                break

    # founded
    for label in ["founded", "formation", "established", "launched"]:
        for k in kv:
            if label in k:
                data["founded"] = kv[k][0]
                break
        if "founded" in data:
            break

    # colours
    for label in ["colours", "colours(s)", "color", "colors", "colour", "colour(s)"]:
        for k in kv:
            if label in k:
                data["colour"] = kv[k][0]
                break
        if "colour" in data:
            break

    # symbol image
    inf_img = infobox.find("img")
    if inf_img and inf_img.get("src"):
        src = inf_img["src"]
        if src.startswith("//upload.wikimedia.org/"):
            data["symbol_url"] = "https:" + src
        elif src.startswith("https://upload.wikimedia.org/"):
            data["symbol_url"] = src

    return data

def resolve_wikipedia_url(row):
    url = row.get("wikipedia_url")
    if isinstance(url, str) and url.startswith("https://en.wikipedia.org/"):
        return url, "from_existing_url"

    # try names in order
    candidates = []
    if isinstance(row.get("d1_name"), str):
        candidates.append(row["d1_name"])
    if isinstance(row.get("csv_party_name"), str):
        candidates.append(row["csv_party_name"])
    if isinstance(row.get("d1_abv"), str) and row["d1_abv"].strip():
        candidates.append(row["d1_abv"] + " political party")

    for q in candidates:
        url = wikipedia_search(q)
        if url:
            return url, f"from_search:{q}"
        time.sleep(0.3)
    return None, "no_enwiki_match"

def main():
    df = pd.read_csv(INPUT_CSV)
    if "notes" not in df.columns:
        df["notes"] = ""
    # Convert aliases column to string type to handle NaN values
    if "aliases" in df.columns:
        df["aliases"] = df["aliases"].astype(str)

    total = len(df)
    for i, (idx, row) in enumerate(df.iterrows(), 1):
        print(f"[{i}/{total}] Processing: {row.get('csv_party_name', 'Unknown')}")
        missing = str(row.get("missing_fields", "") or "").strip()
        if not missing:
            print("  -> Skipping (no missing fields)")
            continue

        missing_fields = [f.strip() for f in missing.split(",")] if missing != "ALL" else [
            "d1_abv", "aliases", "chief", "colour", "founded", "symbol_url", "wikipedia_url"
        ]
        notes = []

        wiki_url, how = resolve_wikipedia_url(row)
        if wiki_url:
            print(f"  -> Found Wikipedia URL: {wiki_url}")
            if "wikipedia_url" in missing_fields and not isinstance(row.get("wikipedia_url"), str):
                df.at[idx, "wikipedia_url"] = wiki_url
                notes.append("filled:wikipedia_url")
            notes.append(how)
            infobox = get_infobox(wiki_url)
            info = extract_from_infobox(infobox)
        else:
            info = {}
            notes.append("no_enwiki_match")
            print("  -> No Wikipedia match found")

        # abbreviations / aliases
        if "d1_abv" in missing_fields and info.get("abbr"):
            if not isinstance(row.get("d1_abv"), str) or not row["d1_abv"].strip():
                df.at[idx, "d1_abv"] = info["abbr"]
                notes.append("filled:d1_abv")
        if "aliases" in missing_fields and info.get("abbr"):
            aliases = set()
            if isinstance(row.get("aliases"), str) and row["aliases"].strip():
                aliases.update(a.strip() for a in row["aliases"].split(",") if a.strip())
            if info["abbr"] not in aliases and info["abbr"] != row.get("d1_abv"):
                aliases.add(info["abbr"])
            if aliases:
                df.at[idx, "aliases"] = ",".join(sorted(aliases))
                notes.append("filled:aliases")

        # chief
        if "chief" in missing_fields:
            current = str(row.get("chief") or "").strip()
            if not current and info.get("chief"):
                df.at[idx, "chief"] = info["chief"]
                notes.append("filled:chief")
            elif not current:
                notes.append("missing:chief")

        # founded
        if "founded" in missing_fields:
            current = str(row.get("founded") or "").strip()
            if not current and info.get("founded"):
                df.at[idx, "founded"] = info["founded"]
                notes.append("filled:founded")
            elif not current:
                notes.append("missing:founded")

        # colour
        if "colour" in missing_fields:
            current = str(row.get("colour") or "").strip()
            if not current and info.get("colour"):
                df.at[idx, "colour"] = info["colour"]
                notes.append("filled:colour")
            elif not current:
                notes.append("missing:colour")

        # symbol_url
        if "symbol_url" in missing_fields:
            current = str(row.get("symbol_url") or "").strip()
            if (not current or "upload.wikimedia.org" not in current) and info.get("symbol_url"):
                df.at[idx, "symbol_url"] = info["symbol_url"]
                notes.append("filled:symbol_url")
            elif not current:
                notes.append("missing:symbol_url")

        existing_notes = str(row.get("notes") or "").strip()
        all_notes = ";".join([n for n in notes if n])
        if existing_notes:
            all_notes = existing_notes + ";" + all_notes if all_notes else existing_notes
        df.at[idx, "notes"] = all_notes

        # Save immediately after processing each row
        df.to_csv(OUTPUT_CSV, index=False)
        print(f"  -> Saved progress")

        # be nice to Wikipedia
        time.sleep(0.3)

    print(f"\nCompleted! Output saved to: {OUTPUT_CSV}")

if __name__ == "__main__":
    main()

