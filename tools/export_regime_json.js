// Export full daily regime history from regime_detector.js
const fs = require('fs');
const path = require('path');

// Import regime detector logic
const detectorPath = path.join(__dirname, 'core', 'regime_detector.js');
// Read the file and eval the needed functions... better to just require-like approach
// Actually let's write a standalone export script

const dataPath = path.join(__dirname, 'data', 'combined_daily.json');
const combinedData = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));

// Copy the needed functions from regime_detector.js
// (inline to avoid module issues)

// ─── 数学工具 ────────────────────────────────────────────────────────────────
function sma(arr, window) {
  if (arr.length < window) return null;
  const slice = arr.slice(-window);
  return slice.reduce((a, b) => a + b, 0) / window;
}
function std(arr) {
  if (arr.length < 2) return 0;
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  const variance = arr.reduce((s, v) => s + (v - mean) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}
function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

// ─── CONFIG ──────────────────────────────────────────────────────────────────
const CONFIG = require('./core/regime_detector.js'); // won't work as-is

// Actually, just exec regime_detector.js with a custom flag
