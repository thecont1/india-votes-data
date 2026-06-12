/**
 * D1 Query Cost Linter
 * Scans all query files for patterns known to cause D1 read explosions.
 * Run with: node scripts/lint-d1-queries.js
 * Exit code 1 if any violations found (wire into CI to block deploys).
 *
 * What it catches:
 *   - COALESCE with a correlated subquery on rounds_ac (the exact pattern
 *     that caused the $168/month billing explosion)
 *   - SELECT * from large tables without LIMIT
 *
 * What it intentionally ignores:
 *   - CTEs with FROM rounds_ac (these are single-pass scans, not correlated)
 *   - Main SELECT FROM rounds_ac with WHERE (indexed lookups)
 *   - Subqueries on other tables (elections, states, etc.)
 */

import { readFileSync, readdirSync, statSync } from "fs";
import { join } from "path";

const QUERY_DIRS = ["worker/queries", "worker-ingest"];

/**
 * Patterns that indicate a D1 read-cost explosion risk.
 * Each pattern is tested against individual SQL string literals,
 * not across the entire file — avoids false positives from CTEs.
 */
const BANNED_PATTERNS = [
  {
    // The exact pattern that caused $168/month:
    // COALESCE( (SELECT ... FROM rounds_ac ...), (SELECT ... FROM rounds_ac ...) )
    // This re-scans rounds_ac per outer row — catastrophic at scale.
    regex: /COALESCE\s*\(\s*\(\s*SELECT[^)]*FROM\s+rounds_ac\b/gis,
    message:
      "COALESCE with correlated rounds_ac subquery detected. " +
      "Replace with a single-pass window function or latest_rounds_ac JOIN. " +
      "This pattern re-scans 246k rows per outer row.",
    severity: "ERROR",
  },
  {
    // SELECT * from large tables without LIMIT — risky in new endpoints
    regex: /SELECT\s+\*\s+FROM\s+(?:rounds_ac|constituency_status)\b(?![^;]*LIMIT)/gis,
    message:
      "SELECT * from rounds_ac or constituency_status without LIMIT. " +
      "This can return hundreds of thousands of rows. Add explicit column list and LIMIT.",
    severity: "WARN",
  },
];

function extractSqlStrings(src) {
  // Extract backtick-delimited template literals (used in JS for SQL)
  const strings = [];
  const regex = /`([\s\S]*?)`/g;
  let match;
  while ((match = regex.exec(src)) !== null) {
    strings.push({ text: match[1], offset: match.index });
  }
  // Also extract single-quoted strings for inline SQL
  const singleRegex = /'([^']+)'/g;
  while ((match = singleRegex.exec(src)) !== null) {
    if (match[1].includes('SELECT') || match[1].includes('FROM')) {
      strings.push({ text: match[1], offset: match.index });
    }
  }
  return strings;
}

function scanFile(filePath) {
  const src = readFileSync(filePath, "utf8");
  const violations = [];
  const sqlStrings = extractSqlStrings(src);

  for (const { text: sql, offset } of sqlStrings) {
    for (const { regex, message, severity } of BANNED_PATTERNS) {
      let match;
      regex.lastIndex = 0;
      while ((match = regex.exec(sql)) !== null) {
        // Calculate line number in the original file
        const lineNo = src.slice(0, offset + match.index).split("\n").length;
        violations.push({
          filePath,
          lineNo,
          severity,
          message,
          snippet: match[0].replace(/\n/g, " ").slice(0, 100),
        });
      }
    }
  }
  return violations;
}

function walk(dir) {
  const files = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) files.push(...walk(full));
    else if (full.endsWith(".js") || full.endsWith(".ts")) files.push(full);
  }
  return files;
}

let hasErrors = false;
for (const dir of QUERY_DIRS) {
  for (const file of walk(dir)) {
    for (const v of scanFile(file)) {
      const label = v.severity === "ERROR" ? "❌ ERROR" : "⚠️  WARN";
      console.log(`\n${label} ${v.filePath}:${v.lineNo}`);
      console.log(`  ${v.message}`);
      console.log(`  Snippet: ${v.snippet}...`);
      if (v.severity === "ERROR") hasErrors = true;
    }
  }
}

if (hasErrors) {
  console.log("\n💥 D1 query linter found ERRORs. Fix before deploying.");
  process.exit(1);
} else {
  console.log("\n✅ D1 query linter passed.");
}
