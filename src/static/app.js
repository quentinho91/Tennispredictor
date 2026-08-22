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
  setupHistory();
  setupDailyScanner();
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
      loadDailyScanner(false);
    });
  });
}

// Helper to safely parse decimal numbers with dot or comma
function parseOddInput(el) {
  if (!el || !el.value) return null;
  const raw = String(el.value).trim().replace(',', '.');
  const val = parseFloat(raw);
  return (isNaN(val) || val <= 1.0) ? null : val;
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

    if (oddsH1Input && oddsH2Input) {
      const tempH = oddsH1Input.value;
      oddsH1Input.value = oddsH2Input.value;
      oddsH2Input.value = tempH;
    }

    if (oddsSet1P1Input && oddsSet1P2Input) {
      const tempS1 = oddsSet1P1Input.value;
      oddsSet1P1Input.value = oddsSet1P2Input.value;
      oddsSet1P2Input.value = tempS1;
    }

    updateDynamicLabels();
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
      odds1: parseOddInput(odds1Input),
      odds2: parseOddInput(odds2Input),
      total_line: totalLineInput && totalLineInput.value ? parseFloat(String(totalLineInput.value).replace(',', '.')) : null,
      odds_over: parseOddInput(oddsOverInput),
      odds_under: parseOddInput(oddsUnderInput),
      handicap_line: handicapLineInput && handicapLineInput.value ? parseFloat(String(handicapLineInput.value).replace(',', '.')) : null,
      odds_h1: parseOddInput(oddsH1Input),
      odds_h2: parseOddInput(oddsH2Input),
      odds_set1_p1: parseOddInput(oddsSet1P1Input),
      odds_set1_p2: parseOddInput(oddsSet1P2Input),
      odds_sets_over25: parseOddInput(oddsSetsOverInput),
      odds_sets_under25: parseOddInput(oddsSetsUnderInput),
      odds_tb_yes: parseOddInput(oddsTbYesInput),
      odds_tb_no: parseOddInput(oddsTbNoInput),
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
      renderResults(data, payload);
      savePredictionToHistory(data, payload);
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
function renderResults(data, payload = {}) {
  resultsSection.style.display = 'block';

  // Trigger re-analysis animation
  resultsSection.classList.remove('re-analyzing');
  void resultsSection.offsetWidth;
  resultsSection.classList.add('re-analyzing');

  // Re-analysis toast notification
  const toastEl = document.getElementById('re-analysis-toast');
  if (toastEl) {
    const now = new Date();
    const timeStr = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
    toastEl.innerHTML = `<span>⚡ Analyse actualisée à <b>${timeStr}</b> avec les cotes renseignées</span>`;
    toastEl.style.display = 'flex';
  }

  // Players & Probabilities
  const p1NameEl = document.getElementById('res-p1-name');
  const p2NameEl = document.getElementById('res-p2-name');
  const p1ProbEl = document.getElementById('res-p1-prob');
  const p2ProbEl = document.getElementById('res-p2-prob');
  const p1FairOddEl = document.getElementById('res-p1-fair-odd');
  const p2FairOddEl = document.getElementById('res-p2-fair-odd');
  const p1BookOddEl = document.getElementById('res-p1-bookmaker-odd');
  const p2BookOddEl = document.getElementById('res-p2-bookmaker-odd');
  const barP1 = document.getElementById('bar-p1');
  const barP2 = document.getElementById('bar-p2');
  const ctxTag = document.getElementById('res-context-tag');

  const p1Pct = (data.proba_p1 * 100).toFixed(1);
  const p2Pct = (data.proba_p2 * 100).toFixed(1);

  p1NameEl.textContent = data.p1;
  p2NameEl.textContent = data.p2;
  p1ProbEl.textContent = `${p1Pct}%`;
  p2ProbEl.textContent = `${p2Pct}%`;
  p1FairOddEl.innerHTML = `Cote juste IA : <b>${data.fair_odds_p1.toFixed(2)}</b>`;
  p2FairOddEl.innerHTML = `Cote juste IA : <b>${data.fair_odds_p2.toFixed(2)}</b>`;

  // Display Bookmaker Odds & EV Badges in Hero Duel Card
  if (p1BookOddEl) {
    if (data.offered_odds_p1) {
      const vb1 = data.vb_p1;
      let badgeClass = 'is-no-vb';
      let badgeText = `${vb1 && vb1.ev_pct > 0 ? '+' : ''}${vb1 ? vb1.ev_pct : '0.0'}% EV`;
      if (vb1 && vb1.is_value_bet) {
        badgeClass = 'is-vb';
        badgeText = `🎯 Value Bet (+${vb1.ev_pct}% EV)`;
      } else if (vb1 && vb1.ev_pct > 0) {
        badgeClass = 'is-low-ev';
        badgeText = `⚖️ +${vb1.ev_pct}% EV`;
      }
      p1BookOddEl.className = `bookmaker-odd-badge ${badgeClass}`;
      p1BookOddEl.innerHTML = `Bookmaker : <b>@ ${data.offered_odds_p1.toFixed(2)}</b> <span style="font-size:10px; margin-left:4px;">(${badgeText})</span>`;
    } else {
      p1BookOddEl.className = 'bookmaker-odd-badge';
      p1BookOddEl.innerHTML = `Bookmaker : <span style="color:var(--text-dim);">Non saisie</span>`;
    }
  }

  if (p2BookOddEl) {
    if (data.offered_odds_p2) {
      const vb2 = data.vb_p2;
      let badgeClass = 'is-no-vb';
      let badgeText = `${vb2 && vb2.ev_pct > 0 ? '+' : ''}${vb2 ? vb2.ev_pct : '0.0'}% EV`;
      if (vb2 && vb2.is_value_bet) {
        badgeClass = 'is-vb';
        badgeText = `🎯 Value Bet (+${vb2.ev_pct}% EV)`;
      } else if (vb2 && vb2.ev_pct > 0) {
        badgeClass = 'is-low-ev';
        badgeText = `⚖️ +${vb2.ev_pct}% EV`;
      }
      p2BookOddEl.className = `bookmaker-odd-badge ${badgeClass}`;
      p2BookOddEl.innerHTML = `Bookmaker : <b>@ ${data.offered_odds_p2.toFixed(2)}</b> <span style="font-size:10px; margin-left:4px;">(${badgeText})</span>`;
    } else {
      p2BookOddEl.className = 'bookmaker-odd-badge';
      p2BookOddEl.innerHTML = `Bookmaker : <span style="color:var(--text-dim);">Non saisie</span>`;
    }
  }

  barP1.style.width = `${p1Pct}%`;
  barP2.style.width = `${p2Pct}%`;

  // ------------------------------------------------------------------------
  // Form Timeline (5 derniers matchs)
  // ------------------------------------------------------------------------
  if (data.recent_matches) {
    renderFormTimeline('p1-form-timeline', data.recent_matches.p1, data.p1);
    renderFormTimeline('p2-form-timeline', data.recent_matches.p2, data.p2);
  }

  // ------------------------------------------------------------------------
  // Confidence Badge & Indicators
  // ------------------------------------------------------------------------
  const conf = data.confidence;
  const confBadge = document.getElementById('confidence-badge');
  const confDetails = document.getElementById('confidence-details');
  if (conf && confBadge) {
    confBadge.className = `confidence-badge confidence-${conf.level}`;
    const icon = conf.level === 'high' ? '🛡️' : (conf.level === 'medium' ? '⚖️' : '⚠️');
    confBadge.textContent = `${icon} ${conf.label} (${conf.score}%)`;

    if (confDetails && conf.details) {
      confDetails.innerHTML = `
        <span class="confidence-detail-item" title="Écart Elo">⚔️ Elo: ${conf.details.elo}%</span>
        <span class="confidence-detail-item" title="Historique H2H">🤝 H2H: ${conf.details.h2h}%</span>
        <span class="confidence-detail-item" title="Données joueurs disponibles">📊 Données: ${conf.details.data_quality}%</span>
        <span class="confidence-detail-item" title="Accord XGBoost / Markov">🎯 Accord: ${conf.details.model_agreement}%</span>
      `;
    }
  }

  const ctx = data.context;
  const tName = ctx.tournament && ctx.tournament !== 'Tournament' && ctx.tournament !== 'Tournoi' ? ctx.tournament : 'Match';
  const cpiStr = ctx.cpi ? ` • Speed: ${ctx.cpi}%` : '';
  const altStr = ctx.altitude > 0 ? ` • Alt: ${ctx.altitude}m` : '';
  ctxTag.textContent = `${tName} • ${ctx.surface}${cpiStr}${altStr} • ${ctx.round} • Best-of ${ctx.best_of}`;

  // ------------------------------------------------------------------------
  // Value Bets Across All Markets (avec Filtrage Anti-Corrélation & Calculateur Bankroll)
  // ------------------------------------------------------------------------
  renderValueBetsContainer(
    data.all_value_bets || [],
    data.scanned_markets || [],
    data.recommended_value_bets || [],
    data.correlated_masked_bets || [],
    data.has_correlated_bets || false,
    data.filter_note || ''
  );

  // ------------------------------------------------------------------------
  // Multi-Market Analysis Grid (avec affichage cotes bookmakers saisies)
  // ------------------------------------------------------------------------
  const mkts = data.markets;
  const marketsGrid = document.getElementById('markets-grid');
  if (marketsGrid && mkts) {
    const tg = mkts.total_games;
    const hg = mkts.handicap_games;
    const tb = mkts.tiebreak;
    const s1 = mkts.set1_winner;
    const ns = mkts.number_of_sets;

    const renderMarketPill = (vbObj) => {
      if (!vbObj || !vbObj.offered_odds) return '';
      const isVb = vbObj.is_value_bet;
      const ev = vbObj.ev_pct;
      const pillClass = isVb ? 'vb' : (ev > 0 ? 'low' : 'neg');
      const text = isVb ? `VB +${ev}%` : `${ev > 0 ? '+' : ''}${ev}% EV`;
      return `
        <span class="mri-bookmaker" title="Cote bookmaker saisie">Book: @${vbObj.offered_odds.toFixed(2)}</span>
        <span class="mri-ev-pill ${pillClass}" title="Espérance de gain">${text}</span>
      `;
    };

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
            <span class="mri-prob" title="Probabilité réelle IA">${tg.proba_over}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${tg.fair_odds_over.toFixed(2)}</span>
            ${renderMarketPill(tg.vb_over)}
          </div>
        </div>
        <div class="market-row-item ${tg.vb_under && tg.vb_under.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">Under ${tg.line}</span>
          <div class="mri-stats">
            <span class="mri-prob" title="Probabilité réelle IA">${tg.proba_under}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${tg.fair_odds_under.toFixed(2)}</span>
            ${renderMarketPill(tg.vb_under)}
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
            <span class="mri-prob" title="Probabilité réelle IA">${hg.proba_h1}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${hg.fair_odds_h1.toFixed(2)}</span>
            ${renderMarketPill(hg.vb_h1)}
          </div>
        </div>
        <div class="market-row-item ${hg.vb_h2 && hg.vb_h2.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(hg.label_h2)}</span>
          <div class="mri-stats">
            <span class="mri-prob" title="Probabilité réelle IA">${hg.proba_h2}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${hg.fair_odds_h2.toFixed(2)}</span>
            ${renderMarketPill(hg.vb_h2)}
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
            <span class="mri-prob" title="Probabilité réelle IA">${tb.proba_yes}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${tb.fair_odds_yes.toFixed(2)}</span>
            ${renderMarketPill(tb.vb_yes)}
          </div>
        </div>
        <div class="market-row-item ${tb.vb_no && tb.vb_no.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">NON (0 TB)</span>
          <div class="mri-stats">
            <span class="mri-prob" title="Probabilité réelle IA">${tb.proba_no}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${tb.fair_odds_no.toFixed(2)}</span>
            ${renderMarketPill(tb.vb_no)}
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
            <span class="mri-prob" title="Probabilité réelle IA">${s1.proba_p1}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${s1.fair_odds_p1.toFixed(2)}</span>
            ${renderMarketPill(s1.vb_p1)}
          </div>
        </div>
        <div class="market-row-item ${s1.vb_p2 && s1.vb_p2.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(data.p2)}</span>
          <div class="mri-stats">
            <span class="mri-prob" title="Probabilité réelle IA">${s1.proba_p2}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${s1.fair_odds_p2.toFixed(2)}</span>
            ${renderMarketPill(s1.vb_p2)}
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
            <span class="mri-prob" title="Probabilité réelle IA">${ns.proba_over}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${ns.fair_odds_over.toFixed(2)}</span>
            ${renderMarketPill(ns.vb_over)}
          </div>
        </div>
        <div class="market-row-item ${ns.vb_under && ns.vb_under.is_value_bet ? 'highlight-vb' : ''}">
          <span class="mri-name">${escapeHtml(ns.label_under)}</span>
          <div class="mri-stats">
            <span class="mri-prob" title="Probabilité réelle IA">${ns.proba_under}%</span>
            <span class="mri-fair" title="Cote juste IA">Juste: ${ns.fair_odds_under.toFixed(2)}</span>
            ${renderMarketPill(ns.vb_under)}
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

// --------------------------------------------------------------------------
// Form Timeline (5 Derniers Matchs)
// --------------------------------------------------------------------------
function renderFormTimeline(containerId, matches, playerName) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!matches || matches.length === 0) {
    container.innerHTML = '<span class="form-timeline-label">Forme : N/A</span>';
    return;
  }

  let html = '<span class="form-timeline-label">Forme :</span>';
  matches.forEach(m => {
    const isWin = m.win;
    const isRet = m.retirement;
    let dotClass = isWin ? 'win' : 'loss';
    let label = isWin ? 'V' : 'D';
    if (isRet && !isWin) {
      dotClass = 'ret';
      label = 'AB';
    }

    const statusText = isWin ? '✅ Victoire' : (isRet ? '⚠️ Abandon' : '❌ Défaite');
    const statusColor = isWin ? '#34d399' : (isRet ? '#fbbf24' : '#f87171');
    const tournText = m.tournament ? `${escapeHtml(m.tournament)}` : 'Tournoi';
    const surfText = m.surface ? ` • ${escapeHtml(m.surface)}` : '';
    const oppText = m.opponent ? `vs ${escapeHtml(m.opponent)}` : '';
    const scoreText = m.score ? `Score : ${escapeHtml(m.score)}` : '';

    html += `
      <div class="form-dot ${dotClass}">
        ${label}
        <div class="dot-tooltip">
          <div style="font-weight:700; color:${statusColor};">${statusText}</div>
          <div style="color:var(--text-dim); font-size:10px;">🏆 ${tournText}${surfText}</div>
          <div style="font-size:11px; margin: 1px 0;">${oppText}</div>
          <div style="font-size:10px; color:#fbbf24; font-weight:600;">${scoreText}</div>
        </div>
      </div>
    `;
  });

  container.innerHTML = html;
}

// --------------------------------------------------------------------------
// Value Bets Rendering & Real-time Euro Bankroll Calculator (avec Filtre Anti-Corrélation)
// --------------------------------------------------------------------------
function renderValueBetsContainer(allVBs, scannedMarkets = [], recommendedVBs = [], maskedVBs = [], hasCorr = false, filterNote = '') {
  const vbContainer = document.getElementById('valuebet-container');
  if (!vbContainer) return;

  // Use recommended value bets (filtered against correlation) as active picks
  const activeVBs = (recommendedVBs && recommendedVBs.length > 0) ? recommendedVBs : (allVBs || []);

  // If no Value Bets were detected
  if (!activeVBs || activeVBs.length === 0) {
    // Check if user provided any odds
    if (scannedMarkets && scannedMarkets.length > 0) {
      vbContainer.style.display = 'block';
      vbContainer.className = 'valuebet-card no-vb';

      let rowsHtml = '';
      scannedMarkets.forEach(sm => {
        const evColor = sm.ev_pct > 0 ? '#fbbf24' : '#f87171';
        const evSign = sm.ev_pct > 0 ? '+' : '';
        const diagText = sm.ev_pct > 0 ? 'Marge trop faible' : 'Cote insuffisante';
        const sconf = sm.confidence || { score: 50, label: 'Modérée', level: 'medium', icon: '⚖️' };
        rowsHtml += `
          <tr>
            <td><b>${escapeHtml(sm.selection)}</b> <span style="color:var(--text-dim); font-size:10.5px;">(${escapeHtml(sm.market)})</span></td>
            <td style="color:#f8fafc;">@ ${sm.offered_odds.toFixed(2)}</td>
            <td style="color:var(--text-muted);">${sm.fair_odds.toFixed(2)}</td>
            <td style="color:${evColor}; font-weight:700;">${evSign}${sm.ev_pct}%</td>
            <td><span class="vb-conf-pill ${sconf.level}" style="font-size:10px; padding:1px 6px;">${sconf.icon} ${sconf.score}%</span></td>
            <td style="color:var(--text-dim); font-size:11px;">${diagText}</td>
          </tr>
        `;
      });

      vbContainer.innerHTML = `
        <div class="vb-header">
          <span class="vb-badge badge-no-vb">❌ AUCUN VALUE BET DÉTECTÉ</span>
          <span style="font-size: 11px; color: var(--text-dim);">${scannedMarkets.length} cote${scannedMarkets.length > 1 ? 's analysées' : ' analysée'}</span>
        </div>
        <p style="font-size: 12.5px; color: var(--text-muted); margin-top: 4px;">
          Les cotes bookmakers renseignées sont inférieures ou égales aux probabilités réelles calculées par l'IA (marge bookmaker non couverte).
        </p>
        <table class="scanned-odds-table">
          <thead>
            <tr>
              <th>Sélection</th>
              <th>Cote Saisie</th>
              <th>Cote Juste IA</th>
              <th>Espérance EV</th>
              <th>Confiance VB</th>
              <th>Diagnostic</th>
            </tr>
          </thead>
          <tbody>
            ${rowsHtml}
          </tbody>
        </table>
      `;
    } else {
      vbContainer.style.display = 'none';
    }
    return;
  }

  // Value Bets Found
  vbContainer.style.display = 'block';
  vbContainer.className = 'valuebet-card is-vb';

  let currentBankroll = parseFloat(localStorage.getItem('tp_user_bankroll') || '500');
  if (isNaN(currentBankroll) || currentBankroll <= 0) currentBankroll = 500;

  let currentStrategy = localStorage.getItem('tp_user_strategy') || 'quarter';

  function getItemsHtml(bankroll, strategy) {
    let listHtml = '';
    activeVBs.forEach((vb, idx) => {
      let stakePct = vb.kelly_quarter_pct || vb.kelly_pct || 2.0;
      if (strategy === 'half') {
        stakePct = vb.kelly_half_pct || (stakePct * 2.0);
      } else if (strategy === 'full') {
        stakePct = vb.kelly_full_pct || (stakePct * 4.0);
      } else if (strategy === 'flat1') {
        stakePct = 1.0;
      } else if (strategy === 'flat2') {
        stakePct = 2.0;
      }
      stakePct = Math.min(stakePct, 15.0);

      const stakeAmount = (bankroll * stakePct / 100).toFixed(2);
      const netProfit = (parseFloat(stakeAmount) * (vb.offered_odds - 1.0)).toFixed(2);

      const isPrimary = vb.is_primary_pick || idx === 0;
      const rankBadge = isPrimary ? '<span class="vb-pick-tag">⭐ PICK RECOMMANDÉ #1</span>' : `<span class="vb-pick-tag" style="background:#38bdf8; color:#0c4a6e;">🎯 PICK COMPLÉMENTAIRE #${idx + 1}</span>`;
      const conf = vb.confidence || { score: 75.0, label: 'Haute', level: 'high', icon: '🔥' };
      const confPillHtml = `<span class="vb-conf-pill ${conf.level}" title="Indice de confiance spécifique à ce Value Bet : ${conf.label}">${conf.icon} Confiance VB : <b>${conf.score}%</b> (${conf.label})</span>`;

      listHtml += `
        <div class="vb-summary-item ${isPrimary ? 'primary-pick' : ''}">
          <div class="vb-sum-left">
            <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
              ${rankBadge}
              ${confPillHtml}
            </div>
            <span class="vb-sum-market">${escapeHtml(vb.market)}</span>
            <span class="vb-sum-title">${escapeHtml(vb.selection)}</span>
            <span style="font-size: 11.5px; color: var(--text-dim);">Proba: ${vb.prob}% • Cote juste: ${vb.fair_odds.toFixed(2)}</span>
          </div>
          <div class="vb-sum-right">
            <div style="text-align: right;">
              <div style="font-size: 16px; font-weight: 800; color: #34d399;">@ ${vb.offered_odds.toFixed(2)}</div>
              <div style="font-size: 11px; color: #fbbf24; font-weight: 700;">+${vb.ev_pct}% EV</div>
            </div>
            <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">
              <div class="vb-euro-badge">💶 Miser <b>${stakeAmount} €</b> (${stakePct.toFixed(1)}%)</div>
              <div class="vb-profit-tag">Gain net : +${netProfit} €</div>
            </div>
          </div>
        </div>
      `;
    });
    return listHtml;
  }

  function getMaskedItemsHtml() {
    if (!maskedVBs || maskedVBs.length === 0) return '';
    let html = '';
    maskedVBs.forEach(mvb => {
      const mconf = mvb.confidence || { score: 60.0, label: 'Modérée', level: 'medium', icon: '⚖️' };
      html += `
        <div class="masked-vb-item">
          <div>
            <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
              <span style="font-size: 13px; font-weight: 700; color: #f1f5f9;">${escapeHtml(mvb.selection)} <span style="font-size: 11px; color: var(--text-dim);">(${escapeHtml(mvb.market)})</span></span>
              <span class="vb-conf-pill ${mconf.level}" style="font-size: 9.5px; padding: 1px 6px;">${mconf.icon} Confiance: <b>${mconf.score}%</b></span>
            </div>
            <div style="font-size: 10.5px; color: #fbbf24; margin-top: 2px;">🔒 ${escapeHtml(mvb.masked_reason || 'Pari corrélé')}</div>
          </div>
          <div style="text-align: right;">
            <div style="font-size: 14px; font-weight: 800; color: #94a3b8;">@ ${mvb.offered_odds.toFixed(2)}</div>
            <div style="font-size: 10.5px; color: #34d399;">+${mvb.ev_pct}% EV</div>
          </div>
        </div>
      `;
    });
    return html;
  }

  const antiCorrBannerHtml = hasCorr ? `
    <div class="anti-corr-banner">
      <div class="ac-icon">🛡️</div>
      <div class="ac-text">
        <div class="ac-title">Filtre Anti-Corrélation Actif (${allVBs.length} Value Bets détectés)</div>
        <div class="ac-desc">
          Plusieurs paris dépendent du même scénario de match. L'IA a isolé <b>le meilleur pari (#1)</b> pour éliminer le risque d'accumulation de mises sur un même scénario.
        </div>
      </div>
    </div>
  ` : '';

  const maskedAccordionHtml = (maskedVBs && maskedVBs.length > 0) ? `
    <div class="masked-vbs-accordion">
      <button type="button" id="btn-toggle-masked-vbs" class="btn-toggle-masked">
        <span>🔒 Voir les ${maskedVBs.length} autre(s) pari(s) corrélé(s) masqué(s)</span>
        <span id="masked-toggle-icon" class="toggle-arrow">▼</span>
      </button>
      <div id="masked-vbs-content" class="masked-vbs-content" style="display: none;">
        <div class="masked-vbs-warning">
          ⚠️ <b>Règle de Bankroll :</b> Ces paris découlent du même scénario que le Pick sélectionné. Ne les cumulez pas afin d'éviter une perte multiple en cas de scénario inverse.
        </div>
        ${getMaskedItemsHtml()}
      </div>
    </div>
  ` : '';

  vbContainer.innerHTML = `
    <div class="vb-header">
      <span class="vb-badge badge-vb">🎯 ${activeVBs.length} VALUE BET${activeVBs.length > 1 ? 'S RECOMMANDÉS' : ' RECOMMANDÉ'}</span>
      ${hasCorr ? `<span style="font-size: 11px; color: #60a5fa; font-weight: 700;">🛡️ 1-2 Picks Max</span>` : ''}
    </div>

    ${antiCorrBannerHtml}

    <!-- Bankroll & Strategy Controls -->
    <div class="bankroll-bar">
      <div class="bk-group">
        <label class="bk-label" for="user-bankroll-input">💶 Bankroll :</label>
        <div class="bk-input-wrap">
          <input type="number" id="user-bankroll-input" class="bk-input" value="${currentBankroll}" min="10" step="50">
          <span class="bk-curr">€</span>
        </div>
      </div>
      <div class="bk-group">
        <label class="bk-label" for="user-strategy-select">Gestion de mise :</label>
        <select id="user-strategy-select" class="bk-select">
          <option value="quarter" ${currentStrategy === 'quarter' ? 'selected' : ''}>Quart-Kelly (Conseillé)</option>
          <option value="half" ${currentStrategy === 'half' ? 'selected' : ''}>Demi-Kelly (Modéré)</option>
          <option value="full" ${currentStrategy === 'full' ? 'selected' : ''}>Kelly Plein (Agressif)</option>
          <option value="flat1" ${currentStrategy === 'flat1' ? 'selected' : ''}>Mise Fixe 1% (Prudent)</option>
          <option value="flat2" ${currentStrategy === 'flat2' ? 'selected' : ''}>Mise Fixe 2%</option>
        </select>
      </div>
    </div>

    <div id="vb-dynamic-list" class="vb-summary-list">
      ${getItemsHtml(currentBankroll, currentStrategy)}
    </div>

    ${maskedAccordionHtml}
  `;

  const bkInput = document.getElementById('user-bankroll-input');
  const stratSelect = document.getElementById('user-strategy-select');
  const listEl = document.getElementById('vb-dynamic-list');
  const toggleMaskedBtn = document.getElementById('btn-toggle-masked-vbs');
  const maskedContent = document.getElementById('masked-vbs-content');
  const maskedIcon = document.getElementById('masked-toggle-icon');

  if (toggleMaskedBtn && maskedContent) {
    toggleMaskedBtn.addEventListener('click', () => {
      const isHidden = maskedContent.style.display === 'none';
      maskedContent.style.display = isHidden ? 'flex' : 'none';
      if (maskedIcon) maskedIcon.classList.toggle('open', isHidden);
    });
  }

  const updateStakes = () => {
    let val = parseFloat(bkInput.value);
    if (isNaN(val) || val <= 0) val = 100;
    const strat = stratSelect.value;
    localStorage.setItem('tp_user_bankroll', String(val));
    localStorage.setItem('tp_user_strategy', strat);
    if (listEl) {
      listEl.innerHTML = getItemsHtml(val, strat);
    }
  };

  if (bkInput) bkInput.addEventListener('input', updateStakes);
  if (stratSelect) stratSelect.addEventListener('change', updateStakes);
}

// ==========================================================================
// PREDICTION HISTORY & ROI TRACKING (LOCALSTORAGE)
// ==========================================================================
const HISTORY_STORAGE_KEY = 'tennis_pred_history_v1';
const MAX_HISTORY_ITEMS = 10;

function getHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch (e) {
    return [];
  }
}

function saveHistory(list) {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(list));
  } catch (e) {
    console.error('Erreur localStorage', e);
  }
}

function savePredictionToHistory(data, payload) {
  const history = getHistory();

  // Déterminer le pick principal (Value Bet prioritaire recommandé, sinon Favori du match)
  let mainPick = '';
  let pickOdds = null;
  let pickType = 'fav';

  const vbs = (data.recommended_value_bets && data.recommended_value_bets.length > 0) ? data.recommended_value_bets : (data.all_value_bets || []);
  if (vbs.length > 0) {
    const topVb = vbs[0];
    const confScore = topVb.confidence ? `${topVb.confidence.score}%` : '';
    mainPick = `🎯 VB: ${topVb.selection}${confScore ? ' (' + confScore + ' conf)' : ''}`;
    pickOdds = topVb.offered_odds;
    pickType = 'vb';
  } else {
    const isP1Fav = data.proba_p1 >= data.proba_p2;
    const favName = isP1Fav ? data.p1 : data.p2;
    const favProba = isP1Fav ? (data.proba_p1 * 100).toFixed(1) : (data.proba_p2 * 100).toFixed(1);
    mainPick = `⭐ Vainqueur: ${favName} (${favProba}%)`;
    pickOdds = isP1Fav ? (payload.odds1 || data.fair_odds_p1) : (payload.odds2 || data.fair_odds_p2);
  }

  const now = new Date();
  const timeStr = `${String(now.getDate()).padStart(2, '0')}/${String(now.getMonth() + 1).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;

  const item = {
    id: Date.now().toString(),
    timestamp: timeStr,
    circuit: data.circuit,
    p1: data.p1,
    p2: data.p2,
    proba_p1: (data.proba_p1 * 100).toFixed(1),
    proba_p2: (data.proba_p2 * 100).toFixed(1),
    tournament: data.context.tournament && data.context.tournament !== 'Tournament' ? data.context.tournament : 'Tournoi',
    surface: data.context.surface,
    mainPick: mainPick,
    pickOdds: pickOdds ? parseFloat(pickOdds) : null,
    pickType: pickType,
    hasVb: vbs.length > 0,
    vbCount: vbs.length,
    result: 'pending', // 'pending' | 'won' | 'lost'
    payload: payload, // Cotes et paramètres complets du formulaire
  };

  // Éviter les doublons consécutifs identiques : mettre à jour le dernier élément
  if (history.length > 0 && history[0].p1 === item.p1 && history[0].p2 === item.p2 && history[0].tournament === item.tournament) {
    item.result = history[0].result;
    item.id = history[0].id;
    history[0] = item;
  } else {
    history.unshift(item);
  }

  if (history.length > MAX_HISTORY_ITEMS) {
    history.length = MAX_HISTORY_ITEMS;
  }

  saveHistory(history);
  renderHistory();
}

function updateHistoryResult(id, newResult) {
  const history = getHistory();
  const item = history.find(h => h.id === id);
  if (item) {
    item.result = item.result === newResult ? 'pending' : newResult;
    saveHistory(history);
    renderHistory();
  }
}
window.updateHistoryResult = updateHistoryResult;

function deleteHistoryItem(id, event) {
  if (event) event.stopPropagation();
  let history = getHistory();
  const item = history.find(h => h.id === id);
  const matchName = item ? `${item.p1} vs ${item.p2}` : 'ce match';

  if (confirm(`Supprimer ${matchName} de l'historique ?`)) {
    history = history.filter(h => h.id !== id);
    saveHistory(history);
    renderHistory();
  }
}
window.deleteHistoryItem = deleteHistoryItem;

function loadPredictionFromHistory(id) {
  const history = getHistory();
  const item = history.find(h => h.id === id);
  if (!item) return;

  const p = item.payload || {
    circuit: item.circuit,
    p1: item.p1,
    p2: item.p2,
    tournament: item.tournament,
    surface: item.surface,
    odds1: item.pickType === 'fav' ? item.pickOdds : null
  };

  // 1. Circuit switch
  const targetCircuit = (p.circuit || item.circuit || 'atp').toLowerCase();
  circuitBtns.forEach(b => {
    if (b.dataset.circuit === targetCircuit) {
      b.classList.add('active');
    } else {
      b.classList.remove('active');
    }
  });
  currentCircuit = targetCircuit;

  // 2. Player Inputs
  p1Input.value = p.p1 || item.p1 || '';
  p2Input.value = p.p2 || item.p2 || '';
  selectedP1 = p.p1 || item.p1 || '';
  selectedP2 = p.p2 || item.p2 || '';

  // 3. Tournament & Context Options
  if (tournamentInput) tournamentInput.value = (p.tournament && p.tournament !== 'Tournoi' && p.tournament !== 'Tournament') ? p.tournament : '';
  if (surfaceSelect && p.surface) surfaceSelect.value = p.surface;
  if (levelSelect && p.level) levelSelect.value = p.level;
  if (roundSelect && p.round) roundSelect.value = p.round;
  if (bestOfSelect && p.best_of) bestOfSelect.value = String(p.best_of);
  if (indoorSelect && p.indoor !== undefined) indoorSelect.value = String(p.indoor);

  // 4. Main Odds
  if (odds1Input) odds1Input.value = (p.odds1 !== null && p.odds1 !== undefined) ? p.odds1 : '';
  if (odds2Input) odds2Input.value = (p.odds2 !== null && p.odds2 !== undefined) ? p.odds2 : '';

  // 5. Secondary Markets Odds
  const hasSecOdds = Boolean(
    p.odds_over || p.odds_under || p.total_line ||
    p.odds_h1 || p.odds_h2 || p.handicap_line ||
    p.odds_set1_p1 || p.odds_set1_p2 ||
    p.odds_sets_over25 || p.odds_sets_under25 ||
    p.odds_tb_yes || p.odds_tb_no
  );

  if (totalLineInput) totalLineInput.value = (p.total_line !== null && p.total_line !== undefined) ? p.total_line : '';
  if (oddsOverInput) oddsOverInput.value = (p.odds_over !== null && p.odds_over !== undefined) ? p.odds_over : '';
  if (oddsUnderInput) oddsUnderInput.value = (p.odds_under !== null && p.odds_under !== undefined) ? p.odds_under : '';

  if (handicapLineInput) handicapLineInput.value = (p.handicap_line !== null && p.handicap_line !== undefined) ? p.handicap_line : '';
  if (oddsH1Input) oddsH1Input.value = (p.odds_h1 !== null && p.odds_h1 !== undefined) ? p.odds_h1 : '';
  if (oddsH2Input) oddsH2Input.value = (p.odds_h2 !== null && p.odds_h2 !== undefined) ? p.odds_h2 : '';

  if (oddsTbYesInput) oddsTbYesInput.value = (p.odds_tb_yes !== null && p.odds_tb_yes !== undefined) ? p.odds_tb_yes : '';
  if (oddsTbNoInput) oddsTbNoInput.value = (p.odds_tb_no !== null && p.odds_tb_no !== undefined) ? p.odds_tb_no : '';

  if (oddsSet1P1Input) oddsSet1P1Input.value = (p.odds_set1_p1 !== null && p.odds_set1_p1 !== undefined) ? p.odds_set1_p1 : '';
  if (oddsSet1P2Input) oddsSet1P2Input.value = (p.odds_set1_p2 !== null && p.odds_set1_p2 !== undefined) ? p.odds_set1_p2 : '';

  if (oddsSetsOverInput) oddsSetsOverInput.value = (p.odds_sets_over25 !== null && p.odds_sets_over25 !== undefined) ? p.odds_sets_over25 : '';
  if (oddsSetsUnderInput) oddsSetsUnderInput.value = (p.odds_sets_under25 !== null && p.odds_sets_under25 !== undefined) ? p.odds_sets_under25 : '';

  // Open secondary accordion if secondary odds exist
  if (hasSecOdds && secMarketsContent) {
    secMarketsContent.style.display = 'flex';
    if (secToggleIcon) secToggleIcon.classList.add('open');
  }

  // Update dynamic labels
  updateDynamicLabels();

  // Scroll to main match card smoothly
  const mainCard = document.querySelector('main.card');
  if (mainCard) {
    mainCard.scrollIntoView({ behavior: 'smooth' });
  }

  // Automatically trigger prediction and report generation
  if (predictBtn) {
    predictBtn.click();
  }
}
window.loadPredictionFromHistory = loadPredictionFromHistory;

function clearHistory() {
  if (confirm('Voulez-vous vraiment effacer TOUT l\'historique des prédictions ?')) {
    localStorage.removeItem(HISTORY_STORAGE_KEY);
    renderHistory();
  }
}

function renderHistory() {
  const historyCard = document.getElementById('history-section');
  const historyList = document.getElementById('history-list');
  const roiBar = document.getElementById('history-roi-bar');
  if (!historyCard || !historyList) return;

  const history = getHistory();
  if (history.length === 0) {
    historyCard.style.display = 'none';
    return;
  }

  historyCard.style.display = 'block';

  // Calcul du ROI global (Mise fixe = 1 unité par prédiction résolue)
  let totalResolved = 0;
  let totalWon = 0;
  let totalLost = 0;
  let netProfitUnits = 0.0;

  history.forEach(item => {
    if (item.result === 'won') {
      totalResolved++;
      totalWon++;
      const odds = item.pickOdds || 1.85;
      netProfitUnits += (odds - 1.0);
    } else if (item.result === 'lost') {
      totalResolved++;
      totalLost++;
      netProfitUnits -= 1.0;
    }
  });

  if (totalResolved > 0 && roiBar) {
    const winrate = ((totalWon / totalResolved) * 100).toFixed(1);
    const roiPct = ((netProfitUnits / totalResolved) * 100).toFixed(1);
    const profitClass = netProfitUnits > 0 ? 'green' : (netProfitUnits < 0 ? 'red' : 'neutral');
    const profitSign = netProfitUnits > 0 ? '+' : '';

    roiBar.style.display = 'flex';
    roiBar.innerHTML = `
      <div class="roi-stat">
        <span class="roi-stat-label">Paris Résolus</span>
        <span class="roi-stat-value neutral">${totalResolved}/${history.length}</span>
      </div>
      <div class="roi-stat">
        <span class="roi-stat-label">Taux Réussite</span>
        <span class="roi-stat-value ${parseFloat(winrate) >= 55 ? 'green' : 'neutral'}">${winrate}% (${totalWon}V - ${totalLost}D)</span>
      </div>
      <div class="roi-stat">
        <span class="roi-stat-label">Bénéfice Net (1u/pari)</span>
        <span class="roi-stat-value ${profitClass}">${profitSign}${netProfitUnits.toFixed(2)} u</span>
      </div>
      <div class="roi-stat">
        <span class="roi-stat-label">ROI Réel</span>
        <span class="roi-stat-value ${profitClass}">${profitSign}${roiPct}%</span>
      </div>
    `;
  } else if (roiBar) {
    roiBar.style.display = 'none';
  }

  let html = '';
  history.forEach(item => {
    const isWon = item.result === 'won';
    const isLost = item.result === 'lost';
    const statusClass = isWon ? 'hist-won' : (isLost ? 'hist-lost' : '');

    let resultBadge = '';
    if (isWon) {
      const odds = item.pickOdds || 1.85;
      const gain = (odds - 1.0).toFixed(2);
      resultBadge = `<span class="hist-roi-pill pos">+${gain} u (Gagné)</span>`;
    } else if (isLost) {
      resultBadge = `<span class="hist-roi-pill neg">-1.00 u (Perdu)</span>`;
    } else {
      resultBadge = `<span class="hist-result-label pending">En attente</span>`;
    }

    const oddsDisplay = item.pickOdds ? ` @ ${item.pickOdds.toFixed(2)}` : '';

    html += `
      <div class="history-item ${statusClass}" onclick="loadPredictionFromHistory('${item.id}')" title="Cliquer pour réinjecter ce match et ses cotes dans le formulaire et régénérer le rapport">
        <div class="hist-main">
          <div class="hist-matchup">${escapeHtml(item.p1)} <span style="font-weight:400; color:var(--text-dim);">(${item.proba_p1}%)</span> vs ${escapeHtml(item.p2)} <span style="font-weight:400; color:var(--text-dim);">(${item.proba_p2}%)</span></div>
          <div class="hist-meta">🏆 ${escapeHtml(item.tournament)} • ${escapeHtml(item.surface)} • ${item.timestamp}</div>
          <div class="hist-pick">${escapeHtml(item.mainPick)}<b style="color:#34d399;">${oddsDisplay}</b></div>
        </div>
        <div class="hist-actions">
          <div style="display: flex; align-items: center; gap: 6px;">
            <button type="button" class="hist-reload-btn" onclick="event.stopPropagation(); loadPredictionFromHistory('${item.id}')" title="Recharger et réanalyser">🔄 Réanalyser</button>
            ${resultBadge}
          </div>
          <div class="hist-outcome-btns" onclick="event.stopPropagation();">
            <button class="hist-btn win-btn ${isWon ? 'active' : ''}" title="Marquer Gagné" onclick="updateHistoryResult('${item.id}', 'won')">✅</button>
            <button class="hist-btn loss-btn ${isLost ? 'active' : ''}" title="Marquer Perdu" onclick="updateHistoryResult('${item.id}', 'lost')">❌</button>
            <button class="hist-btn del-btn" title="Supprimer ce match de l'historique" onclick="deleteHistoryItem('${item.id}', event)">🗑️</button>
          </div>
        </div>
      </div>
    `;
  });

  historyList.innerHTML = html;
}

function setupHistory() {
  const clearBtn = document.getElementById('btn-clear-history');
  if (clearBtn) {
    clearBtn.addEventListener('click', clearHistory);
  }
  renderHistory();
}

// ============================================================
// PAGE TABS NAVIGATION (SCANNER vs ANALYSE MANUELLE)
// ============================================================
function switchPageTab(tabName) {
  const pageScanner = document.getElementById('page-scanner');
  const pageManual = document.getElementById('page-manual');
  const btnScanner = document.getElementById('tab-btn-scanner');
  const btnManual = document.getElementById('tab-btn-manual');

  if (tabName === 'scanner') {
    if (pageScanner) pageScanner.style.display = 'block';
    if (pageManual) pageManual.style.display = 'none';
    if (btnScanner) btnScanner.classList.add('active');
    if (btnManual) btnManual.classList.remove('active');
  } else {
    if (pageScanner) pageScanner.style.display = 'none';
    if (pageManual) pageManual.style.display = 'block';
    if (btnManual) btnManual.classList.add('active');
    if (btnScanner) btnScanner.classList.remove('active');
  }
}
window.switchPageTab = switchPageTab;

// ============================================================
// DAILY MATCHES SCANNER & BET365 LIVE ODDS (ATP + WTA)
// ============================================================
const ODDS_API_KEY_STORAGE = 'tennis_odds_api_key';
let currentScannerMatches = [];
let currentScannerFilter = 'all'; // 'all' | 'atp' | 'wta' | 'vb'

function setupDailyScanner() {
  const btnFilterAll = document.getElementById('btn-filter-all');
  const btnFilterAtp = document.getElementById('btn-filter-atp');
  const btnFilterWta = document.getElementById('btn-filter-wta');
  const btnFilterVb = document.getElementById('btn-filter-vb');
  const btnRefresh = document.getElementById('btn-refresh-scanner');
  const btnSettings = document.getElementById('btn-scanner-settings');

  const filterBtns = [btnFilterAll, btnFilterAtp, btnFilterWta, btnFilterVb];

  filterBtns.forEach(btn => {
    if (btn) {
      btn.addEventListener('click', () => {
        filterBtns.forEach(b => { if (b) b.classList.remove('active'); });
        btn.classList.add('active');
        currentScannerFilter = btn.dataset.filter || 'all';
        renderScannerGrid();
      });
    }
  });

  if (btnRefresh) {
    btnRefresh.addEventListener('click', () => {
      loadDailyScanner(true);
    });
  }

  if (btnSettings) {
    btnSettings.addEventListener('click', openApiKeyModal);
  }

  // Pre-fill modal input if key already saved
  const savedKey = localStorage.getItem(ODDS_API_KEY_STORAGE);
  const keyInput = document.getElementById('odds-api-key-input');
  if (savedKey && keyInput) {
    keyInput.value = savedKey;
  }

  // Initial load (Combined ATP + WTA)
  loadDailyScanner(false);
}

function openApiKeyModal() {
  const modal = document.getElementById('api-key-modal');
  const keyInput = document.getElementById('odds-api-key-input');
  const savedKey = localStorage.getItem(ODDS_API_KEY_STORAGE);
  if (keyInput && savedKey) keyInput.value = savedKey;
  if (modal) modal.style.display = 'flex';
}
window.openApiKeyModal = openApiKeyModal;

function closeApiKeyModal() {
  const modal = document.getElementById('api-key-modal');
  if (modal) modal.style.display = 'none';
}
window.closeApiKeyModal = closeApiKeyModal;

function saveApiKeyAndRefresh() {
  const keyInput = document.getElementById('odds-api-key-input');
  if (keyInput) {
    const val = keyInput.value.trim();
    if (val) {
      localStorage.setItem(ODDS_API_KEY_STORAGE, val);
    } else {
      localStorage.removeItem(ODDS_API_KEY_STORAGE);
    }
  }
  closeApiKeyModal();
  loadDailyScanner(true);
}
window.saveApiKeyAndRefresh = saveApiKeyAndRefresh;

async function loadDailyScanner(forceRefresh = false) {
  const grid = document.getElementById('scanner-grid');
  const loading = document.getElementById('scanner-loading');
  const banner = document.getElementById('scanner-mode-banner');
  const countAll = document.getElementById('count-all-matches');
  const countAtp = document.getElementById('count-atp-matches');
  const countWta = document.getElementById('count-wta-matches');
  const countVb = document.getElementById('count-vb-matches');
  const tabScannerCount = document.getElementById('tab-scanner-count');
  const timeText = document.getElementById('scanner-time-text');

  if (loading) loading.style.display = 'block';
  if (grid) grid.style.opacity = '0.4';

  const savedKey = localStorage.getItem(ODDS_API_KEY_STORAGE) || '';
  const keyParam = savedKey ? `&api_key=${encodeURIComponent(savedKey)}` : '';
  const refreshParam = forceRefresh ? '&refresh=true' : '';

  try {
    const res = await fetch(`/api/scanner?circuit=all&bookmaker=betclic${keyParam}${refreshParam}`);
    if (!res.ok) {
      throw new Error(`Erreur serveur HTTP ${res.status}`);
    }
    const data = await res.json();

    if (data.success && data.matches) {
      currentScannerMatches = data.matches;

      if (countAll) countAll.textContent = data.total_matches || currentScannerMatches.length;
      if (countAtp) countAtp.textContent = data.atp_count || 0;
      if (countWta) countWta.textContent = data.wta_count || 0;
      if (countVb) countVb.textContent = data.value_bets_count || 0;
      if (tabScannerCount) tabScannerCount.textContent = data.total_matches || currentScannerMatches.length;
      if (timeText) timeText.textContent = `Actualisé ${data.last_update}`;

      // Mode banner
      if (banner) {
        if (data.is_demo_mode) {
          banner.style.display = 'flex';
          banner.style.borderColor = 'rgba(59, 130, 246, 0.3)';
          banner.style.background = 'rgba(59, 130, 246, 0.1)';
          banner.style.color = '#93c5fd';
          banner.innerHTML = `
            <span>💡 <b>Mode Démo actif (Cotes simulées Bet365)</b> • Connectez votre clé The Odds API (gratuite) pour scanner les cotes réelles du jour.</span>
            <button type="button" class="hist-reload-btn" onclick="openApiKeyModal()" style="font-size:11px;">⚙️ Ajouter ma clé gratuite</button>
          `;
        } else {
          const remaining = (data.quota_info && data.quota_info.requests_remaining) ? data.quota_info.requests_remaining : '500';
          banner.style.display = 'flex';
          banner.style.borderColor = 'rgba(16, 185, 129, 0.4)';
          banner.style.background = 'rgba(16, 185, 129, 0.08)';
          banner.style.color = '#34d399';
          banner.innerHTML = `
            <span>🟢 <b>The Odds API connectée (Bet365 Live)</b> • Quota restant : <b>${remaining}/500</b> appels ce mois-ci.</span>
            <button type="button" class="hist-reload-btn" onclick="openApiKeyModal()" style="font-size:11px; background:rgba(255,255,255,0.08); color:#fff; border-color:rgba(255,255,255,0.2);">⚙️ Modifier</button>
          `;
        }
      }

      renderScannerGrid();
    }
  } catch (err) {
    console.error('Erreur scanner cotes', err);
    if (grid) grid.innerHTML = `<div style="grid-column: 1/-1; text-align:center; padding: 25px; color: #f87171;">⚠️ Connexion au serveur en cours... Cliquez sur <b>🔄 Actualiser</b> dans quelques secondes.</div>`;
  } finally {
    if (loading) loading.style.display = 'none';
    if (grid) grid.style.opacity = '1';
  }
}

function renderScannerGrid() {
  const grid = document.getElementById('scanner-grid');
  if (!grid) return;

  let matchesToShow = currentScannerMatches;
  if (currentScannerFilter === 'vb') {
    matchesToShow = currentScannerMatches.filter(m => m.has_value_bet);
  } else if (currentScannerFilter === 'atp') {
    matchesToShow = currentScannerMatches.filter(m => m.circuit === 'atp');
  } else if (currentScannerFilter === 'wta') {
    matchesToShow = currentScannerMatches.filter(m => m.circuit === 'wta');
  }

  if (matchesToShow.length === 0) {
    if (currentScannerFilter === 'vb') {
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align:center; padding: 30px; color: var(--text-dim);">🎯 Aucun Value Bet détecté sur les cotes actuelles avec les marges de sécurité requises.</div>`;
    } else {
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align:center; padding: 30px; color: var(--text-dim);">Aucun match programmé dans cette catégorie pour le moment.</div>`;
    }
    return;
  }

  let html = '';
  matchesToShow.forEach(m => {
    const hasVb = m.has_value_bet;
    const topVb = m.top_value_bet;
    const isAtp = m.circuit === 'atp';

    const proba1 = m.prediction ? (m.prediction.proba_p1 * 100).toFixed(0) : '-';
    const proba2 = m.prediction ? (m.prediction.proba_p2 * 100).toFixed(0) : '-';
    const isP1Fav = m.prediction ? (m.prediction.proba_p1 >= m.prediction.proba_p2) : false;

    let vbHtml = '';
    if (hasVb && topVb) {
      const confScore = (topVb.confidence && topVb.confidence.score !== undefined) ? `${topVb.confidence.score}%` : '80%';
      const evVal = (topVb.ev_pct !== undefined) ? topVb.ev_pct : ((topVb.ev_percent !== undefined) ? topVb.ev_percent : 0);
      const edgeVal = (topVb.edge_pct !== undefined) ? topVb.edge_pct : ((topVb.edge_percent !== undefined) ? topVb.edge_percent : 0);

      vbHtml = `
        <div class="scan-vb-banner">
          <div class="scan-vb-title">
            <span>🎯 ${escapeHtml(topVb.selection)} @ ${topVb.offered_odds.toFixed(2)}</span>
            <span style="color:#fbbf24; font-size:11px; font-weight:800;">+${evVal}% EV</span>
          </div>
          <div class="scan-vb-meta">
            <span>🔥 Confiance : ${confScore}</span>
            <span>• Edge : +${edgeVal}%</span>
          </div>
        </div>
      `;
    } else {
      vbHtml = `
        <div class="scan-no-vb-msg">
          ⚖️ Cotes équilibrées (Marge Bet365)
        </div>
      `;
    }

    const odds1Str = m.odds1 ? m.odds1.toFixed(2) : '-';
    const odds2Str = m.odds2 ? m.odds2.toFixed(2) : '-';
    const circuitBadge = isAtp ? `<span class="circuit-pill-atp">🏆 ATP</span>` : `<span class="circuit-pill-wta">👑 WTA</span>`;

    html += `
      <div class="scan-match-card ${hasVb ? 'has-vb' : ''}" onclick="openMatchDetailModal('${m.id}')" style="cursor: pointer;" title="Cliquer pour ouvrir le rapport d'analyse détaillé en grand">
        <div class="scan-card-top">
          <div style="display:flex; align-items:center; gap:6px;">
            ${circuitBadge}
            <span class="scan-tourney-tag">🏆 ${escapeHtml(m.tournament)} • ${escapeHtml(m.surface)}</span>
          </div>
          <span class="scan-time-tag">⏰ ${escapeHtml(m.time_display)}</span>
        </div>

        <div class="scan-players-wrap">
          <div class="scan-player-row">
            <div class="scan-player-name">
              ${escapeHtml(m.p1)}
              <span class="scan-proba-pill ${isP1Fav ? 'fav' : ''}">${proba1}%</span>
            </div>
            <div class="scan-odds-pill">@ ${odds1Str}</div>
          </div>

          <div class="scan-player-row">
            <div class="scan-player-name">
              ${escapeHtml(m.p2)}
              <span class="scan-proba-pill ${!isP1Fav ? 'fav' : ''}">${proba2}%</span>
            </div>
            <div class="scan-odds-pill">@ ${odds2Str}</div>
          </div>
        </div>

        ${vbHtml}

        <div class="scan-btn-load" style="pointer-events: none; text-align: center; margin-top: 4px;">
          🔍 Voir le rapport complet en grand
        </div>
      </div>
    `;
  });

  grid.innerHTML = html;
}

// ============================================================
// POP-UP MODAL : RAPPORT DE MATCH DÉTAILLÉ EN GRAND
// ============================================================
function openMatchDetailModal(matchId) {
  try {
    const match = currentScannerMatches.find(m => String(m.id) === String(matchId));
    const modal = document.getElementById('match-detail-modal');
    const metaContainer = document.getElementById('modal-match-meta');
    const bodyContainer = document.getElementById('modal-match-body');

    if (!match || !modal || !bodyContainer) {
      console.warn('Match ou éléments modaux introuvables pour ID:', matchId);
      return;
    }

    const isAtp = match.circuit === 'atp';
    const circuitBadge = isAtp ? `<span class="circuit-pill-atp">🏆 ATP Hommes</span>` : `<span class="circuit-pill-wta">👑 WTA Femmes</span>`;
    const bookmakerName = match.bookmaker || 'Bet365';

    if (metaContainer) {
      metaContainer.innerHTML = `
        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
          ${circuitBadge}
          <span style="font-weight: 900; color: #ffffff; font-size: 16px;">🏆 ${escapeHtml(match.tournament)}</span>
          <span style="color: #94a3b8;">• Surface : <b style="color: #f1f5f9;">${escapeHtml(match.surface)}</b></span>
          <span style="color: #94a3b8;">• Format : <b style="color: #f1f5f9;">Best-of ${match.best_of}</b></span>
          <span style="color: #94a3b8;">• Heure : <b style="color:#60a5fa;">⏰ ${escapeHtml(match.time_display)}</b></span>
          <span class="bm-badge" style="background: rgba(16, 185, 129, 0.18); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35);">Cotes en direct (${escapeHtml(bookmakerName)})</span>
        </div>
      `;
    }

    const p1 = match.p1;
    const p2 = match.p2;
    const p1Short = p1.split(' ').pop();
    const p2Short = p2.split(' ').pop();

    const rep = match.full_report || {};
    const pred = match.prediction || { proba_p1: 0.50, proba_p2: 0.50, fair_odds_p1: 2.0, fair_odds_p2: 2.0, match_confidence: 75, confidence_level: 'Moyenne' };
    const proba1 = (pred.proba_p1 * 100).toFixed(1);
    const proba2 = (pred.proba_p2 * 100).toFixed(1);
    const isP1Fav = pred.proba_p1 >= pred.proba_p2;

    const mkts = rep.markets || {};
    const winnerM = mkts.winner || {};
    const set1M = mkts.set1_winner || {};
    const totalM = mkts.total_games || {};
    const hcapM = mkts.handicap_games || {};
    const tbM = mkts.tiebreak || {};
    const setsCountM = mkts.sets_count || {};
    const setScores = rep.set_scores || {};
    const stats = rep.stats || {};

    const confScore = (rep.confidence && rep.confidence.score !== undefined) ? rep.confidence.score : (pred.match_confidence || 75);
    const confLevel = (rep.confidence && rep.confidence.level) ? rep.confidence.level : (pred.confidence_level || 'Moyenne');
    const confBadgeClass = confScore >= 78 ? 'high' : (confScore >= 65 ? 'medium' : 'low');

    // Value bets cards
    let vbsHtml = '';
    if (match.all_value_bets && match.all_value_bets.length > 0) {
      let cardsHtml = '';
      match.all_value_bets.forEach((vb, idx) => {
        const conf = vb.confidence || { score: 85, label: 'Très haute', level: 'high', icon: '🔥' };
        const kellyQuarter = vb.kelly_quarter_pct || vb.kelly_pct || 1.5;
        const bEl = document.getElementById('bankroll-input');
        const bankrollTotal = (bEl && parseFloat(bEl.value)) ? parseFloat(bEl.value) : 1000.0;
        const betAmountEuro = ((bankrollTotal * kellyQuarter) / 100).toFixed(0);
        const estGainEuro = (parseFloat(betAmountEuro) * (vb.offered_odds - 1.0)).toFixed(0);
        const evVal = (vb.ev_pct !== undefined) ? vb.ev_pct : ((vb.ev_percent !== undefined) ? vb.ev_percent : 0);
        const edgeVal = (vb.edge_pct !== undefined) ? vb.edge_pct : ((vb.edge_percent !== undefined) ? vb.edge_percent : 0);

        cardsHtml += `
          <div class="vb-card primary-vb" style="background: linear-gradient(135deg, rgba(16, 185, 129, 0.12) 0%, rgba(15, 23, 42, 0.9) 100%); border: 1px solid rgba(16, 185, 129, 0.45); border-radius: var(--radius-md); padding: 14px; margin-bottom: 12px;">
            <div class="vb-card-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
              <span class="vb-badge" style="background: #10b981; color: #064e3b; font-weight: 900; font-size: 11px; padding: 3px 8px; border-radius: 4px;">⭐ VALUE BET RECOMMANDÉ #${idx + 1}</span>
              <span class="vb-conf-pill ${conf.level}" style="font-size: 11.5px; font-weight: 700; color: #fbbf24;">${conf.icon} Confiance : ${conf.score}% (${conf.label})</span>
            </div>
            <div class="vb-selection-title" style="font-size: 16px; font-weight: 900; color: #ffffff; margin-bottom: 10px;">
              🎯 ${escapeHtml(vb.selection)}
            </div>
            <div class="vb-details-grid" style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 8px;">
              <div class="vb-stat-box" style="background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; text-align: center;">
                <span class="vb-stat-label" style="font-size: 10.5px; color: #94a3b8; display: block;">Cote Bookmaker</span>
                <span class="vb-stat-val odd" style="font-size: 15px; font-weight: 900; color: #38bdf8;">@ ${vb.offered_odds.toFixed(2)}</span>
              </div>
              <div class="vb-stat-box" style="background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; text-align: center;">
                <span class="vb-stat-label" style="font-size: 10.5px; color: #94a3b8; display: block;">Cote Équitable IA</span>
                <span class="vb-stat-val" style="font-size: 15px; font-weight: 800; color: #f1f5f9;">@ ${vb.fair_odds.toFixed(2)}</span>
              </div>
              <div class="vb-stat-box" style="background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; text-align: center;">
                <span class="vb-stat-label" style="font-size: 10.5px; color: #94a3b8; display: block;">Espérance (EV)</span>
                <span class="vb-stat-val ev" style="font-size: 15px; font-weight: 900; color: #34d399;">+${evVal}%</span>
              </div>
              <div class="vb-stat-box" style="background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; text-align: center;">
                <span class="vb-stat-label" style="font-size: 10.5px; color: #94a3b8; display: block;">Mise Conseillée</span>
                <span class="vb-stat-val kelly" style="font-size: 15px; font-weight: 900; color: #fbbf24;">${kellyQuarter}% (${betAmountEuro} €)</span>
              </div>
            </div>
            <div style="display:flex; justify-content:space-between; align-items:center; font-size: 11.5px; color: #94a3b8; padding-top: 4px; border-top: 1px solid rgba(255,255,255,0.06);">
              <span>Edge net sur le marché : <b style="color:#34d399;">+${edgeVal}%</b></span>
              <span style="color:#fbbf24; font-weight:700;">💰 Gain net estimé : +${estGainEuro} €</span>
            </div>
          </div>
        `;
      });
      vbsHtml = `
        <div style="margin-bottom: 20px;">
          <h4 style="font-size: 14px; font-weight: 800; color: #34d399; margin-bottom: 10px; display:flex; align-items:center; gap:6px;">
            <span>🎯</span> Opportunités de Value Bets Détectées (${escapeHtml(bookmakerName)})
          </h4>
          ${cardsHtml}
        </div>
      `;
    } else {
      vbsHtml = `
        <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 14px; margin-bottom: 20px; text-align: center; color: var(--text-muted); font-size: 13px;">
          ⚖️ <b>Aucun Value Bet rentable détecté sur ce match</b> : Les cotes proposées par ${escapeHtml(bookmakerName)} intègrent une marge bookmaker normale sans avantage statistique suffisant.
        </div>
      `;
    }

    // Odds string fallbacks
    const odds1Str = match.odds1 ? match.odds1.toFixed(2) : '-';
    const odds2Str = match.odds2 ? match.odds2.toFixed(2) : '-';
    const totLine = match.total_line || (totalM.line || 22.5);
    const oddsOvStr = match.odds_over ? match.odds_over.toFixed(2) : '-';
    const oddsUnStr = match.odds_under ? match.odds_under.toFixed(2) : '-';
    const hLine = match.handicap_line || (hcapM.line || 3.5);
    const oddsH1Str = match.odds_h1 ? match.odds_h1.toFixed(2) : '-';
    const oddsH2Str = match.odds_h2 ? match.odds_h2.toFixed(2) : '-';

    // Markov computed fair odds
    const fairSet1P1 = set1M.fair_odds_p1 ? `@ ${set1M.fair_odds_p1.toFixed(2)}` : `@ ${(1.0 / (pred.proba_p1 || 0.5)).toFixed(2)}`;
    const fairSet1P2 = set1M.fair_odds_p2 ? `@ ${set1M.fair_odds_p2.toFixed(2)}` : `@ ${(1.0 / (pred.proba_p2 || 0.5)).toFixed(2)}`;
    const fairSets2 = setsCountM.fair_odds_2sets ? `@ ${setsCountM.fair_odds_2sets.toFixed(2)}` : '@ 1.65';
    const fairSets3 = setsCountM.fair_odds_3sets ? `@ ${setsCountM.fair_odds_3sets.toFixed(2)}` : '@ 2.45';
    const probaTbYes = tbM.proba_yes !== undefined ? `${tbM.proba_yes}%` : '38%';
    const fairTbYes = tbM.fair_odds_yes ? `@ ${tbM.fair_odds_yes.toFixed(2)}` : '@ 2.60';
    const fairTbNo = tbM.fair_odds_no ? `@ ${tbM.fair_odds_no.toFixed(2)}` : '@ 1.62';

    // Ranks / Elos
    const r1 = stats.rank_p1 ? `#${stats.rank_p1}` : '-';
    const r2 = stats.rank_p2 ? `#${stats.rank_p2}` : '-';
    const det = rep.detailed_analytics || {};
    const enClair = det.summary_en_clair || '';
    const p1Det = det.p1 || {};
    const p2Det = det.p2 || {};
    const h2hDet = det.h2h || {};
    const compMetrics = det.comparative_metrics || [];

    const enClairHtml = enClair ? `
      <div class="en-clair-box">
        <div class="en-clair-tag">💡 EN CLAIR</div>
        <div class="en-clair-text">${enClair}</div>
      </div>
    ` : '';

    let compRowsHtml = '';
    if (compMetrics && compMetrics.length > 0) {
      compMetrics.forEach(cm => {
        const v1 = parseFloat(cm.val1) || 0;
        const v2 = parseFloat(cm.val2) || 0;
        const tot = (v1 + v2) || 1;
        const pct1 = Math.max(15, Math.min(85, (v1 / tot) * 100));
        const pct2 = 100 - pct1;
        compRowsHtml += `
          <div class="comp-meter-row">
            <div class="comp-val-left">${escapeHtml(cm.val1_display || '')}</div>
            <div class="comp-meter-center">
              <div class="comp-meter-label">${escapeHtml(cm.label)}</div>
              <div class="comp-dual-track">
                <div class="comp-fill-p1" style="width: ${pct1}%;"></div>
                <div class="comp-fill-p2" style="width: ${pct2}%;"></div>
              </div>
            </div>
            <div class="comp-val-right">${escapeHtml(cm.val2_display || '')}</div>
          </div>
        `;
      });
    }

    const p1StreakHtml = (p1Det.streak_badges || []).map(b => `<span class="wl-pill ${b.toLowerCase()}">${b}</span>`).join('');
    const p2StreakHtml = (p2Det.streak_badges || []).map(b => `<span class="wl-pill ${b.toLowerCase()}">${b}</span>`).join('');

    const p1MatchesHtml = (p1Det.recent_matches || []).slice(0, 5).map(m => `
      <div class="match-history-row">
        <div class="match-history-opp">
          <span class="wl-pill ${m.is_win ? 'w' : 'l'}" style="width:16px;height:16px;font-size:9px;">${m.is_win ? 'W' : 'L'}</span>
          <span>vs ${escapeHtml(m.opponent)}</span>
        </div>
        <div class="match-history-score">${escapeHtml(m.score)}</div>
        <div class="match-history-meta">
          <span class="surf-tag">${escapeHtml(m.surface)}</span>
          <span>${escapeHtml(m.date)}</span>
        </div>
      </div>
    `).join('');

    const p2MatchesHtml = (p2Det.recent_matches || []).slice(0, 5).map(m => `
      <div class="match-history-row">
        <div class="match-history-opp">
          <span class="wl-pill ${m.is_win ? 'w' : 'l'}" style="width:16px;height:16px;font-size:9px;">${m.is_win ? 'W' : 'L'}</span>
          <span>vs ${escapeHtml(m.opponent)}</span>
        </div>
        <div class="match-history-score">${escapeHtml(m.score)}</div>
        <div class="match-history-meta">
          <span class="surf-tag">${escapeHtml(m.surface)}</span>
          <span>${escapeHtml(m.date)}</span>
        </div>
      </div>
    `).join('');

    bodyContainer.innerHTML = `
      <!-- 1. FACE-A-FACE ARENA -->
      <div class="modal-duel-arena">
        <div class="modal-duel-players">
          <div class="modal-player-card ${isP1Fav ? 'winner' : ''}">
            <div class="modal-player-header">
              <span class="modal-player-name">${escapeHtml(p1)}</span>
              <span class="modal-player-pct">${proba1}%</span>
            </div>
            <div class="modal-player-sub">
              <span>Rang : <b>${r1}</b></span>
              <span>• Elo : <b>${elo1}</b></span>
              <span>• Hold Service : <b>${hold1}</b></span>
            </div>
            <div style="font-size:12px; margin-top:4px;">
              Cote Betclic : <b style="color:#38bdf8;">@ ${odds1Str}</b>
              <span style="color:#94a3b8; font-size:11px;">(Fair IA: @ ${pred.fair_odds_p1})</span>
            </div>
          </div>

          <div class="modal-vs-center">VS</div>

          <div class="modal-player-card ${!isP1Fav ? 'winner' : ''}" style="text-align: right;">
            <div class="modal-player-header" style="flex-direction: row-reverse;">
              <span class="modal-player-name">${escapeHtml(p2)}</span>
              <span class="modal-player-pct">${proba2}%</span>
            </div>
            <div class="modal-player-sub" style="justify-content: flex-end;">
              <span>Hold Service : <b>${hold2}</b></span>
              <span>• Elo : <b>${elo2}</b></span>
              <span>• Rang : <b>${r2}</b></span>
            </div>
            <div style="font-size:12px; margin-top:4px;">
              <span style="color:#94a3b8; font-size:11px;">(Fair IA: @ ${pred.fair_odds_p2})</span>
              Cote Betclic : <b style="color:#38bdf8;">@ ${odds2Str}</b>
            </div>
          </div>
        </div>

        <!-- Split Progress Bar -->
        <div class="modal-split-track">
          <div class="modal-split-fill-p1" style="width: ${proba1}%;"></div>
          <div class="modal-split-fill-p2" style="width: ${proba2}%;"></div>
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; font-size:11.5px; color:#94a3b8;">
          <span>🔥 Modèle ML Calibré : <b>XGBoost + Température (T=0.865)</b></span>
          <span style="font-weight:700; color:#fbbf24;">Indice de confiance du match : ${confScore}% (${confLevel})</span>
        </div>
      </div>

      <!-- 2. BANDEAU EN CLAIR -->
      ${enClairHtml}

      <!-- 3. VALUE BETS RECOMMENDATIONS -->
      ${vbsHtml}

      <!-- 4. COMPARATIF DES FACTEURS CLES -->
      <div class="modal-section-box">
        <div class="modal-section-title">
          <span>📊 Comparatif des Facteurs Clés du Match</span>
          <span style="font-size: 11px; color: #94a3b8; font-weight: normal;">Bilans &amp; Statistiques calculés par le modèle</span>
        </div>
        ${compRowsHtml}
      </div>

      <!-- 5. FORME RECENTE & DERNIERS MATCHS -->
      <div class="modal-section-box">
        <div class="modal-section-title">
          <span>📈 Forme Récente &amp; 5 Derniers Matchs Joués</span>
        </div>
        <div class="recent-duo-grid">
          <!-- P1 -->
          <div class="player-recent-card">
            <div class="player-recent-header">
              <span class="player-recent-name">${escapeHtml(p1)}</span>
              <span class="player-form-badge">⚡ ${p1Det.form_pct || 60}% Forme</span>
            </div>
            <div class="streak-badges-wrap">
              ${p1StreakHtml}
            </div>
            <div class="match-history-list">
              ${p1MatchesHtml}
            </div>
          </div>

          <!-- P2 -->
          <div class="player-recent-card">
            <div class="player-recent-header">
              <span class="player-recent-name">${escapeHtml(p2)}</span>
              <span class="player-form-badge">⚡ ${p2Det.form_pct || 60}% Forme</span>
            </div>
            <div class="streak-badges-wrap">
              ${p2StreakHtml}
            </div>
            <div class="match-history-list">
              ${p2MatchesHtml}
            </div>
          </div>
        </div>
      </div>

      <!-- 6. HEAD-TO-HEAD GLOBAL -->
      <div class="modal-section-box">
        <div class="modal-section-title">
          <span>🤝 Head-to-Head Global</span>
          <span style="font-size:11px; color:#34d399; font-weight:700;">${(h2hDet.total_matches > 0) ? `${h2hDet.p1_wins} - ${h2hDet.p2_wins}` : '0 - 0'}</span>
        </div>
        <div class="h2h-kpi-grid">
          <div class="h2h-kpi-box">
            <div class="h2h-kpi-num">${h2hDet.p1_wins || 0} - ${h2hDet.p2_wins || 0}</div>
            <div class="h2h-kpi-lbl">Score Total</div>
          </div>
          <div class="h2h-kpi-box">
            <div class="h2h-kpi-num">${h2hDet.total_matches || 0}</div>
            <div class="h2h-kpi-lbl">Matchs Joués</div>
          </div>
          <div class="h2h-kpi-box">
            <div class="h2h-kpi-num">${h2hDet.surface_p1_wins || 0} - ${h2hDet.surface_p2_wins || 0}</div>
            <div class="h2h-kpi-lbl">Sur ${escapeHtml(h2hDet.surface_name || match.surface || 'cette surface')}</div>
          </div>
          <div class="h2h-kpi-box">
            <div class="h2h-kpi-num">${h2hDet.p1_wins || 0} - ${h2hDet.p2_wins || 0}</div>
            <div class="h2h-kpi-lbl">3 derniers duels</div>
          </div>
        </div>
      </div>

      <!-- 7. MARCHES & COTES EQUITABLES MARKOV -->
      <div class="modal-section-box">
        <div class="modal-section-title">
          <span>📋 Cotes Réelles Betclic vs Cotes Équitables IA</span>
        </div>

        <div class="modal-markets-grid">
          <!-- Marché 1 : Vainqueur -->
          <div class="modal-market-cell">
            <div class="modal-market-name">🏆 Vainqueur du Match (1 / 2)</div>
            <div class="modal-market-row">
              <span>${escapeHtml(p1Short)}</span>
              <div>
                <span class="modal-bookie-badge">Betclic: @ ${odds1Str}</span>
                <span class="modal-fair-badge">Fair: @ ${pred.fair_odds_p1}</span>
              </div>
            </div>
            <div class="modal-market-row">
              <span>${escapeHtml(p2Short)}</span>
              <div>
                <span class="modal-bookie-badge">Betclic: @ ${odds2Str}</span>
                <span class="modal-fair-badge">Fair: @ ${pred.fair_odds_p2}</span>
              </div>
            </div>
          </div>

          <!-- Marché 2 : Set 1 -->
          <div class="modal-market-cell">
            <div class="modal-market-name">🥇 Vainqueur du 1er Set</div>
            <div class="modal-market-row">
              <span>${escapeHtml(p1Short)}</span>
              <span class="modal-fair-badge">Fair IA: ${fairSet1P1}</span>
            </div>
            <div class="modal-market-row">
              <span>${escapeHtml(p2Short)}</span>
              <span class="modal-fair-badge">Fair IA: ${fairSet1P2}</span>
            </div>
          </div>

          <!-- Marché 3 : Nombre de Sets -->
          <div class="modal-market-cell">
            <div class="modal-market-name">🎾 Nombre de Sets</div>
            <div class="modal-market-row">
              <span>2 Sets (2-0 sec)</span>
              <span class="modal-fair-badge">Fair IA: ${fairSets2}</span>
            </div>
            <div class="modal-market-row">
              <span>3 Sets (2-1 disputé)</span>
              <span class="modal-fair-badge">Fair IA: ${fairSets3}</span>
            </div>
          </div>

          <!-- Marché 4 : Tie-Break -->
          <div class="modal-market-cell">
            <div class="modal-market-name">⚡ Tie-Break dans le match (Proba: ${probaTbYes})</div>
            <div class="modal-market-row">
              <span>Au moins 1 TB (OUI)</span>
              <span class="modal-fair-badge">Fair IA: ${fairTbYes}</span>
            </div>
            <div class="modal-market-row">
              <span>Aucun TB (NON)</span>
              <span class="modal-fair-badge">Fair IA: ${fairTbNo}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 8. MODAL FOOTER ACTIONS -->
      <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--border);">
        <button type="button" class="btn-cancel" onclick="closeMatchDetailModal()" style="font-size: 13px; padding: 8px 18px;">
          ✕ Fermer
        </button>
        <button type="button" class="btn-save-key" onclick="transferMatchToManual('${match.id}')" style="background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%); font-size: 13.5px; padding: 8px 22px; font-weight: 800; display:flex; align-items:center; gap:6px;">
          <span>⚔️</span> Personnaliser mes cotes dans l'Analyseur Manuel
        </button>
      </div>
    `;

    modal.style.display = 'flex';
  } catch (err) {
    console.error('Erreur lors de l ouverture de la modale de match:', err);
    transferMatchToManual(matchId);
  }
}
window.openMatchDetailModal = openMatchDetailModal;
window.openMatchDetailModal = openMatchDetailModal;

function closeMatchDetailModal() {
  const modal = document.getElementById('match-detail-modal');
  if (modal) modal.style.display = 'none';
}
window.closeMatchDetailModal = closeMatchDetailModal;

function transferMatchToManual(matchId) {
  closeMatchDetailModal();
  switchPageTab('manual');
  loadMatchFromScanner(matchId);
}
window.transferMatchToManual = transferMatchToManual;

function loadMatchFromScanner(matchId) {
  const match = currentScannerMatches.find(m => String(m.id) === String(matchId));
  if (!match) return;

  // 1. Switch circuit if needed
  const targetCircuit = (match.circuit || 'atp').toLowerCase();
  circuitBtns.forEach(b => {
    if (b.dataset.circuit === targetCircuit) {
      b.classList.add('active');
    } else {
      b.classList.remove('active');
    }
  });
  currentCircuit = targetCircuit;

  // 2. Players
  p1Input.value = match.p1 || match.p1_raw || '';
  p2Input.value = match.p2 || match.p2_raw || '';
  selectedP1 = match.p1 || match.p1_raw || '';
  selectedP2 = match.p2 || match.p2_raw || '';

  // 3. Tournament & Surface Options
  if (tournamentInput) tournamentInput.value = (match.tournament && match.tournament !== 'Tournoi') ? match.tournament : '';
  if (surfaceSelect && match.surface) surfaceSelect.value = match.surface;
  if (levelSelect && match.level) levelSelect.value = match.level;
  if (bestOfSelect && match.best_of) bestOfSelect.value = String(match.best_of);
  if (indoorSelect && match.indoor !== undefined) indoorSelect.value = String(match.indoor);

  // 4. Main Odds
  if (odds1Input) odds1Input.value = (match.odds1 !== null && match.odds1 !== undefined) ? match.odds1 : '';
  if (odds2Input) odds2Input.value = (match.odds2 !== null && match.odds2 !== undefined) ? match.odds2 : '';

  // 5. Secondary Markets (Totals, Handicap)
  const hasSec = Boolean(match.total_line || match.odds_over || match.odds_under || match.handicap_line || match.odds_h1 || match.odds_h2);

  if (totalLineInput) totalLineInput.value = match.total_line || '';
  if (oddsOverInput) oddsOverInput.value = match.odds_over || '';
  if (oddsUnderInput) oddsUnderInput.value = match.odds_under || '';

  if (handicapLineInput) handicapLineInput.value = match.handicap_line || '';
  if (oddsH1Input) oddsH1Input.value = match.odds_h1 || '';
  if (oddsH2Input) oddsH2Input.value = match.odds_h2 || '';

  if (hasSec && secMarketsContent) {
    secMarketsContent.style.display = 'flex';
    if (secToggleIcon) secToggleIcon.classList.add('open');
  }

  updateDynamicLabels();

  // Scroll to results / main card
  const mainCard = document.querySelector('main.card');
  if (mainCard) {
    mainCard.scrollIntoView({ behavior: 'smooth' });
  }

  // Trigger analysis immediately
  if (predictBtn) {
    predictBtn.click();
  }
}
window.loadMatchFromScanner = loadMatchFromScanner;



