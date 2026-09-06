const DEFAULT_DAYS = 90;

const colors = ['var(--clay)', 'var(--sage)', 'var(--blue)', 'var(--gold)', 'var(--plum)', 'var(--moss)'];

const els = {
  totalValue: document.querySelector('#total-value'),
  asOf: document.querySelector('#as-of'),
  accountCount: document.querySelector('#account-count'),
  baseCurrency: document.querySelector('#base-currency'),
  cashRatio: document.querySelector('#cash-ratio'),
  investedRatio: document.querySelector('#invested-ratio'),
  unrealizedPnl: document.querySelector('#unrealized-pnl'),
  dataStatus: document.querySelector('#data-status'),
  dataSource: document.querySelector('#data-source'),
  allocationChart: document.querySelector('#allocation-chart'),
  accountList: document.querySelector('#account-list'),
  holdingsCount: document.querySelector('#holdings-count'),
  topHoldings: document.querySelector('#top-holdings'),
  emptyHoldings: document.querySelector('#empty-holdings'),
  timeseriesChart: document.querySelector('#timeseries-chart'),
  positionsTable: document.querySelector('#positions-table'),
  search: document.querySelector('#position-search'),
};

function num(value) {
  const parsed = Number.parseFloat(String(value ?? '').replace(/,/g, ''));
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value, currency = 'GBP') {
  return new Intl.NumberFormat('en-GB', {
    style: 'currency',
    currency: currency || 'GBP',
    maximumFractionDigits: Math.abs(num(value)) >= 1000 ? 0 : 2,
  }).format(num(value));
}

function pct(value) {
  const numeric = num(value);
  const normalized = Math.abs(numeric) <= 1 ? numeric * 100 : numeric;
  return `${normalized.toFixed(1)}%`;
}

function signedClass(value) {
  const numeric = num(value);
  if (numeric > 0) return 'positive';
  if (numeric < 0) return 'negative';
  return '';
}

function safeText(value, fallback = '—') {
  return String(value ?? fallback).replace(/[&<>"]/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
  }[char]));
}

function createClient() {
  const config = window.PORTFOLIO_SUPABASE_CONFIG || {};
  if (!config.url || !config.anonKey) {
    throw new Error('Missing dashboard/config.js. Copy config.example.js to config.js and fill SUPABASE_URL + anon key. Do not use service role key here.');
  }
  return window.supabase.createClient(config.url, config.anonKey);
}

async function rpc(client, name, args = {}) {
  const { data, error } = await client.rpc(name, args);
  if (error) throw new Error(`${name}: ${error.message}`);
  return data || [];
}

function summarizeAccounts(summary) {
  const total = summary.reduce((sum, item) => sum + num(item.total_value), 0);
  const cash = summary.reduce((sum, item) => sum + num(item.cash_value), 0);
  const invested = summary.reduce((sum, item) => sum + num(item.invested_value), 0);
  const pnl = summary.reduce((sum, item) => sum + num(item.unrealized_pnl), 0);
  const currency = summary.find((item) => item.base_currency)?.base_currency || 'GBP';
  const asOf = summary.map((item) => item.snapshot_date).filter(Boolean).sort().at(-1);

  return {
    total,
    cash,
    invested,
    pnl,
    currency,
    asOf,
    cashRatio: total ? cash / total : 0,
    investedRatio: total ? invested / total : 0,
  };
}

function renderAccounts(summary) {
  const portfolio = summarizeAccounts(summary);

  els.totalValue.textContent = money(portfolio.total, portfolio.currency);
  els.asOf.textContent = portfolio.asOf ? `As of ${portfolio.asOf}` : 'No snapshot date';
  els.accountCount.textContent = summary.length.toString();
  els.baseCurrency.textContent = `${portfolio.currency} portfolio`;
  els.cashRatio.textContent = pct(portfolio.cashRatio);
  els.investedRatio.textContent = pct(portfolio.investedRatio);
  els.unrealizedPnl.textContent = money(portfolio.pnl, portfolio.currency);
  els.unrealizedPnl.className = signedClass(portfolio.pnl);

  const firstShare = portfolio.total ? (num(summary[0]?.total_value) / portfolio.total) * 360 : 0;
  els.allocationChart.style.setProperty('--a', `${firstShare}deg`);

  els.accountList.innerHTML = summary
    .map((item, index) => {
      const value = num(item.total_value);
      const share = portfolio.total ? (value / portfolio.total) * 100 : 0;
      const cashRatio = value ? num(item.cash_value) / value : 0;
      return `
        <div class="account-row">
          <div>
            <div class="account-row__name">${safeText(item.account_name || item.account_key || 'Account')}</div>
            <div class="account-row__meta">${share.toFixed(1)}% · cash ${pct(cashRatio)} · ${safeText(item.account_key || '—')}</div>
          </div>
          <strong>${money(value, item.base_currency || portfolio.currency)}</strong>
          <div class="bar"><span style="--w:${share}%; background:${colors[index % colors.length]}"></span></div>
        </div>`;
    })
    .join('');
}

function renderHoldings(positions) {
  const total = positions.reduce((sum, item) => sum + num(item.market_value), 0);
  const sorted = [...positions].sort((a, b) => num(b.market_value) - num(a.market_value));
  els.holdingsCount.textContent = `${positions.length} holdings`;
  els.emptyHoldings.hidden = positions.length > 0;

  els.topHoldings.innerHTML = sorted.slice(0, 8).map((item) => {
    const value = num(item.market_value);
    const share = num(item.portfolio_weight) || (total ? value / total : 0);
    const label = item.instrument_name || item.ticker || 'Unknown holding';
    return `
      <div class="holding-row">
        <div>
          <div class="holding-row__name">${safeText(item.ticker || '—')}</div>
          <div class="holding-row__meta">${safeText(label)}</div>
        </div>
        <strong>${money(value, item.currency || 'GBP')}</strong>
        <div class="bar"><span style="--w:${Math.min(share * 100, 100)}%"></span></div>
      </div>`;
  }).join('');
}

function renderTimeseries(rows) {
  if (!rows.length) {
    els.timeseriesChart.innerHTML = '<div class="empty-state"><strong>暂无趋势数据</strong><span>连续同步几天后，这里会显示资产走势。</span></div>';
    return;
  }

  const sorted = [...rows].sort((a, b) => String(a.metric_date).localeCompare(String(b.metric_date)));
  const values = sorted.map((row) => num(row.total_value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 720;
  const height = 210;
  const points = values.map((value, index) => {
    const x = sorted.length === 1 ? width / 2 : (index / (sorted.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 28) - 14;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(' ');
  const last = sorted.at(-1);

  els.timeseriesChart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Portfolio total value trend">
      <defs>
        <linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="var(--clay)" stop-opacity="0.28" />
          <stop offset="100%" stop-color="var(--clay)" stop-opacity="0" />
        </linearGradient>
      </defs>
      <polyline points="0,${height} ${points} ${width},${height}" fill="url(#trendFill)" stroke="none"></polyline>
      <polyline points="${points}" fill="none" stroke="var(--clay)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"></polyline>
    </svg>
    <div class="trend-caption">
      <span>${safeText(sorted[0]?.metric_date || '—')} → ${safeText(last?.metric_date || '—')}</span>
      <strong>${money(last?.total_value, last?.base_currency || 'GBP')}</strong>
    </div>`;
}

function renderTable(positions, query = '') {
  const needle = query.trim().toLowerCase();
  const filtered = positions.filter((item) => [
    item.ticker,
    item.instrument_name,
    item.account_name,
    item.account_key,
  ].join(' ').toLowerCase().includes(needle));

  if (!filtered.length) {
    els.positionsTable.innerHTML = '<tr><td colspan="8">No matching positions.</td></tr>';
    return;
  }

  els.positionsTable.innerHTML = filtered.map((item) => `
    <tr>
      <td><strong>${safeText(item.ticker || '—')}</strong></td>
      <td>${safeText(item.instrument_name || '—')}</td>
      <td>${safeText(item.account_name || item.account_key || '—')}</td>
      <td class="num">${num(item.quantity).toLocaleString('en-GB', { maximumFractionDigits: 4 })}</td>
      <td class="num">${money(item.market_value, item.currency || 'GBP')}</td>
      <td class="num">${pct(num(item.portfolio_weight))}</td>
      <td class="num ${signedClass(item.unrealized_pnl)}">${money(item.unrealized_pnl, item.currency || 'GBP')}</td>
      <td class="num ${signedClass(item.unrealized_pnl_pct)}">${pct(item.unrealized_pnl_pct)}</td>
    </tr>
  `).join('');
}

async function init() {
  try {
    const client = createClient();
    const [summary, positions, timeseries] = await Promise.all([
      rpc(client, 'get_latest_dashboard_summary'),
      rpc(client, 'get_position_allocation'),
      rpc(client, 'get_portfolio_timeseries', { p_days: DEFAULT_DAYS }),
    ]);

    renderAccounts(summary);
    renderHoldings(positions);
    renderTimeseries(timeseries);
    renderTable(positions);
    els.search.addEventListener('input', () => renderTable(positions, els.search.value));
    els.dataStatus.textContent = 'Ready';
    els.dataSource.textContent = 'Supabase read-only RPC';
  } catch (error) {
    els.dataStatus.textContent = 'Load failed';
    els.dataSource.textContent = error.message;
    els.asOf.textContent = 'Check config.js / RPC / RLS';
    console.error(error);
  }
}

init();
