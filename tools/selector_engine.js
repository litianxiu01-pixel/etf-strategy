/**
 * Selector Engine v1 — 统一选股引擎
 *
 * 一个入口，两种打分模式：
 *   1. momentum 模式（scoring.weights 存在）— 动量/风险指标打分
 *   2. rule 模式（rules.entry.conditions 存在）— 规则条件加权打分
 *
 * 统一输出结构：
 * {
 *   experimentId, experimentName,
 *   mode: 'momentum' | 'rule',
 *   universe: [{symbol, name, indicators, normScores, rawScore, score, status, reason}],
 *   selected: [...],   // 入选（最多 maxCandidates，分数 >= minScore）
 *   watching: [...],   // 观察（分数 < minScore 但前 N 名）
 *   rejected: [...],   // 拒绝（不在前 maxCandidates*2 名）
 *   scoringConfig: {...},
 *   metadata: {fetched, failed, totalTimeMs}
 * }
 */

const https = require('https');
const http = require('http');

// ─── HTTP 工具 ───────────────────────────────────────────────────────────────

function httpGet(url) {
  return new Promise((resolve, reject) => {
    const isHttps = url.startsWith('https://');
    const mod = isHttps ? https : http;
    const TIMEOUT_MS = 8000; // 8秒超时（Round 22 Fixpack）
    const req = mod.get(url, {
      headers: {
        Referer: 'https://finance.sina.com.cn/',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
      },
      timeout: TIMEOUT_MS
    }, res => {
      if (res.statusCode === 456) {
        req.destroy();
        return reject(new Error('EM_456: Rate limited'));
      }
      if (res.statusCode !== 200) {
        req.destroy();
        return reject(new Error(`HTTP ${res.statusCode}`));
      }
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try {
          resolve(JSON.parse(d));
        } catch (e) {
          reject(new Error('JSON parse error: ' + e.message));
        }
      });
      res.on('error', err => {
        req.destroy();
        reject(err);
      });
    });
    req.on('error', err => reject(err));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error(`Timeout after ${TIMEOUT_MS}ms`));
    });
  });
}

async function httpGetWithRetry(url, retries = 3, delayMs = 500) {
  for (let i = 0; i < retries; i++) {
    try {
      return await httpGet(url);
    } catch (err) {
      if (i === retries - 1) throw err;
      await new Promise(r => setTimeout(r, delayMs * (i + 1)));
    }
  }
}

// ─── Eastmoney 数据获取（优先）─────────────────────────────────────────────

/**
 * Eastmoney secid 格式:
 *   上交所(sh): 1.6xxxx / 1.51xxx
 *   深交所(sz): 0.00xxxx / 0.15xxxx / 0.30xxxx
 */
function toEastmoneySecid(symbol) {
  const code = symbol.replace(/[^0-9]/g, '');
  if (code.startsWith('6') || code.startsWith('51')) {
    return '1.' + code;  // 上交所
  }
  // 深交所: 00开头→主板, 15开头→创业板, 30开头→科创板
  if (code.startsWith('00') || code.startsWith('15')) {
    return '0.' + code;
  }
  if (code.startsWith('30')) {
    return '0.' + code;  // 科创板也用 0
  }
  return '0.' + code;  // 默认深交所
}

/**
 * 东财 K 线获取（240分钟周期 = 日K；retry=3次防限流）
 */
async function fetchKlineEM(symbol, days) {
  const secid = toEastmoneySecid(symbol);
  const datalen = Math.max(days * 2, 120);
  const url = `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=${secid}&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57&klt=240&fqt=1&lmt=${datalen}`;

  const data = await httpGetWithRetry(url, 3, 500);
  if (!data || !data.data || !Array.isArray(data.data.klines) || data.data.klines.length === 0) {
    return null;
  }

  const priceData = data.data.klines.map(k => {
    const parts = k.split(',');
    return {
      date: parts[0],
      open:   parseFloat(parts[1]),
      close:  parseFloat(parts[2]),
      high:   parseFloat(parts[3]),
      low:    parseFloat(parts[4]),
      volume: parseFloat(parts[5])
    };
  }).sort((a, b) => a.date.localeCompare(b.date));

  return priceData.slice(-days);
}

/**
 * 东财实时价格
 */
async function fetchCurrentPriceEM(symbol) {
  const secid = toEastmoneySecid(symbol);
  const url = `https://push2.eastmoney.com/api/qt/stock/get?secid=${secid}&fields=f43,f169,f170`;
  const data = await httpGetWithRetry(url, 3, 500);
  if (!data || !data.data) return null;
  return parseFloat(data.data.f43) || parseFloat(data.data.f170) || null;
}

// ─── Sina / Tencent 降级备选 ────────────────────────────────────────────────

async function fetchKlineSina(symbol, days) {
  const code = symbol.replace(/[^0-9]/g, '');
  const shanghaiPrefix = code.startsWith('6') || code.startsWith('51');
  const sinaSymbol = (shanghaiPrefix ? 'sh' : 'sz') + code;
  const url = `https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=${sinaSymbol}&scale=240&ma=no&datalen=${Math.max(days * 2, 120)}`;
  const klines = await httpGet(url);
  if (!Array.isArray(klines) || klines.length === 0) return null;
  return klines.map(k => ({
    date: k.day,
    close: parseFloat(k.close),
    high: parseFloat(k.high),
    low: parseFloat(k.low),
    open: parseFloat(k.open),
    volume: parseFloat(k.volume)
  })).sort((a, b) => a.date.localeCompare(b.date)).slice(-days);
}

async function fetchCurrentPriceTencent(symbol) {
  const code = symbol.replace(/[^0-9]/g, '');
  const shanghaiPrefix = code.startsWith('6') || code.startsWith('51');
  const sinaSymbol = (shanghaiPrefix ? 'sh' : 'sz') + code;
  const url = `https://qt.gtimg.cn/q=${sinaSymbol}`;
  const raw = await httpGet(url);
  const parts = raw.split('~');
  return parts.length > 3 ? parseFloat(parts[3]) : null;
}

// --- OKX data source for crypto symbols -------------------------------------

function isCryptoSymbol(symbol) {
  return /USDT$/i.test(symbol || '');
}

function toOKXInstId(symbol) {
  return String(symbol).toUpperCase().replace(/USDT$/, '-USDT');
}

function convertOKXKline(candles) {
  if (!Array.isArray(candles) || candles.length === 0) return null;
  return candles.slice().reverse().map(c => ({
    timestamp: parseInt(c[0], 10),
    date: new Date(parseInt(c[0], 10)).toISOString().slice(0, 10),
    open: parseFloat(c[1]),
    high: parseFloat(c[2]),
    low: parseFloat(c[3]),
    close: parseFloat(c[4]),
    volume: parseFloat(c[5])
  }));
}

async function fetchKlineOKX(symbol, days) {
  const instId = toOKXInstId(symbol);
  const limit = Math.min(Math.max(days * 2, 120), 300);
  const url = `https://www.okx.com/api/v5/market/candles?instId=${instId}&bar=1D&limit=${limit}`;
  const data = await httpGetWithRetry(url, 3, 500);
  if (!data || data.code !== '0' || !Array.isArray(data.data) || data.data.length === 0) {
    return null;
  }
  return convertOKXKline(data.data);
}

async function fetchCurrentPriceOKX(symbol) {
  const instId = toOKXInstId(symbol);
  const url = `https://www.okx.com/api/v5/market/ticker?instId=${instId}`;
  const data = await httpGetWithRetry(url, 3, 500);
  if (!data || data.code !== '0' || !data.data || !data.data[0]) return null;
  return parseFloat(data.data[0].last) || null;
}

// ─── 统一入口（东财优先，降级备选）────────────────────────────────────────

async function fetchKline(symbol, days) {
  if (isCryptoSymbol(symbol)) {
    return fetchKlineOKX(symbol, days);
  }
  // [Round 22 Fixpack] Sina 优先（快），Eastmoney 作为 backup
  try {
    const result = await fetchKlineSina(symbol, days);
    if (result && result.length >= days * 0.8) return result;
  } catch { /* 降级到 Eastmoney */ }
  try {
    const result = await fetchKlineEM(symbol, days);
    if (result && result.length >= days * 0.8) return result;
  } catch { /* 两个都失败 */ }
  return null; // 不再 fallback 到假数据
}

async function fetchCurrentPrice(symbol) {
  if (isCryptoSymbol(symbol)) {
    return fetchCurrentPriceOKX(symbol);
  }
  // [Round 22 Fixpack] Sina/Tencent 优先（快），Eastmoney 作为 backup
  try {
    const result = await fetchCurrentPriceTencent(symbol);
    if (result !== null) return result;
  } catch { /* 降级到 Eastmoney */ }
  try {
    const result = await fetchCurrentPriceEM(symbol);
    if (result !== null) return result;
  } catch { /* 两个都失败 */ }
  return null; // 不再返回假数据
}

// ─── 指标计算 ────────────────────────────────────────────────────────────────

/**
 * 计算单个标的的全部指标（momentum 模式 + rule 模式通用）
 */
function computeIndicators(priceData) {
  if (!priceData || priceData.length < 5) return null;
  const closes = priceData.map(d => d.close);
  const volumes = priceData.map(d => d.volume);
  const latest = closes[closes.length - 1];

  // 移动平均线
  const ma = (arr, n) => arr.slice(-n).reduce((a, b) => a + b, 0) / Math.min(n, arr.length);
  const ma5 = ma(closes, 5);
  const ma10 = ma(closes, 10);
  const ma20 = ma(closes, 20);
  const ma60 = ma(closes, Math.min(60, closes.length));

  // 动量指标
  const n20 = Math.min(20, closes.length - 1);
  const n60 = Math.min(60, closes.length - 1);
  const momentum_20d = n20 > 0 ? (latest - closes[closes.length - 1 - n20]) / closes[closes.length - 1 - n20] : null;
  const momentum_60d = n60 > 0 ? (latest - closes[closes.length - 1 - n60]) / closes[closes.length - 1 - n60] : null;

  // 趋势强度
  const trendStrength = ma20 ? (latest - ma20) / ma20 : null;

  // 波动率（日收益率标准差）
  const returns20 = [];
  for (let i = Math.max(1, closes.length - 20); i < closes.length; i++) {
    returns20.push((closes[i] - closes[i - 1]) / closes[i - 1]);
  }
  const mean = returns20.length > 0 ? returns20.reduce((a, b) => a + b, 0) / returns20.length : 0;
  const volatility_20d = returns20.length > 1
    ? Math.sqrt(returns20.reduce((s, r) => s + Math.pow(r - mean, 2), 0) / returns20.length)
    : null;

  // 最大回撤
  let peak = -Infinity, maxDD = 0;
  for (let i = Math.max(0, closes.length - 20); i < closes.length; i++) {
    if (closes[i] > peak) peak = closes[i];
    const dd = peak > 0 ? (peak - closes[i]) / peak : 0;
    if (dd > maxDD) maxDD = dd;
  }

  // 收益率
  const ret5d = closes.length >= 6 ? (latest - closes[closes.length - 6]) / closes[closes.length - 6] : null;
  const ret20d = closes.length >= 21 ? (latest - closes[closes.length - 21]) / closes[closes.length - 21] : null;

  // 量比
  const volAvg = volumes.slice(-20).reduce((a, b) => a + b, 0) / Math.min(20, volumes.length);
  const volume_ratio = volAvg > 0 ? volumes[volumes.length - 1] / volAvg : null;

  // RSI (14)
  let rsi = null;
  if (closes.length >= 15) {
    const gains = [];
    const losses = [];
    for (let i = closes.length - 14; i < closes.length; i++) {
      const diff = closes[i] - closes[i - 1];
      gains.push(Math.max(diff, 0));
      losses.push(Math.max(-diff, 0));
    }
    const avgGain = gains.reduce((a, b) => a + b, 0) / 14;
    const avgLoss = losses.reduce((a, b) => a + b, 0) / 14;
    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    rsi = 100 - (100 / (1 + rs));
  }

  let rsi_extreme = null;
  if (rsi !== null) {
    if (rsi < 30) rsi_extreme = 'low';
    else if (rsi > 70) rsi_extreme = 'high';
    else rsi_extreme = 'normal';
  }

  let wick_probe = null;
  if (priceData.length >= 1) {
    const d = priceData[priceData.length - 1];
    const bodyLow = Math.min(d.open, d.close);
    if (bodyLow > 0) wick_probe = (bodyLow - d.low) / bodyLow;
  }

  // 成交额（万元）
  const amount = volumes[volumes.length - 1] * latest / 1e4;

  return {
    close: latest,
    ma5, ma10, ma20, ma60,
    momentum_20d,
    momentum_60d,
    trendStrength,
    volatility_20d,
    daily_volatility: volatility_20d,
    max_drawdown_20d: maxDD,
    rsi,
    rsi_extreme,
    wick_probe,
    ret5d, ret20d,
    volume_ratio,
    amount,
    priceData  // 保留引用，供 rule 模式使用
  };
}

/**
 * 计算排名（供 rule 模式使用）
 */
function computeRanks(universe) {
  // rank_20d: 涨幅越高 rank 越小（越强）
  const byRet20d = [...universe].sort((a, b) => (b.ret20d || 0) - (a.ret20d || 0));
  byRet20d.forEach((c, i) => { c.rank_20d = i + 1; });

  const byRet5d = [...universe].sort((a, b) => (b.ret5d || 0) - (a.ret5d || 0));
  byRet5d.forEach((c, i) => { c.rank_5d = i + 1; });

  const byVol = [...universe].sort((a, b) => (b.volume_ratio || 0) - (a.volume_ratio || 0));
  byVol.forEach((c, i) => { c.volume_rank = i + 1; });
}

/**
 * 评估单条规则条件（供 rule 模式使用）
 */
function evaluateCondition(cond, candidate) {
  if (!cond || !cond.field) return { pass: false, score: 0, detail: null };
  const field = cond.field;
  const operator = cond.operator;
  const targetValue = cond.value;
  const weight = typeof cond.weight === 'number' ? cond.weight : 1;

  let fieldValue;
  switch (field) {
    case 'rank_20d': fieldValue = candidate.rank_20d; break;
    case 'rank_5d': fieldValue = candidate.rank_5d; break;
    case 'volume_rank': fieldValue = candidate.volume_rank; break;
    case 'volume_ratio': fieldValue = candidate.volume_ratio; break;
    case 'ret_5d': fieldValue = candidate.ret5d; break;
    case 'ret_20d': fieldValue = candidate.ret20d; break;
    case 'ma5_gt_ma20': fieldValue = (candidate.ma5 > candidate.ma20) ? 1 : 0; break;
    case 'ma5_gt_ma10_gt_ma20': fieldValue = (candidate.ma5 > candidate.ma10 && candidate.ma10 > candidate.ma20) ? 1 : 0; break;
    case 'price_below_ma5': fieldValue = candidate.close < candidate.ma5 ? 1 : 0; break;
    case 'price_below_ma20': fieldValue = candidate.close < candidate.ma20 ? 1 : 0; break;
    case 'hold_days': fieldValue = candidate.hold_days || 0; break;
    default: fieldValue = Object.prototype.hasOwnProperty.call(candidate, field) ? candidate[field] : null;
  }

  if (fieldValue === null || fieldValue === undefined) return { pass: false, score: 0, detail: null };

  let pass;
  switch (operator) {
    case 'gt': pass = fieldValue > targetValue; break;
    case 'gte': pass = fieldValue >= targetValue; break;
    case 'lt': pass = fieldValue < targetValue; break;
    case 'lte': pass = fieldValue <= targetValue; break;
    case 'eq': pass = fieldValue === targetValue; break;
    case 'neq': pass = fieldValue !== targetValue; break;
    case 'ne': pass = fieldValue !== targetValue; break;
    case 'in': pass = Array.isArray(targetValue) && targetValue.includes(fieldValue); break;
    case 'nin': pass = Array.isArray(targetValue) && !targetValue.includes(fieldValue); break;
    default: pass = false;
  }

  const scoreContribution = pass ? weight : 0;
  const detail = {
    field,
    operator,
    threshold: targetValue,
    actual: fieldValue,
    pass,
    weight,
    scoreContribution
  };

  return { pass, score: scoreContribution, detail };
}

// ─── 核心打分 ────────────────────────────────────────────────────────────────

/**
 * momentum 模式打分（纯函数）
 * 各指标 min-max 归一化后加权求和
 */
function scoreMomentumMode(rawMetrics, weights) {
  const fields = ['momentum_20d', 'momentum_60d', 'trendStrength', 'volatility_20d', 'max_drawdown_20d'];
  const normMetrics = rawMetrics.map(m => ({ symbol: m.symbol }));

  for (const field of fields) {
    const values = rawMetrics.map(m => m[field]).filter(v => v !== null && v !== undefined);
    if (values.length === 0) continue;
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    rawMetrics.forEach((m, i) => {
      if (m[field] !== null && m[field] !== undefined) {
        const isRisk = field === 'volatility_20d' || field === 'max_drawdown_20d';
        normMetrics[i][field] = isRisk
          ? 1 - (m[field] - min) / range  // 反转：低风险 → 高分
          : (m[field] - min) / range;    // 正常：高动量 → 高分
      } else {
        normMetrics[i][field] = 0;
      }
    });
  }

  // 加权求和，满分 = 所有正权重之和
  const totalWeight = fields.reduce((sum, f) => sum + (weights[f] || 0), 0);
  return rawMetrics.map((m, i) => {
    let rawScore = 0;
    for (const field of fields) {
      rawScore += (weights[field] || 0) * (normMetrics[i][field] || 0);
    }
    const score = totalWeight > 0 ? rawScore / totalWeight : 0;
    return {
      symbol: m.symbol,
      indicators: {
        close: m.close,
        momentum_20d: m.momentum_20d,
        momentum_60d: m.momentum_60d,
        trendStrength: m.trendStrength,
        volatility_20d: m.volatility_20d,
        max_drawdown_20d: m.max_drawdown_20d
      },
      normScores: normMetrics[i],
      rawScore: parseFloat(rawScore.toFixed(4)),
      score: parseFloat(Math.max(0, Math.min(1, score)).toFixed(4))
    };
  });
}

/**
 * rule 模式打分（纯函数）
 * 默认保留既有加权打分；当 requireAll=true 时，必须全部条件通过才得分。
 */
function scoreRuleMode(rawMetrics, conditions, requireAll = false) {
  return rawMetrics.map(m => {
    let totalWeight = 0;
    const passed = [];
    const failed = [];
    const conditionDetails = [];

    for (const cond of conditions) {
      const { pass, score, detail } = evaluateCondition(cond, m);
      if (detail) conditionDetails.push(detail);
      if (pass) {
        totalWeight += score;
        passed.push(cond.field);
      } else {
        failed.push(cond.field);
      }
    }

    return {
      symbol: m.symbol,
      conditionDetails,
      indicators: {
        rank_20d: m.rank_20d,
        rank_5d: m.rank_5d,
        volume_rank: m.volume_rank,
        volume_ratio: m.volume_ratio,
        ret5d: m.ret5d,
        ret20d: m.ret20d,
        ma5: m.ma5, ma10: m.ma10, ma20: m.ma20,
        close: m.close,
        daily_volatility: m.daily_volatility,
        rsi: m.rsi,
        rsi_extreme: m.rsi_extreme,
        wick_probe: m.wick_probe
      },
      passed,
      failed,
      allPassed: failed.length === 0,
      rawScore: parseFloat(totalWeight.toFixed(4)),
      score: parseFloat((requireAll ? (failed.length === 0 ? 1 : 0) : totalWeight).toFixed(4))
    };
  });
}

/**
 * rule 模式打分（支持 conditionGroups）
 * 组内默认 AND；组间默认 OR。只要有一组 allPassed，候选通过。
 */
function scoreRuleModeWithGroups(rawMetrics, conditionGroups, conditionLogic = 'or') {
  return rawMetrics.map(m => {
    const groupResults = conditionGroups.map(group => {
      const groupPassed = [];
      const groupFailed = [];
      let groupWeight = 0;
      const groupConditionDetails = [];
      for (const cond of group.conditions || []) {
        const { pass, score, detail } = evaluateCondition(cond, m);
        if (detail) groupConditionDetails.push(detail);
        const label = `${group.name || 'group'}:${cond.field}`;
        if (pass) {
          groupPassed.push(label);
          groupWeight += score;
        } else {
          groupFailed.push(label);
        }
      }
      const logic = group.logic || 'and';
      const allPassed = logic === 'or'
        ? groupPassed.length > 0
        : groupPassed.length === (group.conditions || []).length;
      return { allPassed, groupPassed, groupFailed, groupWeight, groupConditionDetails };
    });

    const logic = conditionLogic || 'or';
    const passedGroup = logic === 'and'
      ? (groupResults.every(g => g.allPassed) ? { groupPassed: groupResults.flatMap(g => g.groupPassed), groupWeight: groupResults.reduce((s, g) => s + g.groupWeight, 0), groupConditionDetails: groupResults.flatMap(g => g.groupConditionDetails) } : null)
      : groupResults.find(g => g.allPassed);

    const passed = passedGroup ? passedGroup.groupPassed : [];
    const failed = groupResults.filter(g => !g.allPassed).flatMap(g => g.groupFailed);
    const conditionDetails = passedGroup ? passedGroup.groupConditionDetails : groupResults.flatMap(g => g.groupConditionDetails);

    return {
      symbol: m.symbol,
      conditionDetails,
      indicators: {
        rank_20d: m.rank_20d,
        rank_5d: m.rank_5d,
        volume_rank: m.volume_rank,
        volume_ratio: m.volume_ratio,
        ret5d: m.ret5d,
        ret20d: m.ret20d,
        ma5: m.ma5, ma10: m.ma10, ma20: m.ma20,
        close: m.close,
        daily_volatility: m.daily_volatility,
        rsi: m.rsi,
        rsi_extreme: m.rsi_extreme,
        wick_probe: m.wick_probe
      },
      passed,
      failed,
      allPassed: !!passedGroup,
      rawScore: passedGroup ? parseFloat(passedGroup.groupWeight.toFixed(4)) : 0,
      score: passedGroup ? 1 : 0
    };
  });
}

// ─── 主引擎 ──────────────────────────────────────────────────────────────────

/**
 * 统一选股入口
 *
 * @param {Object} exp - 实验卡片
 * @param {number} days - 数据天数（默认 80）
 * @returns {Object} 统一选股结果
 */
async function runSelectorEngine(exp, days = 80) {
  // Safety check: Block crypto experiments (Round 20 freeze)
  if (exp.marketScope === 'crypto') {
    return {
      error: 'crypto_frozen',
      message: 'OKX API frozen by Round 20',
      experimentId: exp.id,
      experimentName: exp.name,
      mode: 'frozen',
      universe: [],
      selected: [],
      watching: [],
      rejected: [],
      failed: [{ symbol: 'ALL', reason: 'crypto_frozen' }],
      metadata: { fetched: 0, failedCount: 1, totalTimeMs: 0 }
    };
  }
  
  // [Round 22 Fixpack] 总 timeout：30秒（Eastmoney API 较慢，需更长超时）
  const TOTAL_TIMEOUT_MS = 30000;
  return Promise.race([
    runSelectorEngineInner(exp, days),
    new Promise((_, reject) => 
      setTimeout(() => reject(new Error(`runSelectorEngine total timeout ${TOTAL_TIMEOUT_MS}ms`)), TOTAL_TIMEOUT_MS)
    )
  ]).catch(err => {
    console.error(`[Selector] ❌ ${exp.id} total timeout:`, err.message);
    return {
      error: 'timeout',
      message: err.message,
      experimentId: exp.id,
      experimentName: exp.name,
      mode: 'unknown',
      universe: [],
      selected: [],
      watching: [],
      rejected: [],
      failed: [{ symbol: 'ALL', reason: 'timeout' }],
      metadata: { fetched: 0, failedCount: 1, totalTimeMs: TOTAL_TIMEOUT_MS }
    };
  });
}

// 内部实现
async function runSelectorEngineInner(exp, days = 80) {
  
  const t0 = Date.now();
  const universe = exp.universe || [];
  const scoring = exp.scoring || {};
  const rules = exp.rules || {};

  // 判断模式
  const isMomentumMode = !!(scoring && scoring.weights && Object.keys(scoring.weights).length > 0);
  const conditions = (rules.entry && rules.entry.conditions) ? rules.entry.conditions : [];
  const conditionGroups = (rules.entry && rules.entry.conditionGroups) ? rules.entry.conditionGroups : [];
  const isRuleMode = conditions.length > 0 || conditionGroups.length > 0;
  const mode = isMomentumMode ? 'momentum' : (isRuleMode ? 'rule' : 'none');

  // weighted_score mode reads minScore from rules.entry first, then scoring, then default 0.2
  const entryMinScore = rules.entry && rules.entry.minScore != null ? rules.entry.minScore : null;
  const minScore = entryMinScore != null ? entryMinScore : (scoring.minScoreToEnter || 0.2);
  const maxCandidates = scoring.maxCandidates || 3;

  // 获取标的名称映射
  const nameMap = buildNameMap(universe);

  // 获取各标的指标
  const rawMetrics = [];
  const failed = [];

  for (const sym of universe) {
    try {
      const priceData = await fetchKline(sym, days);
      if (!priceData || priceData.length < 20) {
        failed.push({ symbol: sym, reason: '数据不足' });
        continue;
      }
      const indicators = computeIndicators(priceData);
      if (!indicators) {
        failed.push({ symbol: sym, reason: '指标计算失败' });
        continue;
      }
      rawMetrics.push({ symbol: sym, name: nameMap[sym] || sym, ...indicators });
    } catch (err) {
      failed.push({ symbol: sym, reason: err.message });
    }
    // 串行 + 限速（东财限流较宽松，Sina 更敏感）
    await new Promise(r => setTimeout(r, 200));
  }

  if (rawMetrics.length === 0) {
    return {
      experimentId: exp.id,
      experimentName: exp.name,
      mode,
      scoringConfig: { minScore, maxCandidates, mode },
      universe: [],
      selected: [],
      watching: [],
      rejected: [],
      failed,
      metadata: { fetched: 0, failedCount: failed.length, failed, totalTimeMs: Date.now() - t0 }
    };
  }

  // 排名（rule 模式需要）
  if (isRuleMode) computeRanks(rawMetrics);

  // 提取 entry conditions / conditionGroups
  const entryConditions = isRuleMode ? conditions : [];
  const entryConditionGroups = isRuleMode ? conditionGroups : [];
  const conditionLogic = rules.entry ? rules.entry.conditionLogic : undefined;
  const isWeightedScore = conditionLogic === 'weighted_score';
  const requireAllConditions = rules.entry && (
    conditionLogic === 'and' ||
    rules.entry.requireAll === true ||
    rules.entry.requireAllConditions === true
  );

  // 核心打分
  let scored;
  if (isMomentumMode) {
    scored = scoreMomentumMode(rawMetrics, scoring.weights);
  } else if (isRuleMode) {
    scored = entryConditionGroups.length > 0
      ? scoreRuleModeWithGroups(rawMetrics, entryConditionGroups, conditionLogic || 'or')
      : scoreRuleMode(rawMetrics, entryConditions, isWeightedScore ? false : requireAllConditions);
  } else {
    // 无打分逻辑：按收益率排序
    scored = rawMetrics.map(m => ({
      symbol: m.symbol,
      name: m.name,
      indicators: { ret20d: m.ret20d },
      rawScore: m.ret20d || 0,
      score: m.ret20d || 0
    }));
  }

  // 排序（降序）
  scored.sort((a, b) => b.score - a.score);

  // 分配状态
  const selected = [];
  const watching = [];
  const rejected = [];

  scored.forEach((s, i) => {
    const name = rawMetrics.find(r => r.symbol === s.symbol)?.name || s.symbol;
    const item = { symbol: s.symbol, name, score: s.score, indicators: s.indicators };

    if (isMomentumMode) {
      item.normScores = s.normScores;
    }
    if (isRuleMode) {
      item.passed = s.passed;
      item.failed = s.failed;
      item.conditionDetails = s.conditionDetails || [];
    }

    if (i < maxCandidates && s.score >= minScore) {
      item.status = 'selected';
      item.reason = '入选';
      selected.push(item);
    } else if (i < maxCandidates * 2 && s.score >= 0) {
      item.status = 'watching';
      item.reason = isMomentumMode
        ? `分数 ${s.score.toFixed(3)} < 最低 ${minScore}`
        : `未满足全部入选条件`;
      watching.push(item);
    } else {
      item.status = 'rejected';
      item.reason = isMomentumMode
        ? `排名 ${i + 1} 未进入前 ${maxCandidates * 2}`
        : `排名 ${i + 1} 未进入前 ${maxCandidates * 2}`;
      rejected.push(item);
    }
  });

  return {
    experimentId: exp.id,
    experimentName: exp.name,
    mode,
    scoringConfig: { minScore, maxCandidates, weights: scoring.weights || null, conditions: entryConditions, conditionGroups: entryConditionGroups, conditionLogic: conditionLogic || null },
    universe: scored.map(s => ({
      symbol: s.symbol,
      name: rawMetrics.find(r => r.symbol === s.symbol)?.name || s.symbol,
      score: s.score,
      indicators: s.indicators,
      ...(isRuleMode && s.conditionDetails ? { conditionDetails: s.conditionDetails } : {})
    })),
    selected,
    watching,
    rejected,
    failed,
    metadata: {
      fetched: rawMetrics.length,
      failedCount: failed.length,
      totalTimeMs: Date.now() - t0
    }
  };
}

// ─── 工具 ────────────────────────────────────────────────────────────────────

function buildNameMap(universe) {
  const map = {};
  universe.forEach(sym => {
    const nameMap = {
      '510300.SH': '沪深300ETF', '510500.SH': '中证500ETF', '159915.SZ': '创业板ETF',
      '510880.SH': '红利ETF', '512100.SH': '中证1000ETF', '512690.SH': '消费ETF',
      '518880.SH': '黄金ETF', '159941.SZ': '纳指ETF', '513500.SH': '标普500ETF',
      'BTCUSDT': 'BTC', 'ETHUSDT': 'ETH', 'SOLUSDT': 'SOL',
      '000001.SZ': '平安银行', '000002.SZ': '万科A'
    };
    map[sym] = nameMap[sym] || sym;
  });
  return map;
}

// ─── 导出 ────────────────────────────────────────────────────────────────────

module.exports = {
  runSelectorEngine,
  scoreMomentumMode,
  scoreRuleMode,
  scoreRuleModeWithGroups,
  computeIndicators,
  evaluateCondition,
  fetchKline
};
