import re

def clean_file(path, replacements):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for pattern, repl in replacements:
        content = re.sub(pattern, repl, content, flags=re.DOTALL)
        
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

# Nettoyage de 02_feature_engineering.py
reps_02 = [
    # Suppression dans 'out' dict pre-allocation
    (r'"elo_win_prob",\s*', ''),
    (r'"rank_diff",\s*', ''),
    (r'"form5_diff",\s*"form10_diff",\s*"form20_diff",\s*"form180d_diff",\s*"form90d_diff",\s*"form365d_diff",', '"form10_diff", "form365d_diff",'),
    (r'"h2h_2y_diff",\s*', ''),
    (r'"rest_diff",\s*"matches_7d_diff",\s*"matches_14d_diff",\s*', ''),
    (r'"years_pro_diff",\s*', ''),
    (r'"is_seeded_diff",\s*', ''),
    (r'"tourney_game_winpct_diff",\s*"tourney_sets_winpct_diff",', '"tourney_game_winpct_diff",'),
    (r'"giant_killer_10_diff",\s*', ''),
    
    # Autres tableaux/calculs de base
    (r'serve_diff_5 = \{k: np\.empty\(n\) for k in SERVE_RETURN_KEYS\}\s*', ''),
    (r'# Saisonnalité.*?season_cos = np\.cos\(2 \* np\.pi \* day_of_year / 365\.25\)\n\n', ''),
    
    # Suppression dans la boucle de calcul (i)
    (r'out\["elo_win_prob"\]\[i\] = elo_expected\(e1, e2\)\n\s*', ''),
    (r'out\["rank_diff"\]\[i\] = \(r2 - r1\) if has_rank else np\.nan\n\s*', ''),
    
    (r'out\["form5_diff"\].*?\n\s*out\["form10_diff"\]', 'out["form10_diff"]'),
    (r'out\["form20_diff"\].*?\n\s*out\["form90d_diff"\].*?\n\s*out\["form180d_diff"\].*?\n\s*out\["form365d_diff"\]', 'out["form365d_diff"]'),
    
    (r'h2h_hist_12 = h2h_history\[p1\]\[p2\].*?out\["h2h_2y_diff"\]\[i\] = _winrate_days\(h2h_hist_12, day, 730\) - 0\.5 if h2h_hist_12 else 0\.0\n\s*', ''),
    
    (r'out\["rest_diff"\]\[i\] = min\(rest1_days, 60\) - min\(rest2_days, 60\)\n\s*', ''),
    (r'out\["matches_7d_diff"\]\[i\] = _count_recent_tuples\(rr1, day, 7\) - _count_recent_tuples\(rr2, day, 7\)\n\s*', ''),
    (r'out\["matches_14d_diff"\]\[i\] = _count_recent_tuples\(rr1, day, 14\) - _count_recent_tuples\(rr2, day, 14\)\n\s*', ''),
    
    (r'yp1 = \(day - fmd1\).*?\n\s*yp2 = \(day - fmd2\).*?\n\s*out\["years_pro_diff"\]\[i\] = yp1 - yp2\n\s*', ''),
    (r'out\["is_seeded_diff"\]\[i\] = int\(sd1 == sd1\) - int\(sd2 == sd2\)\n\s*', ''),
    (r'out\["tourney_sets_winpct_diff"\]\[i\] = \(tsw1/tst1 if tst1 > 0 else 0\.5\) - \(tsw2/tst2 if tst2 > 0 else 0\.5\)\n\s*', ''),
    (r'out\["giant_killer_10_diff"\]\[i\] = _giant_killer_rate_n\(rr1, 10\) - _giant_killer_rate_n\(rr2, 10\)\n\s*', ''),
    
    (r'serve_diff_5\[k\]\[i\] = r5_1\[k_idx\] - r5_2\[k_idx\]\n\s*', ''),
    
    # Dans result = pd.DataFrame
    (r'"season_sin": season_sin,\s*"season_cos": season_cos,\s*', ''),
    (r'result\[f"\{k\}_5_diff"\] = serve_diff_5\[k\]\n\s*', '')
]

clean_file('src/02_feature_engineering.py', reps_02)
print("02 cleaned")

# Nettoyage de 05_predict_match.py
reps_05 = [
    (r'feat\["elo_win_prob"\].*?\n', ''),
    (r'feat\["rank_diff"\].*?\n', ''),
    
    (r'feat\["form5_diff"\].*?\n\s*feat\["form10_diff"\]', 'feat["form10_diff"]'),
    (r'feat\["form20_diff"\].*?\n\s*feat\["form90d_diff"\].*?\n\s*feat\["form180d_diff"\].*?\n\s*feat\["form365d_diff"\]', 'feat["form365d_diff"]'),
    
    (r'hh12\s*=\s*h2h_hist\.get\(p1, \{\}\)\.get\(p2, \[\]\)\n\s*feat\["h2h_2y_diff"\].*?\n\s*', ''),
    
    (r'feat\["rest_diff"\].*?\n\s*', ''),
    (r'feat\["matches_7d_diff"\].*?\n\s*', ''),
    (r'feat\["matches_14d_diff"\].*?\n\s*', ''),
    
    (r'feat\["years_pro_diff"\].*?\\.*?\n\s*', ''),
    (r'feat\["is_seeded_diff"\].*?\n\s*', ''),
    
    (r'feat\["tourney_sets_winpct_diff"\].*?\n\s*-.*?0\.5\)\n\s*\)\n\s*', ''),
    
    (r'feat\["giant_killer_10_diff"\].*?\n\s*', ''),
    
    (r'feat\[f"\{k\}_5_diff"\].*?\n\s*', ''),
    
    (r'# Saisonnalite.*?\n\s*doy = match_date\.dayofyear\n\s*feat\["season_sin"\].*?\n\s*feat\["season_cos"\].*?\n\s*', ''),
]

clean_file('src/05_predict_match.py', reps_05)
print("05 cleaned")
