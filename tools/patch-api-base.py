#!/usr/bin/env python3
"""Patch static/index.html to add API_BASE for cross-origin Worker calls."""

import re

with open('static/index.html', 'r') as f:
    content = f.read()

# Add API_BASE after prefersReducedMotion line
old_line = "const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');"
new_line = old_line + """
const API_BASE = location.hostname === 'localhost' || location.hostname === '127.0.0.1'
  ? '' : 'https://election-api.thecontrarian.workers.dev';"""
content = content.replace(old_line, new_line)

# Replace fetch('/api/...') with fetch(API_BASE + '/api/...')
content = content.replace("fetch('/api/", "fetch(API_BASE + '/api/")

# Replace fetch(`/api/...`) with fetch(`${API_BASE}/api/...`)
content = content.replace("fetch(`/api/", "fetch(`${API_BASE}/api/")

# Replace url = '/api/... with url = API_BASE + '/api/...
content = content.replace("url = '/api/", "url = API_BASE + '/api/")

# Replace let url = '/api/... (already covered by above)

with open('static/index.html', 'w') as f:
    f.write(content)

# Count replacements
import subprocess
result = subprocess.run(['grep', '-c', 'API_BASE', 'static/index.html'], capture_output=True, text=True)
print(f"API_BASE references: {result.stdout.strip()}")
print("Done")
