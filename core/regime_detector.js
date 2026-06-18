/**
 * Regime Detector v1 — 多维牛熊判定引擎
 *
 * 四维度加权 + 滞后滤波，输出 bull / neutral / bear
 * 数据依赖: data/market_regime/combined_daily.json
 *
 * 用法:
 *   node scripts/regime_detector.js                    # 完整回测
 *   node scripts/regime_detector.js --latest           # 仅最新信号
 *   node scripts/regime_detector.js --monthly          # 月度汇总
 */

const fs = require('fs');
const path = require('path');

// ─── 配置 ────────────────────────────────────────────────────────────────────

const CONFIG = {
  // 维度权重
  weights: {
    trend:      0.35,  // 趋势结构
    breadth:    0.25,  // 市场宽度
    volatility: 0.20,  // 波动率体制
    flow:       0.20,  // 量价关系
  },

  // 滞后滤波阈值
  hysteresis: {
    enterBull:  0.55,  // 进入牛市需要 ≥ 0.55
    exitBull:   0.15,  // 退出牛市需要 ≤ 0.15
    enterBear: -0.55,  // 进入熊市需要 ≤ -0.55
    exitBear:  -0.15,  // 退出熊市需要 ≥ -0.15
  },

  // 技术指标参数
  ma: {
    short: 20,
    medium: 60,
    long: 120,
  },

  // 宽度阈值（动态 — 根据可用 ETF 数量调整比例）
  breadth: {
    bullThreshold:  0.60,  // >60% ETF 站上 MA60 = 普涨
    bearThreshold:  0.30,  // <30% = 普跌
  },

  // 波动率
  vol: {
    expandingFactor:  1.25,  // 短波 > 长波 × 1.25 = 扩张
    contractingFactor: 0.75,  // 短波 < 长波 × 0.75 = 收缩
  },

  // 最小数据要求
  minHistory: 120,   // 需要 120 天历史才出信号（MA120 需要）
  minEtfs:     5,    // 至少 5 只 ETF 才算有效宽度
};

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

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

// ─── 维度一：趋势结构（35%）──────────────────────────────────────────────────

function trendScore(prices) {
  if (prices.length < CONFIG.minHistory) return 0;

  const ma20  = sma(prices, CONFIG.ma.short);
  const ma60  = sma(prices, CONFIG.ma.medium);
  const ma120 = sma(prices, CONFIG.ma.long);
  if (ma20 === null || ma60 === null || ma120 === null) return 0;

  let score = 0;
  const close = prices[prices.length - 1];

  // 1. MA 排列结构（主导因子）
  if (ma20 > ma60 && ma60 > ma120) {
    score += 1.0;
  } else if (ma20 > ma60) {
    score += 0.5;
  } else if (ma20 < ma60 && ma60 < ma120) {
    score -= 1.0;
  } else if (ma20 < ma60) {
    score -= 0.5;
  }

  // 2. 价格相对关键均线
  if (close > ma60)  score += 0.3;
  if (close > ma120) score += 0.3;
  if (close < ma60)  score -= 0.3;
  if (close < ma120) score -= 0.3;

  // 3. 均线斜率（趋势加速度）
  const ma20Prev = sma(prices.slice(0, -20), CONFIG.ma.short);
  if (ma20Prev !== null && ma20Prev > 0) {
    const slope = (ma20 - ma20Prev) / ma20Prev;
    if (slope > 0.02)       score += 0.2;
    else if (slope < -0.02) score -= 0.2;
  }

  return clamp(score, -1.5, 1.5);
}

// ─── 维度二：市场宽度（25%）──────────────────────────────────────────────────

function breadthScore(etfClosesByCode) {
  // etfClosesByCode: { '159611': [prices...], '515880': [prices...], ... }
  const codes = Object.keys(etfClosesByCode);
  if (codes.length < CONFIG.minEtfs) return 0;

  let aboveMA20 = 0;
  let aboveMA60 = 0;
  let valid = 0;

  for (const code of codes) {
    const prices = etfClosesByCode[code];
    if (prices.length < CONFIG.ma.medium) continue;

    const close = prices[prices.length - 1];
    const ma20 = sma(prices, CONFIG.ma.short);
    const ma60 = sma(prices, CONFIG.ma.medium);
    if (ma20 === null || ma60 === null) continue;

    valid++;
    if (close > ma20) aboveMA20++;
    if (close > ma60) aboveMA60++;
  }

  if (valid < CONFIG.minEtfs) return 0;

  const pctAboveMA60 = aboveMA60 / valid;
  const pctAboveMA20 = aboveMA20 / valid;

  // 宽度映射
  let score = 0;
  if (pctAboveMA60 > CONFIG.breadth.bullThreshold) {
    score = 1.0;
  } else if (pctAboveMA60 > 0.50) {
    score = 0.5;
  } else if (pctAboveMA60 < CONFIG.breadth.bearThreshold) {
    score = -1.0;
  } else if (pctAboveMA60 < 0.40) {
    score = -0.5;
  }

  // 虚涨检测：MA20 宽度 < MA60 宽度 → 少数权重股拉指数
  if (pctAboveMA20 < pctAboveMA60 - 0.15 && score > 0) {
    score -= 0.3;  // 降级"虚涨"信号
  }

  // 底部反转检测：MA20 宽度 > MA60 宽度 → 反弹扩散
  if (pctAboveMA20 > pctAboveMA60 + 0.15 && score < 0) {
    score += 0.3;  // 反弹扩散中
  }

  return clamp(score, -1.0, 1.0);
}

// ─── 维度三：波动率体制（20%）────────────────────────────────────────────────

function volatilityScore(prices) {
  if (prices.length < CONFIG.minHistory) return 0;

  // 计算日收益率
  const returns = [];
  for (let i = 1; i < prices.length; i++) {
    returns.push((prices[i] - prices[i - 1]) / prices[i - 1]);
  }

  const vol20  = std(returns.slice(-20))  * Math.sqrt(252);
  const vol60  = std(returns.slice(-60))  * Math.sqrt(252);
  const vol120 = std(returns.slice(-120)) * Math.sqrt(252);

  const volExpanding   = vol20 > vol60 * CONFIG.vol.expandingFactor;
  const volContracting = vol20 < vol60 * CONFIG.vol.contractingFactor;

  const ma60 = sma(prices, CONFIG.ma.medium);
  if (ma60 === null) return 0;
  const close = prices[prices.length - 1];
  const trendUp = close > ma60;

  // 四象限判定
  if (trendUp && volContracting)        return  1.0;  // 低波上行 = 最佳牛
  if (trendUp && !volExpanding)         return  0.5;  // 稳中有升
  if (trendUp && volExpanding)          return  0.0;  // 暴躁牛/赶顶
  if (!trendUp && volExpanding)         return -1.0;  // 高波下行 = 最危险
  if (!trendUp && !volContracting)      return -0.5;  // 阴跌
  return 0.0;  // 低波阴跌 — 钝刀割肉
}

// ─── 维度四：量价关系（20%）──────────────────────────────────────────────────

function flowScore(prices, volumes) {
  if (prices.length < CONFIG.ma.short || volumes.length < CONFIG.ma.short) return 0;

  const volMA20 = sma(volumes, CONFIG.ma.short);
  if (volMA20 === null || volMA20 === 0) return 0;

  const recentVol = sma(volumes.slice(-5), 5);
  if (recentVol === null) return 0;

  const volRatio = recentVol / volMA20;
  const priceChg = (prices[prices.length - 1] / prices[prices.length - 20]) - 1;

  let score = 0;
  if (priceChg > 0.03 && volRatio > 1.2)        score =  1.0;  // 放量上涨
  else if (priceChg > 0.03 && volRatio < 0.8)   score = -0.3;  // 缩量上涨
  else if (priceChg < -0.03 && volRatio > 1.2)  score = -1.0;  // 放量下跌
  else if (priceChg < -0.03 && volRatio < 0.8)  score =  0.3;  // 缩量下跌
  else if (priceChg > 0 && volRatio > 1.0)       score =  0.5;
  else if (priceChg < 0 && volRatio > 1.0)       score = -0.5;

  return clamp(score, -1.0, 1.0);
}

// ─── 滞后滤波 ────────────────────────────────────────────────────────────────

function applyHysteresis(rawScore, prevRegime) {
  const h = CONFIG.hysteresis;

  switch (prevRegime) {
    case 'bull':
      if (rawScore <= h.exitBull) return 'neutral';
      return 'bull';
    case 'bear':
      if (rawScore >= h.exitBear) return 'neutral';
      return 'bear';
    case 'neutral':
    default:
      if (rawScore >= h.enterBull) return 'bull';
      if (rawScore <= h.enterBear) return 'bear';
      return 'neutral';
  }
}

// ─── 主检测函数 ──────────────────────────────────────────────────────────────

function detectRegime(hs300Data, etfData, prevRegime = 'neutral') {
  // hs300Data: array of { date, close, volume, amount }
  // etfData:    { '159611': array of { date, close, volume }, ... }

  const hs300Prices  = hs300Data.map(d => d.close);
  const hs300Volumes = hs300Data.map(d => d.volume);

  // ETF 收盘价序列
  const etfCloses = {};
  for (const code of Object.keys(etfData)) {
    etfCloses[code] = etfData[code].map(d => d.close);
  }

  const trend      = trendScore(hs300Prices);
  const breadth    = breadthScore(etfCloses);
  const volatility = volatilityScore(hs300Prices);
  const flow       = flowScore(hs300Prices, hs300Volumes);

  const rawScore = (
    trend      * CONFIG.weights.trend +
    breadth    * CONFIG.weights.breadth +
    volatility * CONFIG.weights.volatility +
    flow       * CONFIG.weights.flow
  );

  const regime = applyHysteresis(rawScore, prevRegime);

  return {
    regime,
    rawScore:     Math.round(rawScore * 1000) / 1000,
    confidence:   Math.round(Math.abs(rawScore) * 1000) / 1000,
    breakdown: {
      trend:      Math.round(trend * 1000) / 1000,
      breadth:    Math.round(breadth * 1000) / 1000,
      volatility: Math.round(volatility * 1000) / 1000,
      flow:       Math.round(flow * 1000) / 1000,
    },
  };
}

// ─── 策略建议 ────────────────────────────────────────────────────────────────

function getRegimeSuggestion(regime) {
  switch (regime) {
    case 'bull':
      return {
        selectorMode: 'momentum',
        maxPositions: 5,
        stopLoss: 'loose',
        cashReserve: 0.10,
        description: '牛市 — 动量选最强，放宽止损，低现金'
      };
    case 'neutral':
      return {
        selectorMode: 'rule',
        maxPositions: 3,
        stopLoss: 'tight',
        cashReserve: 0.30,
        description: '震荡 — 多因子精选，严格止损，中现金'
      };
    case 'bear':
      return {
        selectorMode: 'defensive',
        maxPositions: 2,
        stopLoss: 'strict',
        cashReserve: 0.50,
        description: '熊市 — 防御优先，只选避险资产，高现金'
      };
    default:
      return { selectorMode: 'rule', maxPositions: 3, stopLoss: 'tight', cashReserve: 0.30 };
  }
}

// ─── 滚动回测 ────────────────────────────────────────────────────────────────

function runBacktest(combinedData) {
  const results = [];
  let prevRegime = 'neutral';

  // 按日期累积数据
  let hs300Accum = [];
  const etfAccum = {};

  for (const row of combinedData) {
    // 累积沪深 300
    hs300Accum.push({
      date: row.date,
      close: row.hs300.close,
      volume: row.hs300.volume,
      amount: row.hs300.amount,
    });

    // 累积 ETF
    for (const code of Object.keys(row.etfs)) {
      if (!etfAccum[code]) etfAccum[code] = [];
      etfAccum[code].push({
        date: row.date,
        close: row.etfs[code].close,
        volume: row.etfs[code].volume,
      });
    }

    // 需要足够历史
    if (hs300Accum.length < CONFIG.minHistory) {
      results.push({
        date: row.date,
        regime: 'insufficient_data',
        rawScore: 0,
        confidence: 0,
        breakdown: { trend: 0, breadth: 0, volatility: 0, flow: 0 },
        suggestion: null,
      });
      continue;
    }

    const result = detectRegime(hs300Accum, etfAccum, prevRegime);
    prevRegime = result.regime;

    results.push({
      date: row.date,
      ...result,
      nEtfs: Object.keys(row.etfs).length,
      suggestion: getRegimeSuggestion(result.regime),
    });
  }

  return results;
}

// ─── 统计汇总 ────────────────────────────────────────────────────────────────

function summarize(results) {
  const valid = results.filter(r => r.regime !== 'insufficient_data');

  const regimeCounts = { bull: 0, neutral: 0, bear: 0 };
  valid.forEach(r => { if (regimeCounts[r.regime] !== undefined) regimeCounts[r.regime]++; });

  // 切换次数
  let switches = 0;
  for (let i = 1; i < valid.length; i++) {
    if (valid[i].regime !== valid[i - 1].regime) switches++;
  }

  // 各 regime 下的平均得分
  const regimeScores = { bull: [], neutral: [], bear: [] };
  valid.forEach(r => {
    if (regimeScores[r.regime]) regimeScores[r.regime].push(r.rawScore);
  });

  const avgScore = {};
  for (const k of Object.keys(regimeScores)) {
    const arr = regimeScores[k];
    avgScore[k] = arr.length > 0 ? Math.round(arr.reduce((a, b) => a + b, 0) / arr.length * 1000) / 1000 : 0;
  }

  // 按年统计
  const yearly = {};
  valid.forEach(r => {
    const yr = r.date.slice(0, 4);
    if (!yearly[yr]) yearly[yr] = { bull: 0, neutral: 0, bear: 0, total: 0 };
    yearly[yr][r.regime]++;
    yearly[yr].total++;
  });

  return {
    period: { start: valid[0]?.date, end: valid[valid.length - 1]?.date },
    totalDays: valid.length,
    regimeCounts,
    regimePct: {
      bull:    Math.round(regimeCounts.bull    / valid.length * 100),
      neutral: Math.round(regimeCounts.neutral / valid.length * 100),
      bear:    Math.round(regimeCounts.bear    / valid.length * 100),
    },
    switches,
    avgScore,
    yearly,
  };
}

// ─── 月度聚合 ────────────────────────────────────────────────────────────────

function monthlyAggregate(results) {
  const monthly = {};
  results.forEach(r => {
    const month = r.date.slice(0, 7);
    if (!monthly[month]) {
      monthly[month] = {
        month,
        regimes: { bull: 0, neutral: 0, bear: 0 },
        scores: [],
        total: 0,
      };
    }
    if (r.regime !== 'insufficient_data') {
      monthly[month].regimes[r.regime]++;
      monthly[month].scores.push(r.rawScore);
      monthly[month].total++;
    }
  });

  return Object.values(monthly)
    .sort((a, b) => a.month.localeCompare(b.month))
    .map(m => ({
      month: m.month,
      dominantRegime: Object.entries(m.regimes).sort((a, b) => b[1] - a[1])[0][0],
      regimePct: {
        bull:    Math.round(m.regimes.bull    / m.total * 100),
        neutral: Math.round(m.regimes.neutral / m.total * 100),
        bear:    Math.round(m.regimes.bear    / m.total * 100),
      },
      avgScore: Math.round(m.scores.reduce((a, b) => a + b, 0) / m.scores.length * 1000) / 1000,
    }));
}

// ─── 详情输出（最近 N 次切换）─────────────────────────────────────────────────

function recentSwitches(results, n = 20) {
  const switches = [];
  for (let i = 1; i < results.length; i++) {
    if (results[i].regime !== results[i - 1].regime &&
        results[i].regime !== 'insufficient_data') {
      switches.push({
        date: results[i].date,
        from: results[i - 1].regime,
        to: results[i].regime,
        score: results[i].rawScore,
        breakdown: results[i].breakdown,
      });
    }
  }
  return switches.slice(-n);
}

// ─── CLI 入口 ────────────────────────────────────────────────────────────────

function main() {
  const dataPath = path.join(__dirname, '..', 'data', 'market_regime', 'combined_daily.json');

  if (!fs.existsSync(dataPath)) {
    console.error(`Data not found: ${dataPath}`);
    console.error('Run the data download script first.');
    process.exit(1);
  }

  const combinedData = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));
  console.error(`Loaded ${combinedData.length} daily records`);

  const results = runBacktest(combinedData);
  const summary = summarize(results);

  const arg = process.argv[2];

  if (arg === '--latest') {
    const last = results[results.length - 1];
    console.log(JSON.stringify(last, null, 2));
    return;
  }

  if (arg === '--monthly') {
    const monthly = monthlyAggregate(results);
    console.log(JSON.stringify(monthly, null, 2));
    return;
  }

  if (arg === '--switches') {
    const sw = recentSwitches(results, 30);
    console.log(JSON.stringify(sw, null, 2));
    return;
  }

  // 默认：完整输出
  const output = {
    summary,
    monthly: monthlyAggregate(results),
    recentSwitches: recentSwitches(results, 20),
    latest: results[results.length - 1],
  };

  console.log(JSON.stringify(output, null, 2));
}

main();
