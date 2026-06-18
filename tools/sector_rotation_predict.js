#!/usr/bin/env node
/**
 * 板块轮动预测工具
 * 分析 A 股行业 ETF 的强弱，预测当日板块轮动方向
 * 
 * @author 章鱼码农
 * @date 2026-06-05
 */

const https = require('https');
const fs = require('fs');
const path = require('path');

// ============================================================================
// 配置常量
// ============================================================================

const CONFIG = {
  // API 配置
  API_TIMEOUT: 8000,
  API_BASE_URL: 'push2his.eastmoney.com',
  API_PATH: '/api/qt/stock/kline/get',
  
  // K线参数
  KLINE_TYPE: 101,  // 日线
  DATA_LIMIT: 120,  // 120个交易日
  
  // 权重配置
  WEIGHTS: {
    momentum: 0.30,    // 短期动量
    trend: 0.25,       // 中期趋势
    volume: 0.20,      // 量能信号
    relative: 0.15,    // 相对强弱
    rotation: 0.10     // 轮动检测
  },
  
  // 标的池
  ETFS: [
    // 宽基（3个）
    { symbol: '510300.SH', name: '沪深300ETF', category: '宽基', isBenchmark: true },
    { symbol: '510500.SH', name: '中证500ETF', category: '宽基' },
    { symbol: '159915.SZ', name: '创业板ETF', category: '宽基' },
    
    // 行业（15个）
    { symbol: '512480.SH', name: '半导体ETF', category: '行业' },
    { symbol: '512660.SH', name: '军工ETF', category: '行业' },
    { symbol: '512010.SH', name: '医药ETF', category: '行业' },
    { symbol: '512880.SH', name: '证券ETF', category: '行业' },
    { symbol: '515050.SH', name: '5GETF', category: '行业' },
    { symbol: '515030.SH', name: '新能车ETF', category: '行业' },
    { symbol: '159995.SZ', name: '芯片ETF', category: '行业' },
    { symbol: '159928.SZ', name: '消费ETF', category: '行业' },
    { symbol: '512800.SH', name: '银行ETF', category: '行业' },
    { symbol: '512400.SH', name: '有色ETF', category: '行业' },
    { symbol: '515790.SH', name: '光伏ETF', category: '行业' },
    { symbol: '515220.SH', name: '煤炭ETF', category: '行业' },
    { symbol: '159611.SZ', name: '电力ETF', category: '行业' },
    { symbol: '512200.SH', name: '房地产ETF', category: '行业' },
    { symbol: '512980.SZ', name: '传媒ETF', category: '行业' },
    
    // 防御/海外（6个）
    { symbol: '510880.SH', name: '红利ETF', category: '防御' },
    { symbol: '518880.SH', name: '黄金ETF', category: '防御' },
    { symbol: '511010.SH', name: '国债ETF', category: '防御' },
    { symbol: '159920.SZ', name: '恒生ETF', category: '海外' },
    { symbol: '513100.SH', name: '纳指100ETF', category: '海外' },
    { symbol: '513500.SH', name: '标普500ETF', category: '海外' }
  ],
  
  // 输出目录
  OUTPUT_DIR: 'sector_rotation'
};

// ============================================================================
// 工具函数
// ============================================================================

/**
 * 获取市场代码
 * @param {string} symbol - ETF代码（如 510300.SH）
 * @returns {number} 市场代码（1=上海，0=深圳）
 */
function getMarketCode(symbol) {
  const code = symbol.split('.')[0];
  
  // 上海：60开头
  if (code.startsWith('60')) return 1;
  // 深圳：00、30、15开头
  if (code.startsWith('00') || code.startsWith('30') || code.startsWith('15')) return 0;
  
  // 默认根据后缀判断
  if (symbol.endsWith('.SH')) return 1;
  if (symbol.endsWith('.SZ')) return 0;
  
  return 1; // 默认上海
}

/**
 * HTTP GET 请求（Promise封装）
 * @param {string} urlStr - 完整URL
 * @param {number} timeout - 超时时间（毫秒）
 * @returns {Promise<string>} 响应数据
 */
function httpGet(urlStr, timeout = CONFIG.API_TIMEOUT) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      req.destroy(new Error(`请求超时 (${timeout}ms)`));
    }, timeout);
    
    const req = https.get(urlStr, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        clearTimeout(timer);
        if (res.statusCode === 200) {
          resolve(data);
        } else {
          reject(new Error(`HTTP ${res.statusCode}`));
        }
      });
    });
    
    req.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });
  });
}

/**
 * 获取ETF K线数据
 * @param {string} symbol - ETF代码
 * @returns {Promise<Array>} K线数据数组
 */
async function fetchKlineData(symbol) {
  const code = symbol.split('.')[0];
  const market = getMarketCode(symbol);
  const secid = `${market}.${code}`;
  
  const url = `https://${CONFIG.API_BASE_URL}${CONFIG.API_PATH}?secid=${secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&klt=${CONFIG.KLINE_TYPE}&fqt=1&end=20500101&lmt=${CONFIG.DATA_LIMIT}`;
  
  try {
    const response = await httpGet(url);
    const json = JSON.parse(response);
    
    if (!json.data || !json.data.klines) {
      throw new Error('数据格式异常');
    }
    
    // 解析K线数据
    // 格式：日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
    const klines = json.data.klines.map(line => {
      const parts = line.split(',');
      return {
        date: parts[0],
        open: parseFloat(parts[1]),
        close: parseFloat(parts[2]),
        high: parseFloat(parts[3]),
        low: parseFloat(parts[4]),
        volume: parseFloat(parts[5]),
        amount: parseFloat(parts[6]),
        amplitude: parseFloat(parts[7]),
        changePct: parseFloat(parts[8]),
        change: parseFloat(parts[9]),
        turnover: parseFloat(parts[10])
      };
    });
    
    return klines.reverse(); // 按日期升序排列
  } catch (err) {
    throw new Error(`获取 ${symbol} 数据失败: ${err.message}`);
  }
}

/**
 * Min-Max 归一化
 * @param {number} value - 待归一化值
 * @param {number} min - 最小值
 * @param {number} max - 最大值
 * @returns {number} 归一化值 [0, 1]
 */
function normalize(value, min, max) {
  if (max === min) return 0.5;
  const result = (value - min) / (max - min);
  return Math.max(0, Math.min(1, result));
}

/**
 * 计算均线
 * @param {Array<number>} prices - 价格数组
 * @param {number} period - 周期
 * @returns {number} 均值
 */
function calculateMA(prices, period) {
  if (prices.length < period) return null;
  const slice = prices.slice(-period);
  return slice.reduce((a, b) => a + b, 0) / period;
}

/**
 * 计算涨幅
 * @param {Array} klines - K线数据
 * @param {number} days - 天数
 * @returns {number} 涨幅（百分比）
 */
function calculateReturn(klines, days) {
  if (klines.length < days) return 0;
  const startClose = klines[klines.length - days].close;
  const endClose = klines[klines.length - 1].close;
  return ((endClose - startClose) / startClose) * 100;
}

/**
 * 计算平均成交量
 * @param {Array} klines - K线数据
 * @param {number} days - 天数
 * @returns {number} 平均成交量
 */
function calculateAvgVolume(klines, days) {
  if (klines.length < days) return 0;
  const slice = klines.slice(-days);
  return slice.reduce((sum, k) => sum + k.volume, 0) / days;
}

// ============================================================================
// 评分计算
// ============================================================================

/**
 * 计算单个ETF的评分指标
 * @param {Array} klines - K线数据
 * @param {Object} benchmark - 基准ETF（沪深300）数据
 * @param {Object} allReturns - 所有ETF的涨幅数据（用于排名）
 * @returns {Object} 评分指标
 */
function calculateIndicators(klines, benchmark, allReturns) {
  // 1. 短期动量指标
  const ret5d = calculateReturn(klines, 5);
  const ret10d = calculateReturn(klines, 10);
  
  // 2. 中期趋势指标
  const ret20d = calculateReturn(klines, 20);
  const prices = klines.map(k => k.close);
  const ma5 = calculateMA(prices, 5);
  const ma10 = calculateMA(prices, 10);
  const ma20 = calculateMA(prices, 20);
  
  // MA排列得分
  let maScore = 0;
  if (ma5 > ma10 && ma10 > ma20) {
    maScore = 1.0; // 三线多头
  } else if (ma5 > ma10 || ma10 > ma20) {
    maScore = 0.5; // 两线多头
  }
  
  // 3. 量能信号
  const avgVol5d = calculateAvgVolume(klines, 5);
  const avgVol20d = calculateAvgVolume(klines, 20);
  const volumeRatio = avgVol20d > 0 ? avgVol5d / avgVol20d : 1;
  
  // 4. 相对强弱
  const relativeStrength = benchmark ? (ret10d - benchmark.ret10d) : 0;
  
  return {
    ret5d,
    ret10d,
    ret20d,
    maScore,
    volumeRatio,
    relativeStrength,
    rotationSignal: 0 // 稍后计算
  };
}

/**
 * 计算所有ETF的综合评分
 * @param {Array} etfDataList - ETF数据列表
 * @returns {Array} 带评分的ETF列表
 */
function calculateScores(etfDataList) {
  // 收集所有指标用于归一化
  const allRet5d = etfDataList.map(e => e.indicators.ret5d);
  const allRet10d = etfDataList.map(e => e.indicators.ret10d);
  const allRet20d = etfDataList.map(e => e.indicators.ret20d);
  const allVolumeRatio = etfDataList.map(e => e.indicators.volumeRatio);
  const allRelativeStrength = etfDataList.map(e => e.indicators.relativeStrength);
  const allMaScore = etfDataList.map(e => e.indicators.maScore);
  
  // 计算排名（用于动量和轮动检测）
  const sortedBy5d = [...etfDataList].sort((a, b) => b.indicators.ret5d - a.indicators.ret5d);
  const sortedBy10d = [...etfDataList].sort((a, b) => b.indicators.ret10d - a.indicators.ret10d);
  
  // 计算10日vs前10日排名变化（轮动信号）
  // 由于我们只有120日数据，取最近20日数据来模拟
  const rotationRanks = new Map();
  etfDataList.forEach(etf => {
    if (etf.klines.length >= 20) {
      const recent10d = calculateReturn(etf.klines.slice(-10), 10);
      const prev10d = calculateReturn(etf.klines.slice(-20, -10), 10);
      rotationRanks.set(etf.symbol, { recent10d, prev10d });
    }
  });
  
  // 按前10日涨幅排名
  const sortedByPrev10d = [...etfDataList]
    .filter(e => rotationRanks.has(e.symbol))
    .sort((a, b) => (rotationRanks.get(b.symbol)?.prev10d || 0) - (rotationRanks.get(a.symbol)?.prev10d || 0));
  
  const prevRankMap = new Map();
  sortedByPrev10d.forEach((e, idx) => prevRankMap.set(e.symbol, idx + 1));
  
  // 按近10日涨幅排名
  const sortedByRecent10d = [...etfDataList]
    .filter(e => rotationRanks.has(e.symbol))
    .sort((a, b) => (rotationRanks.get(b.symbol)?.recent10d || 0) - (rotationRanks.get(a.symbol)?.recent10d || 0));
  
  const recentRankMap = new Map();
  sortedByRecent10d.forEach((e, idx) => recentRankMap.set(e.symbol, idx + 1));
  
  // Min-Max 范围
  const min5d = Math.min(...allRet5d);
  const max5d = Math.max(...allRet5d);
  const min10d = Math.min(...allRet10d);
  const max10d = Math.max(...allRet10d);
  const min20d = Math.min(...allRet20d);
  const max20d = Math.max(...allRet20d);
  const minVol = Math.min(...allVolumeRatio);
  const maxVol = Math.max(...allVolumeRatio);
  const minRel = Math.min(...allRelativeStrength);
  const maxRel = Math.max(...allRelativeStrength);
  
  // 计算每个ETF的评分
  return etfDataList.map(etf => {
    const ind = etf.indicators;
    
    // 1. 短期动量得分（权重30%）
    const rank5d = sortedBy5d.findIndex(e => e.symbol === etf.symbol) + 1;
    const rank10d = sortedBy10d.findIndex(e => e.symbol === etf.symbol) + 1;
    const norm5dRank = 1 - normalize(rank5d, 1, etfDataList.length);
    const norm10dRank = 1 - normalize(rank10d, 1, etfDataList.length);
    const momentumScore = (norm5dRank + norm10dRank) / 2 * CONFIG.WEIGHTS.momentum;
    
    // 2. 中期趋势得分（权重25%）
    const norm20d = normalize(ind.ret20d, min20d, max20d);
    const trendScore = (ind.maScore * 0.5 + norm20d * 0.5) * CONFIG.WEIGHTS.trend;
    
    // 3. 量能信号得分（权重20%）
    const normVol = normalize(ind.volumeRatio, minVol, maxVol);
    const volumeScore = normVol * CONFIG.WEIGHTS.volume;
    
    // 4. 相对强弱得分（权重15%）
    const normRel = normalize(ind.relativeStrength, minRel, maxRel);
    const relativeScore = normRel * CONFIG.WEIGHTS.relative;
    
    // 5. 轮动检测得分（权重10%）
    let rotationScore = 0;
    if (prevRankMap.has(etf.symbol) && recentRankMap.has(etf.symbol)) {
      const prevRank = prevRankMap.get(etf.symbol);
      const recentRank = recentRankMap.get(etf.symbol);
      const rankChange = prevRank - recentRank; // 正数表示排名上升
      // 归一化排名变化
      const maxChange = etfDataList.length - 1;
      rotationScore = normalize(rankChange, -maxChange, maxChange) * CONFIG.WEIGHTS.rotation;
    }
    
    const totalScore = momentumScore + trendScore + volumeScore + relativeScore + rotationScore;
    
    return {
      ...etf,
      score: Math.round(totalScore * 100) / 100,
      rank5d,
      rank10d,
      subScores: {
        momentum: Math.round(momentumScore * 100) / 100,
        trend: Math.round(trendScore * 100) / 100,
        volume: Math.round(volumeScore * 100) / 100,
        relative: Math.round(relativeScore * 100) / 100,
        rotation: Math.round(rotationScore * 100) / 100
      },
      prevRank: prevRankMap.get(etf.symbol),
      recentRank: recentRankMap.get(etf.symbol)
    };
  });
}

// ============================================================================
// 输出格式化
// ============================================================================

/**
 * 获取等级标识
 * @param {number} rank - 排名
 * @param {number} total - 总数
 * @returns {string} 等级标识
 */
function getLevelEmoji(rank, total) {
  const percentile = rank / total;
  if (percentile <= 0.2) return '🟢';
  if (percentile <= 0.4) return '🟡';
  if (percentile <= 0.6) return '⚪';
  if (percentile <= 0.8) return '🟠';
  return '🔴';
}

/**
 * 格式化涨幅显示
 * @param {number} value - 涨幅值
 * @returns {string} 格式化字符串
 */
function formatChange(value) {
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toFixed(1)}%`;
}

/**
 * 输出终端报告
 * @param {Array} results - 评分结果
 * @param {string} date - 日期
 */
function printTerminalReport(results, date) {
  const dayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const dayOfWeek = dayNames[new Date(date).getDay()];
  
  console.log('\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━');
  console.log(`📊 板块轮动预测 — ${date} (${dayOfWeek})`);
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
  
  // 按得分排序
  const sorted = [...results].sort((a, b) => b.score - a.score);
  const total = sorted.length;
  
  // Top 5 强势板块
  console.log('🏆 今日强势板块 Top 5');
  sorted.slice(0, 5).forEach((etf, idx) => {
    console.log(`${idx + 1}. ${etf.name.padEnd(10, ' ')} 得分 ${etf.score.toFixed(2)} | 5日涨${formatChange(etf.indicators.ret5d)} | 20日涨${formatChange(etf.indicators.ret20d)} | 量比 ${etf.indicators.volumeRatio.toFixed(2)}`);
  });
  console.log();
  
  // Bottom 5 弱势板块
  console.log('📉 今日弱势板块 Bottom 5');
  sorted.slice(-5).reverse().forEach((etf, idx) => {
    console.log(`${idx + 1}. ${etf.name.padEnd(10, ' ')} 得分 ${etf.score.toFixed(2)} | 5日涨${formatChange(etf.indicators.ret5d)} | 20日涨${formatChange(etf.indicators.ret20d)} | 量比 ${etf.indicators.volumeRatio.toFixed(2)}`);
  });
  console.log();
  
  // 轮动信号分析
  console.log('🔄 轮动信号');
  
  // 资金流入：排名前20%且得分上升
  const hotSectors = sorted.slice(0, Math.ceil(total * 0.3)).filter(e => e.subScores.momentum > 0.1);
  if (hotSectors.length > 0) {
    console.log(`⬆️ 资金正在流入：${hotSectors.map(e => e.name.replace('ETF', '')).join('、')}`);
  }
  
  // 资金流出：排名后20%
  const coldSectors = sorted.slice(-Math.ceil(total * 0.3));
  if (coldSectors.length > 0) {
    console.log(`⬇️ 资金正在流出：${coldSectors.map(e => e.name.replace('ETF', '')).join('、')}`);
  }
  
  // 轮动加速：排名大幅上升
  const accelerating = sorted.filter(e => e.prevRank && e.recentRank && (e.prevRank - e.recentRank) >= 5);
  if (accelerating.length > 0) {
    accelerating.forEach(e => {
      console.log(`⚠️ 轮动加速信号：${e.name}（10日排名从#${e.prevRank}→#${e.recentRank}）`);
    });
  }
  console.log();
  
  // 全板块排名
  console.log('📊 全板块排名（共' + total + '只）');
  sorted.forEach((etf, idx) => {
    const rank = idx + 1;
    const emoji = getLevelEmoji(rank, total);
    console.log(`#${String(rank).padStart(2, '0')} ${etf.name.padEnd(10, ' ')} ${etf.score.toFixed(2)} ${emoji}`);
  });
  
  console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
}

/**
 * 生成JSON输出
 * @param {Array} results - 评分结果
 * @param {string} date - 日期
 * @returns {Object} JSON对象
 */
function generateJsonOutput(results, date) {
  const dayNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const dayOfWeek = dayNames[new Date(date).getDay()];
  
  const sorted = [...results].sort((a, b) => b.score - a.score);
  const total = sorted.length;
  
  // 计算汇总信息
  const hotSectors = sorted.slice(0, 3).map(e => e.name.replace('ETF', ''));
  const coldSectors = sorted.slice(-3).map(e => e.name.replace('ETF', ''));
  
  const rotationIn = sorted.slice(0, Math.ceil(total * 0.3))
    .filter(e => e.subScores.momentum > 0.1)
    .map(e => e.name.replace('ETF', ''));
  
  const rotationOut = sorted.slice(-Math.ceil(total * 0.3))
    .map(e => e.name.replace('ETF', ''));
  
  const accelerating = sorted
    .filter(e => e.prevRank && e.recentRank && (e.prevRank - e.recentRank) >= 5)
    .map(e => e.name.replace('ETF', ''));
  
  const rankings = sorted.map((etf, idx) => ({
    rank: idx + 1,
    symbol: etf.symbol,
    name: etf.name,
    score: etf.score,
    level: getLevelEmoji(idx + 1, total),
    indicators: {
      ret5d: Math.round(etf.indicators.ret5d * 10) / 10,
      ret10d: Math.round(etf.indicators.ret10d * 10) / 10,
      ret20d: Math.round(etf.indicators.ret20d * 10) / 10,
      volume_ratio: Math.round(etf.indicators.volumeRatio * 10) / 10,
      relative_strength: Math.round(etf.indicators.relativeStrength * 10) / 10,
      rotation_signal: Math.round((etf.prevRank && etf.recentRank ? (etf.prevRank - etf.recentRank) / total : 0) * 10) / 10
    },
    subScores: {
      momentum: etf.subScores.momentum,
      trend: etf.subScores.trend,
      volume: etf.subScores.volume,
      relative: etf.subScores.relative,
      rotation: etf.subScores.rotation
    }
  }));
  
  return {
    date,
    dayOfWeek,
    generatedAt: new Date().toISOString(),
    summary: {
      hotSectors,
      coldSectors,
      rotationIn,
      rotationOut,
      accelerating
    },
    rankings
  };
}

// ============================================================================
// 主函数
// ============================================================================

async function main() {
  console.log('🐙 章鱼码农 · 板块轮动预测工具启动...\n');
  
  const startTime = Date.now();
  const today = new Date().toISOString().split('T')[0];
  
  // 创建输出目录
  const outputDir = path.join(process.cwd(), CONFIG.OUTPUT_DIR);
  if (!fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
    console.log(`✓ 创建输出目录: ${outputDir}`);
  }
  
  // 获取所有ETF数据
  console.log(`\n📡 正在获取 ${CONFIG.ETFS.length} 只ETF的K线数据...\n`);
  
  const etfDataList = [];
  const failedList = [];
  let benchmarkData = null;
  
  for (const etf of CONFIG.ETFS) {
    try {
      process.stdout.write(`  获取 ${etf.name} (${etf.symbol})... `);
      const klines = await fetchKlineData(etf.symbol);
      
      if (klines.length < 20) {
        throw new Error(`数据不足（仅${klines.length}条）`);
      }
      
      const indicators = calculateIndicators(klines, null, null);
      etfDataList.push({
        ...etf,
        klines,
        indicators
      });
      
      if (etf.isBenchmark) {
        benchmarkData = { ret10d: indicators.ret10d };
      }
      
      console.log(`✓ (${klines.length}条)`);
    } catch (err) {
      failedList.push({ symbol: etf.symbol, name: etf.name, error: err.message });
      console.log(`✗ ${err.message}`);
    }
    
    // 避免请求过快
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  
  // 检查成功率
  const successRate = etfDataList.length / CONFIG.ETFS.length;
  console.log(`\n📊 数据获取完成: ${etfDataList.length}/${CONFIG.ETFS.length} (${(successRate * 100).toFixed(1)}%)`);
  
  if (successRate < 0.8) {
    console.error('❌ 数据获取成功率低于80%，无法继续分析');
    process.exit(1);
  }
  
  if (failedList.length > 0) {
    console.log('\n⚠️ 失败列表:');
    failedList.forEach(f => console.log(`  - ${f.name} (${f.symbol}): ${f.error}`));
  }
  
  // 计算评分
  console.log('\n📈 计算板块评分...\n');
  
  // 重新计算指标（加入基准相对强弱）
  etfDataList.forEach(etf => {
    etf.indicators = calculateIndicators(etf.klines, benchmarkData, null);
  });
  
  const results = calculateScores(etfDataList);
  
  // 输出终端报告
  printTerminalReport(results, today);
  
  // 生成并保存JSON
  const jsonOutput = generateJsonOutput(results, today);
  const jsonPath = path.join(outputDir, `${today}.json`);
  
  fs.writeFileSync(jsonPath, JSON.stringify(jsonOutput, null, 2), 'utf-8');
  console.log(`✓ JSON已保存: ${jsonPath}`);
  
  const elapsed = Date.now() - startTime;
  console.log(`\n⏱️ 执行耗时: ${(elapsed / 1000).toFixed(2)}秒`);
  console.log('🐙 章鱼码农 · 任务完成\n');
  
  process.exit(0);
}

// 执行主函数
main().catch(err => {
  console.error('❌ 执行失败:', err.message);
  console.error(err.stack);
  process.exit(1);
});
