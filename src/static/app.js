// ==========================================================================
// TENNIS PREDICTOR AI - CLIENT JAVASCRIPT LOGIC
// ==========================================================================

let currentCircuit = 'atp';
let selectedP1 = '';
let selectedP2 = '';

// DOM Elements
// DOM Elements
const circuitBtns = document.querySelectorAll('.circuit-btn');
const p1Input = document.getElementById('p1-input');
const p2Input = document.getElementById('p2-input');
const p1Dropdown = document.getElementById('p1-dropdown');
const p2Dropdown = document.getElementById('p2-dropdown');
const swapBtn = document.getElementById('swap-btn');
const predictBtn = document.getElementById('btn-predict');
const resultsSection = document.getElementById('results-section');

// Surface & Context Elements
const tournamentInput = document.getElementById('tournament-input');
const tournamentDropdown = document.getElementById('tournament-dropdown');
const surfaceSelect = document.getElementById('surface-select');
const levelSelect = document.getElementById('level-select');
const roundSelect = document.getElementById('round-select');
const bestOfSelect = document.getElementById('best-of-select');
const indoorSelect = document.getElementById('indoor-select');
const odds1Input = document.getElementById('odds1-input');
const odds2Input = document.getElementById('odds2-input');

// Secondary Markets Elements
const toggleSecMarketsBtn = document.getElementById('toggle-sec-markets');
const secMarketsContent = document.getElementById('sec-markets-content');
const secToggleIcon = document.getElementById('sec-toggle-icon');
const totalLineInput = document.getElementById('total-line-input');
const oddsOverInput = document.getElementById('odds-over-input');
const oddsUnderInput = document.getElementById('odds-under-input');
const handicapLineInput = document.getElementById('handicap-line-input');
const oddsH1Input = document.getElementById('odds-h1-input');
const oddsH2Input = document.getElementById('odds-h2-input');
const labelOddsH1 = document.getElementById('label-odds-h1');
const labelOddsH2 = document.getElementById('label-odds-h2');
const oddsSet1P1Input = document.getElementById('odds-set1-p1');
const oddsSet1P2Input = document.getElementById('odds-set1-p2');
const labelOddsSet1P1 = document.getElementById('label-odds-set1-p1');
const labelOddsSet1P2 = document.getElementById('label-odds-set1-p2');
const oddsSetsOverInput = document.getElementById('odds-sets-over');
const oddsSetsUnderInput = document.getElementById('odds-sets-under');
const oddsTbYesInput = document.getElementById('odds-tb-yes');
const oddsTbNoInput = document.getElementById('odds-tb-no');

// Update Data Elements
const btnUpdateData = document.getElementById('btn-update-data');
const updateStatusMsg = document.getElementById('update-status-msg');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  setupCircuitSwitcher();
  setupPlayerAutocomplete(p1Input, p1Dropdown, (name) => { selectedP1 = name; updateDynamicLabels(); });
  setupPlayerAutocomplete(p2Input, p2Dropdown, (name) => { selectedP2 = name; updateDynamicLabels(); });
  setupTournamentAutocomplete(tournamentInput, tournamentDropdown);
  setupSwap();
  setupSecondaryMarketsToggle();
  setupDynamicLabels();
  setupUpdateData();
  setupFormSubmit();
});

// Dynamic Labels for Handicap and Set 1
function updateDynamicLabels() {
  const p1Name = p1Input.value.trim() || 'J1';
  const p2Name = p2Input.value.trim() || 'J2';
  const p1Short = p1Name.split(' ').pop();
  const p2Short = p2Name.split(' ').pop();
  const hVal = (handicapLineInput && handicapLineInput.value) ? parseFloat(handicapLineInput.value) : 1.5;
  const hFormatted = isNaN(hVal) ? '1.5' : Math.abs(hVal).toFixed(1);

  if (labelOddsH1) labelOddsH1.textContent = `Cote ${p1Short} (-${hFormatted})`;
  if (labelOddsH2) labelOddsH2.textContent = `Cote ${p2Short} (+${hFormatted})`;
  if (labelOddsSet1P1) labelOddsSet1P1.textContent = `Cote ${p1Short} Set 1`;
  if (labelOddsSet1P2) labelOddsSet1P2.textContent = `Cote ${p2Short} Set 1`;
}

function setupDynamicLabels() {
  p1Input.addEventListener('input', updateDynamicLabels);
  p2Input.addEventListener('input', updateDynamicLabels);
  if (handicapLineInput) {
    handicapLineInput.addEventListener('input', updateDynamicLabels);
  }
}

// Secondary Markets Toggle
function setupSecondaryMarketsToggle() {
  if (!toggleSecMarketsBtn || !secMarketsContent) return;
  toggleSecMarketsBtn.addEventListener('click', () => {
    const isHidden = secMarketsContent.style.display === 'none';
    secMarketsContent.style.display = isHidden ? 'flex' : 'none';
    if (secToggleIcon) {
      secToggleIcon.classList.toggle('open', isHidden);
    }
  });
}

// Data Update (Sync recent matches & tournaments)
function setupUpdateData() {
  if (!btnUpdateData) return;
  btnUpdateData.addEventListener('click', async () => {
    btnUpdateData.classList.add('loading');
    btnUpdateData.disabled = true;
    updateStatusMsg.textContent = '⏳ Téléchargement et synchronisation des matchs récents...';
    updateStatusMsg.style.color = '#38bdf8';

    try {
      const res = await fetch('/api/update-data', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        updateStatusMsg.textContent = `✅ ${data.message} (${data.timestamp})`;
        updateStatusMsg.style.color = '#34d399';
      } else {
        updateStatusMsg.textContent = `⚠️ ${data.message}`;
        updateStatusMsg.style.color = '#f87171';
      }
    } catch (err) {
      updateStatusMsg.textContent = `❌ Erreur de connexion: ${err.message}`;
      updateStatusMsg.style.color = '#f87171';
    } finally {
      btnUpdateData.classList.remove('loading');
      btnUpdateData.disabled = false;
      setTimeout(() => {
        // Clear status after 8 seconds
        if (updateStatusMsg.textContent.startsWith('✅')) {
          updateStatusMsg.textContent = '';
        }
      }, 8000);
    }
  });
}

// Circuit Switching (ATP / WTA)
function setupCircuitSwitcher() {
  circuitBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      circuitBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentCircuit = btn.dataset.circuit;
      
      // Update best-of default (Grand Slam is best-of-5 for ATP, best-of-3 for WTA)
      if (levelSelect.value === 'G') {
        bestOfSelect.value = currentCircuit === 'atp' ? '5' : '3';
        triggerHighlight(bestOfSelect);
      } else {
        bestOfSelect.value = '3';
      }

      // Clear inputs on circuit switch
      p1Input.value = '';
      p2Input.value = '';
      selectedP1 = '';
      selectedP2 = '';
      p1Dropdown.style.display = 'none';
      p2Dropdown.style.display = 'none';
      resultsSection.style.display = 'none';
    });
  });
}

// Swap Players
function setupSwap() {
  swapBtn.addEventListener('click', () => {
    const tempName = p1Input.value;
    p1Input.value = p2Input.value;
    p2Input.value = tempName;

    const tempSel = selectedP1;
    selectedP1 = selectedP2;
    selectedP2 = tempSel;

    const tempOdds = odds1Input.value;
    odds1Input.value = odds2Input.value;
    odds2Input.value = tempOdds;
  });
}

// --------------------------------------------------------------------------
// Autocomplete : JOUEURS
// --------------------------------------------------------------------------
function setupPlayerAutocomplete(input, dropdown, onSelect) {
  let debounceTimer = null;
  let selectedIndex = -1;

  const fetchAndRender = (q) => {
    clearTimeout(debounceTimer);
    if (!q || q.length < 1) {
      dropdown.style.display = 'none';
      return;
    }

    debounceTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/players?circuit=${currentCircuit}&q=${encodeURIComponent(q)}&limit=10`);
        const players = await res.json();
        renderPlayerDropdown(dropdown, players, input, onSelect);
      } catch (err) {
        console.error('Error fetching players:', err);
      }
    }, 150);
  };

  input.addEventListener('input', () => {
    selectedIndex = -1;
    fetchAndRender(input.value.trim());
  });

  input.addEventListener('focus', () => {
    if (input.value.trim().length >= 1) {
      fetchAndRender(input.value.trim());
    }
  });

  // Keyboard navigation
  input.addEventListener('keydown', (e) => {
    const items = dropdown.querySelectorAll('.autocomplete-item');
    if (!items.length || dropdown.style.display === 'none') return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = (selectedIndex + 1) % items.length;
      updateActiveItem(items, selectedIndex);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = (selectedIndex - 1 + items.length) % items.length;
      updateActiveItem(items, selectedIndex);
    } else if (e.key === 'Enter') {
      if (selectedIndex >= 0 && items[selectedIndex]) {
        e.preventDefault();
        items[selectedIndex].click();
      }
    } else if (e.key === 'Escape') {
      dropdown.style.display = 'none';
    }
  });

  // Close dropdown on click outside
  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.style.display = 'none';
    }
  });
}

function renderPlayerDropdown(dropdown, players, input, onSelect) {
  dropdown.innerHTML = '';
  if (!players || players.length === 0) {
    dropdown.style.display = 'none';
    return;
  }

  players.forEach((p, idx) => {
    const item = document.createElement('div');
    item.className = 'autocomplete-item';
    item.dataset.index = idx;

    const rankStr = p.rank ? `#${p.rank}` : '';
    const isTop10 = p.rank && p.rank <= 10;
    const handLabel = p.hand === 'L' ? 'Gaucher' : 'Droitier';

    item.innerHTML = `
      <div class="ac-main-col">
        <span class="ac-name">${escapeHtml(p.name)}</span>
        <span class="ac-sub">${handLabel}</span>
      </div>
      <div class="ac-meta">
        ${rankStr ? `<span class="ac-rank ${isTop10 ? 'top10' : ''}">${rankStr}</span>` : ''}
        <span class="ac-elo">Elo ${p.elo}</span>
      </div>
    `;

    item.addEventListener('click', () => {
      input.value = p.name;
      onSelect(p.name);
      dropdown.style.display = 'none';
    });

    dropdown.appendChild(item);
  });

  dropdown.style.display = 'block';
}

// --------------------------------------------------------------------------
// Autocomplete & Auto-Détection : TOURNOIS
// --------------------------------------------------------------------------
function setupTournamentAutocomplete(input, dropdown) {
  let debounceTimer = null;
  let selectedIndex = -1;

  const fetchAndRender = (q) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/tournaments?q=${encodeURIComponent(q)}&limit=10`);
        const tourneys = await res.json();
        renderTournamentDropdown(dropdown, tourneys, input);
      } catch (err) {
        console.error('Error fetching tournaments:', err);
      }
    }, 150);
  };

  input.addEventListener('input', () => {
    selectedIndex = -1;
    fetchAndRender(input.value.trim());
  });

  input.addEventListener('focus', () => {
    fetchAndRender(input.value.trim());
  });

  // Keyboard navigation
  input.addEventListener('keydown', (e) => {
    const items = dropdown.querySelectorAll('.autocomplete-item');
    if (!items.length || dropdown.style.display === 'none') return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = (selectedIndex + 1) % items.length;
      updateActiveItem(items, selectedIndex);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = (selectedIndex - 1 + items.length) % items.length;
      updateActiveItem(items, selectedIndex);
    } else if (e.key === 'Enter') {
      if (selectedIndex >= 0 && items[selectedIndex]) {
        e.preventDefault();
        items[selectedIndex].click();
      }
    } else if (e.key === 'Escape') {
      dropdown.style.display = 'none';
    }
  });

  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.style.display = 'none';
    }
  });
}

function renderTournamentDropdown(dropdown, tourneys, input) {
  dropdown.innerHTML = '';
  if (!tourneys || tourneys.length === 0) {
    dropdown.style.display = 'none';
    return;
  }

  tourneys.forEach((t, idx) => {
    const item = document.createElement('div');
    item.className = 'autocomplete-item';
    item.dataset.index = idx;

    const surfKey = (t.surface || 'Hard').toLowerCase();
    const surfLabels = {
      hard: '🟦 Dur',
      clay: '🧱 Terre Battue',
      grass: '🌿 Gazon',
      carpet: '🟫 Moquette'
    };
    const surfText = surfLabels[surfKey] || t.surface;

    const levelLabels = {
      G: 'Grand Chelem',
      M: 'Masters 1000',
      F: 'Finals',
      A: 'ATP/WTA 500-250',
      C: 'Challenger/125'
    };
    const levelText = levelLabels[t.level] || 'Tournoi';
    const indoorText = t.indoor === 1 ? ' • Indoor' : '';

    item.innerHTML = `
      <div class="ac-main-col">
        <span class="ac-name">${escapeHtml(t.name)}</span>
        <span class="ac-sub">${levelText}${indoorText}</span>
      </div>
      <div class="ac-meta">
        <span class="ac-badge ${surfKey}">${surfText}</span>
      </div>
    `;

    item.addEventListener('click', () => {
      input.value = t.name;
      applyTournamentMetadata(t, true);
      dropdown.style.display = 'none';
    });

    dropdown.appendChild(item);
  });

  dropdown.style.display = 'block';
}

// Appliquer automatiquement la surface, catégorie, indoor et format
function applyTournamentMetadata(t, overwriteInputName = false) {
  if (overwriteInputName && t.name) {
    tournamentInput.value = t.name;
  }

  // 1. Surface
  if (t.surface) {
    const surfVal = capitalizeFirst(t.surface);
    if (['Hard', 'Clay', 'Grass', 'Carpet'].includes(surfVal)) {
      surfaceSelect.value = surfVal;
      triggerHighlight(surfaceSelect);
    }
  }

  // 2. Niveau / Catégorie
  if (t.level) {
    levelSelect.value = t.level;
    triggerHighlight(levelSelect);
  }

  // 3. Indoor / Outdoor
  if (t.indoor !== undefined) {
    indoorSelect.value = String(t.indoor);
    triggerHighlight(indoorSelect);
  }

  // 4. Format (Best of 5 sets en Grand Chelem ATP, sinon 3)
  if (t.level === 'G') {
    bestOfSelect.value = currentCircuit === 'atp' ? '5' : '3';
  } else {
    bestOfSelect.value = '3';
  }
  triggerHighlight(bestOfSelect);
}

// Animation visuelle de détection automatique
function triggerHighlight(element) {
  element.classList.remove('auto-detected');
  // Trigger DOM reflow to replay animation
  void element.offsetWidth;
  element.classList.add('auto-detected');
}

function updateActiveItem(items, activeIndex) {
  items.forEach((item, i) => {
    if (i === activeIndex) {
      item.classList.add('selected');
      item.scrollIntoView({ block: 'nearest' });
    } else {
      item.classList.remove('selected');
    }
  });
}

function capitalizeFirst(str) {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1).toLowerCase();
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// --------------------------------------------------------------------------
// Form Submission & Prediction
// --------------------------------------------------------------------------
// --------------------------------------------------------------------------
// Form Submission & Prediction
// --------------------------------------------------------------------------
function setupFormSubmit() {
  predictBtn.addEventListener('click', async (e) => {
    e.preventDefault();

    const p1 = p1Input.value.trim();
    const p2 = p2Input.value.trim();

    if (!p1 || !p2) {
      alert('Veuillez sélectionner deux joueurs pour prédire le match.');
      return;
    }

    if (p1.toLowerCase() === p2.toLowerCase()) {
      alert('Les deux joueurs doivent être différents.');
      return;
    }

    predictBtn.disabled = true;
    predictBtn.innerHTML = '⏳ Calcul des prédictions & scan des marchés...';

    const payload = {
      circuit: currentCircuit,
      p1: p1,
      p2: p2,
      surface: surfaceSelect.value,
      tournament: tournamentInput.value.trim() || 'Tournament',
      level: levelSelect.value,
      round: roundSelect.value,
      best_of: parseInt(bestOfSelect.value, 10),
      indoor: parseInt(indoorSelect.value, 10),
      odds1: odds1Input.value ? parseFloat(odds1Input.value) : null,
      odds2: odds2Input.value ? parseFloat(odds2Input.value) : null,
      total_line: totalLineInput && totalLineInput.value ? parseFloat(totalLineInput.value) : null,
      odds_over: oddsOverInput && oddsOverInput.value ? parseFloat(oddsOverInput.value) : null,
      odds_under: oddsUnderInput && oddsUnderInput.value ? parseFloat(oddsUnderInput.value) : null,
      handicap_line: handicapLineInput && handicapLineInput.value ? parseFloat(handicapLineInput.value) : null,
      odds_h1: oddsH1Input && oddsH1Input.value ? parseFloat(oddsH1Input.value) : null,
      odds_h2: oddsH2Input && oddsH2Input.value ? parseFloat(oddsH2Input.value) : null,
      odds_set1_p1: oddsSet1P1Input && oddsSet1P1Input.value ? parseFloat(oddsSet1P1Input.value) : null,
      odds_set1_p2: oddsSet1P2Input && oddsSet1P2Input.value ? parseFloat(oddsSet1P2Input.value) : null,
      odds_sets_over25: oddsSetsOverInput && oddsSetsOverInput.value ? parseFloat(oddsSetsOverInput.value) : null,
      odds_sets_under25: oddsSetsUnderInput && oddsSetsUnderInput.value ? parseFloat(oddsSetsUnderInput.value) : null,
      odds_tb_yes: oddsTbYesInput && oddsTbYesInput.value ? parseFloat(oddsTbYesInput.value) : null,
      odds_tb_no: oddsTbNoInput && oddsTbNoInput.value ? parseFloat(oddsTbNoInput.value) : null,
    };

    try {
      const res = await fetch('/api/predict', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Erreur lors de la prédiction');
      }

      const data = await res.json();
      renderResults(data);
      resultsSection.scrollIntoView({ behavior: 'smooth' });
    } catch (err) {
      alert(`Erreur: ${err.message}`);
    } finally {
      predictBtn.disabled = false;
      predictBtn.innerHTML = '🔮 Prédire le Match & Scanner les Value Bets';
    }
  });
}

// --------------------------------------------------------------------------
// Render Results
// --------------------------------------------------------------------------
function renderResults(data) {
  resultsSection.style.display = 'block';

  // Trigger re-analysis animation
  resultsSection.classList.remove('re-analyzing');
  void resultsSection.offsetWidth;
  resultsSection.classList.add('re-analyzing');

  // Players & Probabilities
  const p1NameEl = document.getElementById('res-p1-name');
  const p2NameEl = document.getElementById('res-p2-name');
  const p1ProbEl = document.getElementById('res-p1-prob');
  const p2ProbEl = document.getElementById('res-p2-prob');
  const p1FairOddEl = document.getElementById('res-p1-fair-odd');
  const p2FairOddEl = document.getElementById('res-p2-fair-odd');
  const barP1 = document.getElementById('bar-p1');
  const barP2 = document.getElementById('bar-p2');
  const ctxTag = document.getElementById('res-context-tag');

  const p1Pct = (data.proba_p1 * 100).toFixed(1);
  const p2Pct = (data.proba_p2 * 100).toFixed(1);

  p1NameEl.textContent = data.p1;
  p2NameEl.textContent = data.p2;
  p1ProbEl.textContent = `${p1Pct}%`;
  p2ProbEl.textContent = `${p2Pct}%`;
  p1FairOddEl.textContent = `Cote juste : ${data.fair_odds_p1.toFixed(2)}`;
  p2FairOddEl.textContent = `Cote juste : ${data.fair_odds_p2.toFixed(2)}`;

  barP1.style.width = `${p1Pct}%`;
  barP2.style.width = `${p2Pct}%`;

  const ctx = data.context;
  const tName = ctx.tournament && ctx.tournament !== 'Tournament' && ctx.tournament !== 'Tournoi' ? ctx.tournament : 'Match';
  const cpiStr = ctx.cpi ? ` • Speed: ${ctx.cpi}%` : '';
  const altStr = ctx.altitude > 0 ? ` • Alt: ${ctx.altitude}m` : '';
  ctxTag.textContent = `${tName} • ${ctx.surface}${cpiStr}${altStr} • ${ctx.round} • Best-of ${ctx.best_of}`;

  // ------------------------------------------------------------------------
  // Value Bets Across All Markets
  // ------------------------------------------------------------------------
  const vbContainer = document.getElementById('valuebet-container');
  const allVBs = data.all_value_bets || [];

  if (allVBs.length > 0) {
    vbContainer.style.display = 'block';
    vbContainer.className = 'valuebet-card is-vb';

    let vbsHtml = `
      <div class="vb-header">
        <span class="vb-badge badge-vb">🎯 ${allVBs.length} VALUE BET${allVBs.length > 1 ? 'S DÉTECTÉS' : ' DÉTECTÉ'}</span>
      </div>
      <div class="vb-summary-list">
    `;

    allVBs.forEach(vb => {
      vbsHtml += `
        <div class="vb-summary-item">
          <div class="vb-sum-left">
            <span class="vb-sum-market">${escapeHtml(vb.market)}</span>
            <span class="vb-sum-title">${escapeHtml(vb.selection)}</span>
            <span style="font-size: 11.5px; color: var(--text-dim);">Proba: ${vb.prob}% • Cote juste: ${vb.fair_odds.toFixed(2)}</span>
          </div>
          <div class="vb-sum-right">
            <div>
              <div style="font-size: 16px; font-weight: 800; color: #34d399;">@ ${vb.offered_odds.toFixed(2)}</div>
              <div style="font-size: 11px; color: #fbbf24; font-weight: 700;">+${vb.ev_pct}% EV</div>
            </div>
            <div class="vb-sum-badge">Mise: ${vb.kelly_pct}%</div>
          </div>
        </div>
      `;
    });

    vbsHtml += `</div>`;
    vbContainer.innerHTML = vbsHtml;
  } else if (odds1Input.value || (totalLineInput && totalLineInput.value) || (oddsH1Input && oddsH1Input.value) || (oddsTbYesInput && oddsTbYesInput.value)) {
    vbContainer.style.display = 'block';
    vbContainer.className = 'valuebet-card no-vb';
    vbContainer.innerHTML = `
      <div class="vb-header">
        <span class="vb-badge badge-no-vb">❌ AUCUN VALUE BET DÉTECTÉ</span>
      </div>
      <p style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">
        Les cotes bookmakers renseignées sont inférieures ou égales aux probabilités réelles calculées par l'IA.
      </p>
    `;
  } else {
    vbContainer.style.display = 'none';
  }

  // ------------------------------------------------------------------------
  // Multi-Market Analysis Grid
  // ------------------------------------------------------------------------
  const mkts = data.markets;
  const marketsGrid = document.getElementById('markets-grid');
  if (marketsGrid && mkts) {
    const tg = mkts.total_games;
    const hg = mkts.handicap_games;
    const tb = mkts.tiebreak;
    const s1 = mkts.set1_winner;
    const ns = mkts.number_of_sets;

    marketsGrid.innerHTML = `
      <!-- Total Games Card -->
      <div class="market-card">
        <div class="market-card-title">
          <span>🎾 Total de Jeux</span>
          <span class="market-meta-badge">Ligne: ${tg.line} (Exp: ${tg.expected})</span>
        </div>
        <div class="market-row-item ${tg.vb_over && tg.vb_over.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">Over ${tg.line}</span>
          <div class="mri-stats">
            <span class="mri-prob">${tg.proba_over}%</span>
            <span class="mri-fair">Cote: ${tg.fair_odds_over.toFixed(2)}</span>
            ${tg.vb_over && tg.vb_over.is_value_bet ? `<span class="mri-vb-pill vb">VB +${tg.vb_over.ev_pct}%</span>` : ''}
          </div>
        </div>
        <div class="market-row-item ${tg.vb_under && tg.vb_under.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">Under ${tg.line}</span>
          <div class="mri-stats">
            <span class="mri-prob">${tg.proba_under}%</span>
            <span class="mri-fair">Cote: ${tg.fair_odds_under.toFixed(2)}</span>
            ${tg.vb_under && tg.vb_under.is_value_bet ? `<span class="mri-vb-pill vb">VB +${tg.vb_under.ev_pct}%</span>` : ''}
          </div>
        </div>
      </div>

      <!-- Handicap Games Card (J1 -X.5 vs J2 +X.5) -->
      <div class="market-card">
        <div class="market-card-title">
          <span>⚡ Handicap de Jeux</span>
          <span class="market-meta-badge">Diff: ${hg.expected_diff > 0 ? '+' : ''}${hg.expected_diff}</span>
        </div>
        <div class="market-row-item ${hg.vb_h1 && hg.vb_h1.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(hg.label_h1)}</span>
          <div class="mri-stats">
            <span class="mri-prob">${hg.proba_h1}%</span>
            <span class="mri-fair">Cote: ${hg.fair_odds_h1.toFixed(2)}</span>
            ${hg.vb_h1 && hg.vb_h1.is_value_bet ? `<span class="mri-vb-pill vb">VB +${hg.vb_h1.ev_pct}%</span>` : ''}
          </div>
        </div>
        <div class="market-row-item ${hg.vb_h2 && hg.vb_h2.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(hg.label_h2)}</span>
          <div class="mri-stats">
            <span class="mri-prob">${hg.proba_h2}%</span>
            <span class="mri-fair">Cote: ${hg.fair_odds_h2.toFixed(2)}</span>
            ${hg.vb_h2 && hg.vb_h2.is_value_bet ? `<span class="mri-vb-pill vb">VB +${hg.vb_h2.ev_pct}%</span>` : ''}
          </div>
        </div>
      </div>

      <!-- Tie-Break in Match (+0.5 TB) Card -->
      <div class="market-card">
        <div class="market-card-title">
          <span>🎾 Au moins 1 Tie-Break (+0.5 TB)</span>
          <span class="market-meta-badge">P(Set): ${tb.proba_per_set}%</span>
        </div>
        <div class="market-row-item ${tb.vb_yes && tb.vb_yes.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">OUI (+0.5 TB)</span>
          <div class="mri-stats">
            <span class="mri-prob">${tb.proba_yes}%</span>
            <span class="mri-fair">Cote: ${tb.fair_odds_yes.toFixed(2)}</span>
            ${tb.vb_yes && tb.vb_yes.is_value_bet ? `<span class="mri-vb-pill vb">VB +${tb.vb_yes.ev_pct}%</span>` : ''}
          </div>
        </div>
        <div class="market-row-item ${tb.vb_no && tb.vb_no.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">NON (0 TB)</span>
          <div class="mri-stats">
            <span class="mri-prob">${tb.proba_no}%</span>
            <span class="mri-fair">Cote: ${tb.fair_odds_no.toFixed(2)}</span>
            ${tb.vb_no && tb.vb_no.is_value_bet ? `<span class="mri-vb-pill vb">VB +${tb.vb_no.ev_pct}%</span>` : ''}
          </div>
        </div>
      </div>

      <!-- Set 1 Winner Card -->
      <div class="market-card">
        <div class="market-card-title">
          <span>🥇 Vainqueur 1er Set</span>
          <span class="market-meta-badge">1er Set</span>
        </div>
        <div class="market-row-item ${s1.vb_p1 && s1.vb_p1.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(data.p1)}</span>
          <div class="mri-stats">
            <span class="mri-prob">${s1.proba_p1}%</span>
            <span class="mri-fair">Cote: ${s1.fair_odds_p1.toFixed(2)}</span>
            ${s1.vb_p1 && s1.vb_p1.is_value_bet ? `<span class="mri-vb-pill vb">VB +${s1.vb_p1.ev_pct}%</span>` : ''}
          </div>
        </div>
        <div class="market-row-item ${s1.vb_p2 && s1.vb_p2.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(data.p2)}</span>
          <div class="mri-stats">
            <span class="mri-prob">${s1.proba_p2}%</span>
            <span class="mri-fair">Cote: ${s1.fair_odds_p2.toFixed(2)}</span>
            ${s1.vb_p2 && s1.vb_p2.is_value_bet ? `<span class="mri-vb-pill vb">VB +${s1.vb_p2.ev_pct}%</span>` : ''}
          </div>
        </div>
      </div>

      <!-- Number of Sets Card -->
      <div class="market-card">
        <div class="market-card-title">
          <span>⏳ Nombre de Sets</span>
          <span class="market-meta-badge">Total Sets</span>
        </div>
        <div class="market-row-item ${ns.vb_over && ns.vb_over.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(ns.label_over)}</span>
          <div class="mri-stats">
            <span class="mri-prob">${ns.proba_over}%</span>
            <span class="mri-fair">Cote: ${ns.fair_odds_over.toFixed(2)}</span>
            ${ns.vb_over && ns.vb_over.is_value_bet ? `<span class="mri-vb-pill vb">VB +${ns.vb_over.ev_pct}%</span>` : ''}
          </div>
        </div>
        <div class="market-row-item ${ns.vb_under && ns.vb_under.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(ns.label_under)}</span>
          <div class="mri-stats">
            <span class="mri-prob">${ns.proba_under}%</span>
            <span class="mri-fair">Cote: ${ns.fair_odds_under.toFixed(2)}</span>
            ${ns.vb_under && ns.vb_under.is_value_bet ? `<span class="mri-vb-pill vb">VB +${ns.vb_under.ev_pct}%</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }

  // ------------------------------------------------------------------------
  // Exact Scores Matrix
  // ------------------------------------------------------------------------
  const scoresMatrix = document.getElementById('scores-matrix');
  if (scoresMatrix && mkts && mkts.exact_scores) {
    let scoresHtml = '';
    for (const [score, item] of Object.entries(mkts.exact_scores)) {
      scoresHtml += `
        <div class="score-card">
          <div class="score-title">${escapeHtml(score)}</div>
          <div class="score-proba">${item.proba}%</div>
          <div class="score-fair">Cote : ${item.fair_odds.toFixed(2)}</div>
        </div>
      `;
    }
    scoresMatrix.innerHTML = scoresHtml;
  }

  // Detailed Stats Table
  const statsBody = document.getElementById('stats-table-body');

  // Form delta indicators
  const fd1 = data.markov.form_delta_p1 || 0;
  const fd2 = data.markov.form_delta_p2 || 0;
  const formIcon = (d) => d > 0.4 ? '🔥' : (d < -0.4 ? '🧊' : '➖');
  const formColor = (d) => d > 0.4 ? '#34d399' : (d < -0.4 ? '#60a5fa' : 'var(--text-muted)');
  const formStr = (d) => (d > 0 ? '+' : '') + d.toFixed(2) + '%';

  statsBody.innerHTML = `
    <tr><td>Elo Global (avec Decay)</td><td>${data.elo.global_p1}</td><td>${data.elo.global_p2}</td></tr>
    <tr><td>Elo ${ctx.surface}</td><td>${data.elo.surface_p1}</td><td>${data.elo.surface_p2}</td></tr>
    <tr><td>Serve Elo</td><td>${data.elo.serve_p1}</td><td>${data.elo.serve_p2}</td></tr>
    <tr><td>Return Elo</td><td>${data.elo.return_p1}</td><td>${data.elo.return_p2}</td></tr>
    <tr><td>H2H Historique</td><td>${data.h2h.wins_p1}</td><td>${data.h2h.wins_p2}</td></tr>
    <tr><td>P(Hold Service - Markov)</td><td>${data.markov.hold_proba_p1}%</td><td>${data.markov.hold_proba_p2}%</td></tr>
    <tr>
      <td>P(Point Service) <span style="font-size:10px; color:var(--text-dim);">brut → ajusté</span></td>
      <td>${data.markov.serve_point_raw_p1}% → <b>${data.markov.serve_point_p1}%</b></td>
      <td>${data.markov.serve_point_raw_p2}% → <b>${data.markov.serve_point_p2}%</b></td>
    </tr>
    <tr>
      <td>🔥 Freshness (5 derniers matchs)</td>
      <td style="color:${formColor(fd1)}; font-weight:700;">${formIcon(fd1)} ${formStr(fd1)}</td>
      <td style="color:${formColor(fd2)}; font-weight:700;">${formIcon(fd2)} ${formStr(fd2)}</td>
    </tr>
    <tr><td>Total Jeux Prévu</td><td colspan="2" style="text-align:center; color:#f59e0b;">${data.markov.expected_total_games} jeux</td></tr>
  `;
}
