import pandas as pd
import numpy as np
import requests
import time
import random
from datetime import datetime, timedelta
from rapidfuzz import process, fuzz

def scrape_recent_matches(days=30):
    """
    1. SCRAPING (30 DERNIERS JOURS)
    Récupère les résultats des matchs de tennis (ATP/WTA) via une API JSON (ex: SofaScore).
    """
    matches_data = []
    
    # Simulation d'URL d'API (à remplacer par l'URL exacte)
    # L'API SofaScore par exemple utilise des dates dans l'URL: /api/v1/sport/tennis/scheduled-events/{date}
    base_url = "https://api.sofascore.com/api/v1/sport/tennis/scheduled-events/{date}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "*/*",
        "Origin": "https://www.sofascore.com",
        "Referer": "https://www.sofascore.com/"
    }

    start_date = datetime.now() - timedelta(days=days)
    
    print(f"Début du scraping pour les {days} derniers jours...")
    
    # Boucle sur les 30 derniers jours
    for i in range(days + 1):
        target_date = (start_date + timedelta(days=i)).strftime('%Y-%m-%d')
        url = base_url.format(date=target_date)
        
        try:
            # Délai aléatoire pour éviter les blocages IP (2 à 4 secondes)
            time.sleep(random.uniform(2.0, 4.0))
            
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                
                # Extraction des données pertinentes depuis la structure JSON 
                # (La structure dépend de l'API ciblée, ici adaptée pour SofaScore)
                for event in data.get('events', []):
                    # On peut ajouter un filtre pour le tournoi (ATP/WTA uniquement)
                    
                    p1_name = event.get('homeTeam', {}).get('name')
                    p2_name = event.get('awayTeam', {}).get('name')
                    start_timestamp = event.get('startTimestamp')
                    
                    if p1_name and p2_name and start_timestamp:
                        # Conversion du timestamp Unix en objet datetime
                        match_dt = datetime.fromtimestamp(start_timestamp)
                        matches_data.append({
                            'player1': p1_name,
                            'player2': p2_name,
                            'match_datetime': match_dt
                        })
            else:
                print(f"Erreur API {response.status_code} pour la date {target_date}")
                
        except Exception as e:
            print(f"Erreur de connexion pour la date {target_date}: {e}")
            
    return pd.DataFrame(matches_data)


def match_player_names(scraped_names, dataset_names, threshold=85):
    """
    2. RÉCONCILIATION DES DONNÉES (DATA MATCHING)
    Fait correspondre les noms scrappés avec les noms du dataset existant
    en utilisant thefuzz (distance de Levenshtein).
    """
    mapping = {}
    
    # On déduplique pour optimiser le temps de traitement
    unique_scraped = set([name for name in scraped_names if pd.notna(name)])
    unique_dataset = set([name for name in dataset_names if pd.notna(name)])
    
    for scraped_name in unique_scraped:
        # extractOne renvoie un tuple (meilleur_match, score)
        # On utilise token_sort_ratio qui gère bien l'inversion Nom/Prénom (ex: "Alcaraz C." vs "Carlos Alcaraz")
        best_match, score = process.extractOne(scraped_name, unique_dataset, scorer=fuzz.token_sort_ratio)
        
        if score >= threshold:
            mapping[scraped_name] = best_match
        else:
            mapping[scraped_name] = np.nan  # Pas de correspondance trouvée
            
    return mapping


def calculate_rest_features(df):
    """
    3. FEATURE ENGINEERING
    Calcule les features de fatigue (heures) et le flag de match en soirée.
    Le DataFrame d'entrée (df) doit contenir:
    - 'player1' (nom réconcilié)
    - 'player2' (nom réconcilié)
    - 'match_datetime' (datetime de début du match)
    """
    # Copie pour éviter de modifier le DataFrame original
    df_feat = df.copy()
    
    # S'assurer que match_datetime est au format datetime
    df_feat['match_datetime'] = pd.to_datetime(df_feat['match_datetime'])
    
    # Trier chronologiquement pour le calcul de l'historique
    df_feat = df_feat.sort_values('match_datetime').reset_index(drop=True)
    
    # Dictionnaire pour stocker la date/heure du dernier match connu pour chaque joueur
    last_match_time = {}
    
    p1_hours_since_last = []
    p2_hours_since_last = []
    
    for idx, row in df_feat.iterrows():
        p1 = row['player1']
        p2 = row['player2']
        current_time = row['match_datetime']
        
        # Heures depuis le dernier match pour P1
        if pd.notna(p1) and p1 in last_match_time and pd.notna(current_time):
            hours_p1 = (current_time - last_match_time[p1]).total_seconds() / 3600.0
        else:
            hours_p1 = np.nan
            
        # Heures depuis le dernier match pour P2
        if pd.notna(p2) and p2 in last_match_time and pd.notna(current_time):
            hours_p2 = (current_time - last_match_time[p2]).total_seconds() / 3600.0
        else:
            hours_p2 = np.nan
            
        p1_hours_since_last.append(hours_p1)
        p2_hours_since_last.append(hours_p2)
        
        # Mettre à jour l'heure de dernier match pour le joueur (on considère le début du match courant)
        # Note: on pourrait aussi ajouter une durée moyenne de match (ex: +2h) si on veut l'heure de fin
        if pd.notna(p1) and pd.notna(current_time):
            last_match_time[p1] = current_time
        if pd.notna(p2) and pd.notna(current_time):
            last_match_time[p2] = current_time

    # 3.a hours_since_last_match (pour P1 et P2)
    df_feat['hours_since_last_match_p1'] = p1_hours_since_last
    df_feat['hours_since_last_match_p2'] = p2_hours_since_last
    
    # 3.b hours_rest_diff (Différence de temps de repos en heures)
    # Positif si P1 a eu plus de repos, négatif si P2 a eu plus de repos
    df_feat['hours_rest_diff'] = df_feat['hours_since_last_match_p1'] - df_feat['hours_since_last_match_p2']
    
    # 3.c short_rest_p1 / short_rest_p2 (Flag binaire: moins de 20h de repos)
    # On utilise np.where pour gérer les cas NaN proprement
    df_feat['short_rest_p1'] = np.where(df_feat['hours_since_last_match_p1'].isna(), np.nan, 
                                        (df_feat['hours_since_last_match_p1'] < 20).astype(float))
    
    df_feat['short_rest_p2'] = np.where(df_feat['hours_since_last_match_p2'].isna(), np.nan, 
                                        (df_feat['hours_since_last_match_p2'] < 20).astype(float))
    
    # 3.d is_night_match (Flag binaire: 1 si le match débute à 20h00 ou après, 0 sinon)
    df_feat['is_night_match'] = np.where(df_feat['match_datetime'].isna(), np.nan, 
                                         (df_feat['match_datetime'].dt.hour >= 20).astype(float))
    
    return df_feat


def main():
    # --- Exemple d'orchestration ---
    
    # 1. Scraping des derniers matchs
    df_scraped = scrape_recent_matches(days=30)
    
    if df_scraped.empty:
        print("Aucune donnée récupérée par le scraper.")
        return
        
    print(f"\n{len(df_scraped)} matchs récupérés avec succès.")
    
    # 2. Réconciliation avec votre base de joueurs (Exemple fictif)
    # Ici vous passerez les noms uniques de votre DataFrame existant
    dataset_names = ["Carlos Alcaraz", "Novak Djokovic", "Jannik Sinner", "Daniil Medvedev"]
    
    print("\nRéconciliation des noms en cours...")
    # On rassemble tous les noms scrappés
    all_scraped_names = pd.concat([df_scraped['player1'], df_scraped['player2']]).unique()
    
    name_mapping = match_player_names(all_scraped_names, dataset_names, threshold=85)
    
    # On applique la réconciliation sur notre DataFrame scrappé
    df_scraped['player1_std'] = df_scraped['player1'].map(name_mapping)
    df_scraped['player2_std'] = df_scraped['player2'].map(name_mapping)
    
    # Remplacement par les noms standardisés pour le feature engineering
    # On garde les lignes où la réconciliation a marché pour au moins un joueur (ou les deux)
    df_scraped['player1'] = df_scraped['player1_std']
    df_scraped['player2'] = df_scraped['player2_std']
    
    # 3. Calcul des Features
    print("\nCalcul des features de fatigue...")
    df_features = calculate_rest_features(df_scraped)
    
    # Affichage des résultats
    print("\nAperçu des nouvelles features (colonnes cibles):")
    cols = ['player1', 'player2', 'match_datetime', 
            'hours_since_last_match_p1', 'hours_since_last_match_p2', 
            'hours_rest_diff', 'short_rest_p1', 'is_night_match']
    
    # On affiche que les lignes où l'on a calculé un repos (sans NaN) pour l'exemple
    mask_has_rest = df_features['hours_since_last_match_p1'].notna() | df_features['hours_since_last_match_p2'].notna()
    if mask_has_rest.any():
        print(df_features.loc[mask_has_rest, cols].head())
    else:
        print("Pas assez de matchs consécutifs pour les joueurs mappés pour calculer le repos dans l'échantillon.")
        print(df_features[cols].head())

if __name__ == "__main__":
    main()
