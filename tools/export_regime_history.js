/**
 * Export daily regime history as JSONL
 * Usage: node tools/export_regime_history.js > data/regime_v2_daily.jsonl
 */

const fs = require('fs');
const path = require('path');

const { runBacktest } = require('/tmp/regime_export.js');

const dataPath = path.join(__dirname, '..', 'data', 'combined_daily.json');

if (!fs.existsSync(dataPath)) {
  console.error(`Data not found: ${dataPath}`);
  process.exit(1);
}

const combinedData = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));
console.error(`Loaded ${combinedData.length} daily records`);

const results = runBacktest(combinedData);

results.forEach(r => {
  console.log(JSON.stringify({
    date: r.date,
    regime: r.regime,
    rawScore: r.rawScore,
    confidence: r.confidence,
    regime_type: r.regime_type || null,
    dimension_agreement: r.dimension_agreement ?? 1.0,
    breakdown: r.breakdown,
  }));
});

console.error(`Exported ${results.length} daily records`);
