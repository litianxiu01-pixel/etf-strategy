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
  // 维度权重 (v2: Step 1 新增 correlation)
  weights: {
    trend:       0.30,  // 趋势结构
    breadth:     0.20,  // 市场宽度
    volatility:  0.15,  // 波动率体制
    flow:        0.15,  // 量价关系
    correlation: 0.20,  // 股债相关性 (v2: Step 1)
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
    multiShort: 10,   // v2: Step 2 多时间尺度
    multiMid: 40,     // v2: Step 2 多时间尺度
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

// ─── v2: Step 2 ─── 维度一：趋势结构（30%）多时间尺度 ─────────────

function trendScore(prices) {
  if (prices.length < CONFIG.minHistory) return 0;

  // 原始三档 MA
  const ma20  = sma(prices, CONFIG.ma.short);
  const ma60  = sma(prices, CONFIG.ma.medium);
  const ma120 = sma(prices, CONFIG.ma.long);
  if (ma20 === null || ma60 === null || ma120 === null) return 0;

  // ─── v2: Step 2 ─── 多时间尺度 MA
  const ma10 = sma(prices, CONFIG.ma.multiShort);  // 10
  const ma40 = sma(prices, CONFIG.ma.multiMid);    // 40
  if (ma10 === null || ma40 === null) return 0;

  let score = 0;
  const close = prices[prices.length - 1];

  // 1. 原始 MA 排列结构（保留）
  if (ma20 > ma60 && ma60 > ma120) {
    score += 1.0;
  } else if (ma20 > ma60) {
    score += 0.5;
  } else if (ma20 < ma60 && ma60 < ma120) {
    score -= 1.0;
  } else if (ma20 < ma60) {
    score -= 0.5;
  }

  // 2. 价格相对关键均线（保留）
  if (close > ma60)  score += 0.3;
  if (close > ma120) score += 0.3;
  if (close < ma60)  score -= 0.3;
  if (close < ma120) score -= 0.3;

  // 3. 原始均线斜率（保留）
  const ma20Prev = sma(prices.slice(0, -20), CONFIG.ma.short);
  if (ma20Prev !== null && ma20Prev > 0) {
    const slope = (ma20 - ma20Prev) / ma20Prev;
    if (slope > 0.02)       score += 0.2;
    else if (slope < -0.02) score -= 0.2;
  }

  // ─── v2: Step 2 ─── 三时区对比
  const shortDir = ma10 > ma40 ? 1 : -1;  // 短线方向
  const midDir   = ma20 > ma60 ? 1 : -1;  // 中线方向
  const longDir  = ma40 > ma120 ? 1 : -1; // 长线方向

  const sameDirection = (shortDir === midDir && midDir === longDir);
  const shortUpMidLongDown = (shortDir === 1 && midDir === -1 && longDir === -1);
  const shortDownMidLongUp = (shortDir === -1 && midDir === 1 && longDir === 1);

  // 三线同向 → 信心加成
  if (sameDirection) {
    score += (shortDir === 1) ? 0.3 : -0.3;
  }

  // 短线 ↑ 但中线+长线 ↓ → 假信号削减
  if (shortUpMidLongDown) {
    score -= 0.3;
  }

  // 短线 ↓ 但中线+长线 ↑ → 正常回调
  if (shortDownMidLongUp) {
    score -= 0.2;
  }

  // ─── v2: Step 2 ─── MA10 斜率检测
  const ma10Prev = sma(prices.slice(0, -10), CONFIG.ma.multiShort);
  if (ma10Prev !== null && ma10Prev > 0) {
    const ma10Slope = (ma10 - ma10Prev) / ma10Prev;
    if (ma10Slope > 0.02)      score += 0.15;  // 正斜率 > 2%
    else if (ma10Slope < -0.02) score -= 0.15;  // 负斜率 < -2%
  }

  return clamp(score, -2.0, 2.0);  // 扩大范围以容纳新增分数
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

// ─── v2: Step 1 ─── 维度五：股债相关性（20%）─────────────────────────────

function pearsonCorrelation(x, y) {
  // 计算 Pearson 相关系数
  if (x.length !== y.length || x.length < 2) return null;
  
  const n = x.length;
  const sumX = x.reduce((a, b) => a + b, 0);
  const sumY = y.reduce((a, b) => a + b, 0);
  const sumXY = x.reduce((s, xi, i) => s + xi * y[i], 0);
  const sumX2 = x.reduce((s, xi) => s + xi * xi, 0);
  const sumY2 = y.reduce((s, yi) => s + yi * yi, 0);
  
  const numerator = n * sumXY - sumX * sumY;
  const denominator = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
  
  if (denominator === 0) return null;
  return numerator / denominator;
}

function correlationScore(hs300Prices, bondPrices) {
  // hs300Prices: 沪深300 收盘价序列
  // bondPrices:   国债 ETF 收盘价序列（511010 或 511180）
  
  // ─── v2: Step 1 ─── 无国债数据 fallback → 0.0
  if (!bondPrices || bondPrices.length < 60) return 0.0;
  if (hs300Prices.length < 60) return 0.0;
  
  // 计算日收益率
  const hs300Returns = [];
  for (let i = 1; i < hs300Prices.length; i++) {
    hs300Returns.push((hs300Prices[i] - hs300Prices[i - 1]) / hs300Prices[i - 1]);
  }
  
  const bondReturns = [];
  for (let i = 1; i < bondPrices.length; i++) {
    bondReturns.push((bondPrices[i] - bondPrices[i - 1]) / bondPrices[i - 1]);
  }
  
  // 对齐长度，取最近 60 日
  const minLen = Math.min(hs300Returns.length, bondReturns.length);
  if (minLen < 60) return 0.0;
  
  const hs300Window = hs300Returns.slice(-60);
  const bondWindow = bondReturns.slice(-60);
  
  const rho = pearsonCorrelation(hs300Window, bondWindow);
  if (rho === null) return 0.0;
  
  // 得分映射
  let score = 0.0;
  if (rho > 0.6) {
    score = -1.0;  // 极致正相关，资金枯竭
  } else if (rho >= 0.3 && rho <= 0.6) {
    score = -0.5;
  } else if (rho >= -0.3 && rho <= 0.3) {
    score = 0.0;
  } else if (rho < -0.3) {
    score = 0.5;   // 正常避险机制
  }
  
  return score;
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

// ─── v2: Step 1 ─── 主检测函数 ─────────────────────────────────────────────

function detectRegime(hs300Data, etfData, prevRegime = 'neutral') {
  // hs300Data: array of { date, close, volume, amount }
  // etfData:    { '159611': array of { date, close, volume }, ... }
  // returns:    { regime, rawScore, confidence, breakdown, regime_type?, dimension_agreement? }

  const hs300Prices  = hs300Data.map(d => d.close);
  const hs300Volumes = hs300Data.map(d => d.volume);

  // ETF 收盘价序列
  const etfCloses = {};
  for (const code of Object.keys(etfData)) {
    etfCloses[code] = etfData[code].map(d => d.close);
  }

  // ─── v2: Step 1 ─── 提取国债 ETF 价格（511010 优先，备选 511180）
  let bondPrices = null;
  if (etfCloses['511010']) {
    bondPrices = etfCloses['511010'];
  } else if (etfCloses['511180']) {
    bondPrices = etfCloses['511180'];
  }

  const trend      = trendScore(hs300Prices);
  const breadth    = breadthScore(etfCloses);
  let   volatility = volatilityScore(hs300Prices);  // 可能被打折
  const flow       = flowScore(hs300Prices, hs300Volumes);
  const correlation= correlationScore(hs300Prices, bondPrices);

  // ─── v2: Step 1 ─── 极端正相关（ρ > 0.6）触发 volatility 打折
  if (correlation <= -0.9) {  // correlationScore 返回 -1.0 时
    volatility *= 0.7;  // 资金枯竭常伴低波假象
  }

  const rawScore = (
    trend      * CONFIG.weights.trend +
    breadth    * CONFIG.weights.breadth +
    volatility * CONFIG.weights.volatility +
    flow       * CONFIG.weights.flow +
    correlation* CONFIG.weights.correlation
  );

  const regime = applyHysteresis(rawScore, prevRegime);

  // ─── v2: Step 3 ─── 一致性检查 + 过渡态标记
  const dimensions = [trend, breadth, volatility, flow, correlation];
  const positiveCount = dimensions.filter(d => d > 0).length;
  const negativeCount = dimensions.filter(d => d < 0).length;
  const agreementCount = Math.max(positiveCount, negativeCount);
  const dimensionAgreement = agreementCount / dimensions.length;

  let regimeType = 'mixed';
  let confidenceDiscount = 1.0;
  if (agreementCount >= 4) {
    regimeType = 'pure';
    confidenceDiscount = 1.0;
  } else if (agreementCount >= 2) {
    regimeType = 'transitional';
    confidenceDiscount = 0.8;
  } else {
    regimeType = 'mixed';
    confidenceDiscount = 0.7;
  }

  // ─── v2: Step 3 ─── BEAR 细分
  let regimeSubtype = null;
  if (regime === 'bear') {
    if (negativeCount === 5 && breadth < 0.3) {
      regimeSubtype = 'pure_bear';  // 真熊
    } else {
      regimeSubtype = 'transitional_bear';  // 震荡熊
    }
  }

  const result = {
    regime,
    rawScore:     Math.round(rawScore * 1000) / 1000,
    confidence:   Math.round(Math.abs(rawScore) * confidenceDiscount * 1000) / 1000,
    regime_type: regimeType,
    dimension_agreement: Math.round(dimensionAgreement * 1000) / 1000,
    breakdown: {
      trend:      Math.round(trend * 1000) / 1000,
      breadth:    Math.round(breadth * 1000) / 1000,
      volatility: Math.round(volatility * 1000) / 1000,
      flow:       Math.round(flow * 1000) / 1000,
      correlation: Math.round(correlation * 1000) / 1000,  // v2: Step 1
    },
  };

  if (regimeSubtype) {
    result.regime_subtype = regimeSubtype;
  }

  return result;
}

// ─── v2: Step 3 ─── 策略建议（适配 regime_type）─────────────────────

function getRegimeSuggestion(regime, regimeType = 'pure') {
  // regimeType: "pure" | "transitional" | "mixed"
  
  const baseSuggestions = {
    bull: {
      selectorMode: 'momentum',
      maxPositions: 5,
      stopLoss: 'loose',
      cashReserve: 0.10,
      description: '牛市 — 动量选最强，放宽止损，低现金'
    },
    neutral: {
      selectorMode: 'rule',
      maxPositions: 3,
      stopLoss: 'tight',
      cashReserve: 0.30,
      description: '震荡 — 多因子精选，严格止损，中现金'
    },
    bear: {
      selectorMode: 'defensive',
      maxPositions: 2,
      stopLoss: 'strict',
      cashReserve: 0.50,
      description: '熊市 — 防御优先，只选避险资产，高现金'
    }
  };
  
  const suggestion = baseSuggestions[regime] || baseSuggestions.neutral;
  
  // ─── v2: Step 3 ─── transitional 时仓位上限 +1
  if (regimeType === 'transitional') {
    suggestion.maxPositions += 1;
    suggestion.description += '（过渡态：允许试探性持仓 +1）';
  }
  
  return suggestion;
}

// ─── 滚动回测 ────────────────────────────────────────────────────────────────

function runBacktest(combinedData) {
  const results = [];
  let prevRegime = 'neutral';

  // 按日期累积数据
  let hs300Accum = [];
  const etfAccum = {};

  for (const row of combinedData) {
    // ─── v2: Step 1 ─── 兼容数据格式：优先使用 row.hs300，否则从 ETF 510300 提取
    let hs300Record = null;
    if (row.hs300 && row.hs300.close) {
      hs300Record = {
        date: row.date,
        close: row.hs300.close,
        volume: row.hs300.volume || 0,
        amount: row.hs300.amount || 0,
      };
    } else if (row.etfs && row.etfs['510300'] && row.etfs['510300'].close) {
      // 使用 510300 ETF 作为沪深300代理
      hs300Record = {
        date: row.date,
        close: row.etfs['510300'].close,
        volume: row.etfs['510300'].volume || 0,
        amount: 0,
      };
    }

    if (hs300Record) {
      hs300Accum.push(hs300Record);
    }

    // 累积 ETF
    for (const code of Object.keys(row.etfs || {})) {
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
        regime_type: 'mixed',
        dimension_agreement: 0,
        breakdown: { trend: 0, breadth: 0, volatility: 0, flow: 0, correlation: 0 },
        suggestion: null,
      });
      continue;
    }

    const result = detectRegime(hs300Accum, etfAccum, prevRegime);
    prevRegime = result.regime;

    // ─── v2: Step 3 ─── 确保新增字段每次迭代都有值
    results.push({
      date: row.date,
      ...result,
      nEtfs: Object.keys(row.etfs).length,
      suggestion: getRegimeSuggestion(result.regime, result.regime_type),  // 传入 regime_type
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
  // ─── v2: Step 1 ─── 数据路径修正（去掉 market_regime 子目录）
  const dataPath = path.join(__dirname, '..', 'data', 'combined_daily.json');

  if (!fs.existsSync(dataPath)) {
    console.error(`Data not found: ${dataPath}`);
    console.error('Run the data download script first.');
    process.exit(1);
  }

  const combinedData = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));
  console.error(`Loaded ${combinedData.length} daily records`);

  const arg = process.argv[2];

  // ─── v2: Step 1 ─── 新增测试入口：--diagnose
  if (arg === '--diagnose') {
    diagnose(combinedData);
    return;
  }

  const results = runBacktest(combinedData);
  const summary = summarize(results);

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

// ─── v2: Step 1 ─── 诊断函数：输出每个维度的原始值和同向性 ────────────

function diagnose(combinedData) {
  console.log('=== Regime Detector v2 Diagnostics ===');
  console.log('');
  
  // ─── v2: Step 1 ─── 兼容数据格式
  const hs300Data = [];
  for (const row of combinedData) {
    if (row.hs300 && row.hs300.close) {
      hs300Data.push({
        date: row.date,
        close: row.hs300.close,
        volume: row.hs300.volume || 0,
        amount: row.hs300.amount || 0,
      });
    } else if (row.etfs && row.etfs['510300'] && row.etfs['510300'].close) {
      hs300Data.push({
        date: row.date,
        close: row.etfs['510300'].close,
        volume: row.etfs['510300'].volume || 0,
        amount: 0,
      });
    }
  }
  const etfData = {};
  for (const row of combinedData) {
    for (const code of Object.keys(row.etfs)) {
      if (!etfData[code]) etfData[code] = [];
      etfData[code].push({
        date: row.date,
        close: row.etfs[code].close,
        volume: row.etfs[code].volume,
      });
    }
  }
  
  // 提取国债 ETF
  let bondPrices = null;
  if (etfData['511010']) bondPrices = etfData['511010'].map(d => d.close);
  else if (etfData['511180']) bondPrices = etfData['511180'].map(d => d.close);
  
  const hs300Prices = hs300Data.map(d => d.close);
  const hs300Volumes = hs300Data.map(d => d.volume);
  
  const etfCloses = {};
  for (const code of Object.keys(etfData)) {
    etfCloses[code] = etfData[code].map(d => d.close);
  }
  
  console.log('Sample Date: ' + hs300Data[hs300Data.length - 1].date);
  console.log('');
  
  const trend      = trendScore(hs300Prices);
  const breadth    = breadthScore(etfCloses);
  const volatility = volatilityScore(hs300Prices);
  const flow       = flowScore(hs300Prices, hs300Volumes);
  const correlation= correlationScore(hs300Prices, bondPrices);
  
  console.log('--- Dimension Scores ---');
  console.log('trend:      ' + trend.toFixed(3));
  console.log('breadth:    ' + breadth.toFixed(3));
  console.log('volatility: ' + volatility.toFixed(3));
  console.log('flow:       ' + flow.toFixed(3));
  console.log('correlation:' + correlation.toFixed(3));
  console.log('');
  
  const dimensions = [trend, breadth, volatility, flow, correlation];
  const positiveCount = dimensions.filter(d => d > 0).length;
  const negativeCount = dimensions.filter(d => d < 0).length;
  const agreementCount = Math.max(positiveCount, negativeCount);
  const agreement = agreementCount / dimensions.length;
  
  console.log('--- Agreement Analysis ---');
  console.log('Positive dimensions: ' + positiveCount);
  console.log('Negative dimensions: ' + negativeCount);
  console.log('Agreement count:     ' + agreementCount + '/5');
  console.log('Dimension agreement:  ' + agreement.toFixed(3));
  console.log('');
  
  console.log('--- Regime Type Prediction ---');
  if (agreementCount >= 4) console.log('regime_type: pure');
  else if (agreementCount >= 2) console.log('regime_type: transitional');
  else console.log('regime_type: mixed');
  console.log('');
  
  if (bondPrices) {
    console.log('Bond ETF detected: ' + (etfData['511010'] ? '511010' : '511180'));
  } else {
    console.log('No bond ETF data (511010/511180) — correlation score = 0.0');
  }
}

main();
