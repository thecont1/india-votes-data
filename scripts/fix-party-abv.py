#!/usr/bin/env python3
"""Fix mismatched party_abv values in rounds_ac for AC-2022-03."""
import json, subprocess, os, re

WORKER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'worker')

def wrangler_sql(sql):
    """Execute SQL via wrangler, return parsed results."""
    r = subprocess.run(
        ['npx', 'wrangler', 'd1', 'execute', 'election-results', '--remote', f'--command={sql}'],
        capture_output=True, text=True, timeout=30, cwd=WORKER_DIR
    )
    # Extract JSON from stdout (skip wrangler banner lines)
    for line in r.stdout.split('\n'):
        line = line.strip()
        if line.startswith('[{'):
            return json.loads(line)
    return None

def wrangler_file(path):
    """Execute SQL file via wrangler."""
    r = subprocess.run(
        ['npx', 'wrangler', 'd1', 'execute', 'election-results', '--remote', f'--file={path}'],
        capture_output=True, text=True, timeout=30, cwd=WORKER_DIR
    )
    return r.returncode == 0

# Step 1: Get all parties from D1 (name -> abv)
print("Loading parties from D1...")
r = subprocess.run(
    ['npx', 'wrangler', 'd1', 'execute', 'election-results', '--remote',
     "--command=SELECT abv, name FROM parties WHERE abv IS NOT NULL AND abv != '';"],
    capture_output=True, text=True, timeout=30, cwd=WORKER_DIR
)
parties = {}
for line in r.stdout.split('\n'):
    line = line.strip()
    if line.startswith('[{'):
        data = json.loads(line)
        for row in data[0]['results']:
            parties[row['name']] = row['abv']
print(f"  {len(parties)} parties loaded")

# Step 2: Get unmapped party_abv values from rounds_ac
print("Finding unmapped party_abv values...")
r2 = subprocess.run(
    ['npx', 'wrangler', 'd1', 'execute', 'election-results', '--remote',
     "--command=SELECT DISTINCT party_abv FROM rounds_ac WHERE election_id = 'AC-2022-03' AND party_abv NOT IN (SELECT abv FROM parties);"],
    capture_output=True, text=True, timeout=30, cwd=WORKER_DIR
)
unmapped = []
for line in r2.stdout.split('\n'):
    line = line.strip()
    if line.startswith('[{'):
        data = json.loads(line)
        unmapped = [row['party_abv'] for row in data[0]['results']]
print(f"  {len(unmapped)} unmapped party_abv values")

# Step 3: Match truncated names to full party names
fixes = []
still_unmapped = []
for name in unmapped:
    trimmed = name.strip()
    matches = [(full, abv) for full, abv in parties.items() if full.startswith(trimmed)]
    if matches:
        matches.sort(key=lambda x: len(x[0]))
        fixes.append((name, matches[0][1], matches[0][0]))
    else:
        still_unmapped.append(name)

print(f"  Fixable: {len(fixes)}")
print(f"  Still unmapped: {len(still_unmapped)}")

# Step 4: Generate and execute UPDATE statements
if fixes:
    stmts = []
    for old, new, full in fixes:
        old_esc = old.replace("'", "''")
        new_esc = new.replace("'", "''")
        stmts.append(f"UPDATE rounds_ac SET party_abv = '{new_esc}' WHERE election_id = 'AC-2022-03' AND party_abv = '{old_esc}';")

    print(f"\nExecuting {len(stmts)} UPDATE statements...")
    # Execute in batches of 20
    for i in range(0, len(stmts), 20):
        batch = stmts[i:i+20]
        tmp = '/tmp/fix_party_batch.sql'
        with open(tmp, 'w') as f:
            f.write('\n'.join(batch))
        ok = wrangler_file(tmp)
        print(f"  Batch {i//20 + 1}: {'ok' if ok else 'FAILED'}")
        os.unlink(tmp)

    print("\nDone. Party abbreviations fixed.")
else:
    print("\nNothing to fix.")

if still_unmapped:
    print(f"\n{len(still_unmapped)} parties still unmapped (micro-parties with no D1 match):")
    for n in still_unmapped[:10]:
        print(f"  '{n.strip()}'")
