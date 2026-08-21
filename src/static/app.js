// ==========================================================================
// TENNIS PREDICTOR AI - CLIENT JAVASCRIPT LOGIC
// ==========================================================================

let currentCircuit = 'atp';
let selectedP1 = '';
let selectedP2 = '';

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
const surfaceSelect = document.getElementById('surface-select');
const tournamentInput = document.getElementById('tournament-input');
const levelSelect = document.getElementById('level-select');
const roundSelect = document.getElementById('round-select');
const bestOfSelect = document.getElementById('best-of-select');
const indoorSelect = document.getElementById('indoor-select');
const odds1Input = document.getElementById('odds1-input');
const odds2Input = document.getElementById('odds2-input');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  setupCircuitSwitcher();
  setupAutocomplete(p1Input, p1Dropdown, (name) => { selectedP1 = name; });
  setupAutocomplete(p2Input, p2Dropdown, (name) => { selectedP2 = name; });
  setupSwap();
  setupFormSubmit();
});

// Circuit Switching (ATP / WTA)
function setupCircuitSwitcher() {
  circuitBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      circuitBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentCircuit = btn.dataset.circuit;
      
      // Clear inputs on circuit switch
      p1Input.value = '';
      p2Input.value = '';
      selectedP1 = '';
      selectedP2 = '';
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

// Autocomplete Setup
function setupAutocomplete(input, dropdown, onSelect) {
  let debounceTimer = null;

  input.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const q = input.value.trim();
    if (q.length < 1) {
      dropdown.style.display = 'none';
      return;
    }

    debounceTimer = setTimeout(async () => {
      try {
        const res = await fetch(`/api/players?circuit=${currentCircuit}&q=${encodeURIComponent(q)}&limit=8`);
        const players = await res.json();
        renderDropdown(dropdown, players, input, onSelect);
      } catch (err) {
        console.error('Error fetching players:', err);
      }
    }, 250);
  });

  // Close dropdown on click outside
  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.style.display = 'none';
    }
  });
}

function renderDropdown(dropdown, players, input, onSelect) {
  dropdown.innerHTML = '';
  if (!players || players.length === 0) {
    dropdown.style.display = 'none';
    return;
  }

  players.forEach(p => {
    const item = document.createElement('div');
    item.className = 'autocomplete-item';
    
    const rankStr = p.rank ? `#${p.rank}` : '';
    item.innerHTML = `
      <span class="ac-name">${p.name}</span>
      <div class="ac-meta">
        ${rankStr ? `<span class="ac-rank">${rankStr}</span>` : ''}
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

// Form Submission & Prediction
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
    predictBtn.innerHTML = '⏳ Calcul des prédictions XGBoost...';

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
      predictBtn.innerHTML = '🔮 Prédire le Match';
    }
  });
}

// Render Results
function renderResults(data) {
  resultsSection.style.display = 'block';

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
  const cpiStr = ctx.cpi ? ` • Ace Rate: ${ctx.cpi}%` : '';
  const altStr = ctx.altitude > 0 ? ` • Alt: ${ctx.altitude}m` : '';
  ctxTag.textContent = `${ctx.tournament} • ${ctx.surface}${cpiStr}${altStr} • ${ctx.round} • Best-of ${ctx.best_of}`;

  // Value Bet Box
  const vbContainer = document.getElementById('valuebet-container');
  const vb = data.value_bet;

  if (vb && vb.has_odds) {
    vbContainer.style.display = 'block';
    let cardClass = 'no-vb';
    let badgeClass = 'badge-no-vb';
    let badgeText = '❌ AUCUN VALUE BET';
    let descText = 'Les cotes proposées sont trop basses par rapport aux probabilités réelles du modèle.';

    if (vb.is_value_bet) {
      cardClass = 'is-vb';
      badgeClass = 'badge-vb';
      badgeText = `🎯 VALUE BET CONFIRMÉ SUR ${vb.recommended_player.toUpperCase()}`;
      descText = `Mise recommandée : ${vb.kelly_pct}% de la bankroll (Quarter-Kelly) pour maximiser la croissance du capital à long terme.`;
    } else if (vb.decision_badge === 'LOW_EV') {
      cardClass = 'is-low-ev';
      badgeClass = 'badge-low-ev';
      badgeText = `⚠️ EV TROP FAIBLE (+${vb.ev_pct}%)`;
      descText = `Bien qu'il y ait un avantage sur le marché, la marge du bookmaker absorbe le gain net. Cote requise : ${vb.min_odds_required}+`;
    }

    vbContainer.className = `valuebet-card ${cardClass}`;
    vbContainer.innerHTML = `
      <div class="vb-header">
        <span class="vb-badge ${badgeClass}">${badgeText}</span>
      </div>
      <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 8px;">${descText}</p>
      <div class="vb-grid">
        <div class="vb-metric">
          <div class="vbm-label">Espérance de Gain (EV)</div>
          <div class="vbm-val ${vb.ev_pct >= 3 ? 'green' : (vb.ev_pct > 0 ? 'amber' : '')}">${vb.ev_pct > 0 ? '+' : ''}${vb.ev_pct}%</div>
        </div>
        <div class="vb-metric">
          <div class="vbm-label">Avantage Marché (Edge)</div>
          <div class="vbm-val ${vb.edge_pct >= 3 ? 'green' : (vb.edge_pct > 0 ? 'amber' : '')}">${vb.edge_pct > 0 ? '+' : ''}${vb.edge_pct}%</div>
        </div>
        <div class="vb-metric">
          <div class="vbm-label">Cote Offerte vs Min</div>
          <div class="vbm-val">${vb.offered_odds ? vb.offered_odds.toFixed(2) : '-'} <span style="font-size:12px; color:var(--text-dim);">/ ${vb.min_odds_required ? vb.min_odds_required.toFixed(2) : '-'}</span></div>
        </div>
        <div class="vb-metric">
          <div class="vbm-label">Mise Kelly Conseillée</div>
          <div class="vbm-val green">${vb.kelly_pct}%</div>
        </div>
      </div>
    `;
  } else {
    vbContainer.style.display = 'none';
  }

  // Detailed Stats Table
  const statsBody = document.getElementById('stats-table-body');
  statsBody.innerHTML = `
    <tr><td>Elo Global (avec Decay)</td><td>${data.elo.global_p1}</td><td>${data.elo.global_p2}</td></tr>
    <tr><td>Elo ${ctx.surface}</td><td>${data.elo.surface_p1}</td><td>${data.elo.surface_p2}</td></tr>
    <tr><td>Serve Elo</td><td>${data.elo.serve_p1}</td><td>${data.elo.serve_p2}</td></tr>
    <tr><td>Return Elo</td><td>${data.elo.return_p1}</td><td>${data.elo.return_p2}</td></tr>
    <tr><td>H2H Historique</td><td>${data.h2h.wins_p1}</td><td>${data.h2h.wins_p2}</td></tr>
    <tr><td>P(Hold Service - Markov)</td><td>${data.markov.hold_proba_p1}%</td><td>${data.markov.hold_proba_p2}%</td></tr>
    <tr><td>P(Point Service - Markov)</td><td>${data.markov.serve_point_p1}%</td><td>${data.markov.serve_point_p2}%</td></tr>
    <tr><td>Total Jeux Prévu</td><td colspan="2" style="text-align:center; color:#f59e0b;">${data.markov.expected_total_games} jeux</td></tr>
  `;
}
