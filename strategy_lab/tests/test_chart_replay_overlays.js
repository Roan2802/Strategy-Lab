const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('strategy_lab/static/app.js', 'utf8');
const start = source.indexOf('function renderChart(');
const end = source.indexOf('\nfunction renderEquity()', start);
assert(start >= 0 && end > start, 'renderChart function must exist');

let plotted;
let plottedLayout;
const graph = { removeAllListeners() {}, on() {} };
const context = {
  state: {
    replay: true,
    runData: {
      trades: [
        { id: 'long-tp', type: 'long', opened_at: 1000, closed_at: 3000, entry_price: 100, exit_price: 105 },
        { id: 'short-sl', type: 'short', opened_at: 2000, closed_at: 4000, entry_price: 110, exit_price: 115 },
        { id: 'future', type: 'long', opened_at: 6000, closed_at: 7000, entry_price: 90, exit_price: 95 },
      ],
      events: [
        { event_type: 'POSITION_CLOSE', trade_id: 'long-tp', reason: 'TAKE_PROFIT' },
        { event_type: 'POSITION_CLOSE', trade_id: 'short-sl', reason: 'STOP_LOSS' },
        { event_type: 'POSITION_CLOSE', trade_id: 'future', reason: 'TAKE_PROFIT' },
      ],
    },
  },
  $: () => graph,
  currentParams: () => ({}),
  Plotly: { react: (_id, traces, layout) => { plotted = traces; plottedLayout = layout; } },
  renderEquity() {},
  setTheme() {},
  document: { body: { dataset: { theme: 'dark' } } },
};

vm.runInNewContext(`${source.slice(start, end)}; this.renderUnderTest = renderChart;`, context);
const candles = [1000, 2000, 3000, 4000, 5000].map((ms, index) => ({
  timestamp: new Date(ms).toISOString(), timestamp_ms: ms,
  bid_open: 100 + index, bid_high: 102 + index, bid_low: 99 + index, bid_close: 101 + index,
}));

context.renderUnderTest(candles, context.state.runData.events, 5000);
let names = Array.from(plotted, trace => trace.name);
const connectors = Array.from(plotted).filter(trace => trace.mode === 'lines' && trace.line?.dash === 'dot');
assert(names.includes('Bullish entry'));
assert(names.includes('Bearish entry'));
assert.deepEqual(connectors.map(trace => trace.customdata[0][0]).sort(), ['long-tp', 'short-sl']);
assert(connectors.every(trace => trace.showlegend === false && trace.line.width >= 2));
assert(names.includes('TP geraakt'));
assert(names.includes('SL geraakt'));
assert(!connectors.some(trace => trace.customdata[0][0] === 'future'));
const bullish = plotted.find(trace => trace.name === 'Bullish entry');
const bearish = plotted.find(trace => trace.name === 'Bearish entry');
assert.deepEqual(Array.from(bullish.x), [new Date(1000).toISOString()]);
assert.deepEqual(Array.from(bullish.y), [100]);
assert.deepEqual(Array.from(bearish.x), [new Date(2000).toISOString()]);
assert.deepEqual(Array.from(bearish.y), [110]);
assert.equal(bullish.marker.symbol, 'triangle-up');
assert.equal(bearish.marker.symbol, 'triangle-down');
const tp = plotted.find(trace => trace.name === 'TP geraakt');
const sl = plotted.find(trace => trace.name === 'SL geraakt');
assert.deepEqual(Array.from(tp.x), [new Date(3000).toISOString()]);
assert.deepEqual(Array.from(tp.y), [105]);
assert.deepEqual(Array.from(sl.x), [new Date(4000).toISOString()]);
assert.deepEqual(Array.from(sl.y), [115]);
for (const connector of connectors) {
  const id = connector.customdata[0][0];
  const expected = id === 'long-tp'
    ? [[new Date(1000).toISOString(), 100], [new Date(3000).toISOString(), 105]]
    : [[new Date(2000).toISOString(), 110], [new Date(4000).toISOString(), 115]];
  assert.deepEqual(Array.from(connector.x, (x, i) => [x, connector.y[i]]), expected);
  assert.equal(connector.customdata[1][0], id, 'connector exit must belong to the same trade');
}

context.renderUnderTest(candles, context.state.runData.events, 2500);
names = Array.from(plotted, trace => trace.name);
assert.equal(Array.from(plotted).filter(trace => trace.mode === 'lines' && trace.line?.dash === 'dot').length, 0);
assert(names.includes('Bearish entry'), 'open entries remain visible before their exit');
assert(!names.includes('TP geraakt') && !names.includes('SL geraakt'), 'future exits stay hidden until replay reaches them');

context.renderUnderTest(candles.slice(2, 4), context.state.runData.events, 4000, {
  ema: [101, 102], rolling: true, padding: 500,
});
names = Array.from(plotted, trace => trace.name);
assert.deepEqual(Array.from(plotted[0].customdata), [3000, 4000]);
assert.deepEqual(Array.from(plottedLayout.xaxis.range), [new Date(2500).toISOString(), new Date(4500).toISOString()]);
assert(!names.includes('Bullish entry') && !names.includes('Bearish entry'));
assert(names.includes('TP geraakt') && names.includes('SL geraakt'), 'exits remain inside the rolling window');
assert(!plotted.some(trace => trace.line?.dash === 'dot'), 'an off-screen entry cannot widen the chart');
console.log('Replay overlay and rolling-window regression passed.');
