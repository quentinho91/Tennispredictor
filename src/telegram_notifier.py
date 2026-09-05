"""
telegram_notifier.py - Module d'alerte et de notification Telegram automatique pour Tennis Predictor.

Fonctionnalités :
1. Télécharge les derniers modèles et états récents (GitHub Release).
2. Analyse l'intégralité des matchs et cotes du jour via TennisExplorer (sans quota API).
3. Détecte les Value Bets haute/moyenne confiance et assemble les combinés optimaux.
4. Formate un briefing complet, élégant et lisible en HTML pour Telegram.
5. Envoie le message via l'API Telegram Bot (zéro dépendance externe, urllib natif).
"""

import os
import sys
import json
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "src"))

logger = logging.getLogger("tennis_predictor.telegram")

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    disable_web_preview: bool = True
) -> Dict[str, Any]:
    """
    Envoie un message via l'API Telegram Bot.
    Gère automatiquement le découpage si le message dépasse la limite de 4096 caractères de Telegram.
    """
    if not bot_token or not chat_id:
        return {"success": False, "error": "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant"}

    url = TELEGRAM_API_URL.format(token=bot_token, method="sendMessage")

    # Découpage si > 4000 caractères
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] if len(text) > max_len else [text]

    responses = []
    for idx, chunk in enumerate(chunks):
        payload = {
            "chat_id": str(chat_id).strip(),
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_preview
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "TennisPredictor/2.0"}
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res_body = json.loads(resp.read().decode("utf-8"))
                responses.append(res_body)
        except urllib.error.HTTPError as he:
            err_msg = he.read().decode("utf-8")
            logger.error(f"Erreur HTTP Telegram ({he.code}): {err_msg}")
            return {"success": False, "status_code": he.code, "error": err_msg}
        except Exception as e:
            logger.error(f"Erreur envoi Telegram: {e}")
            return {"success": False, "error": str(e)}

    return {"success": True, "parts_sent": len(chunks), "responses": responses}


def format_daily_telegram_briefing(scan_data: Dict[str, Any]) -> str:
    """
    Formate un rapport complet et clair des Value Bets et Combinés du jour en HTML pour Telegram.
    """
    now = datetime.now()
    days_fr = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    months_fr = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    date_str = f"{days_fr[now.weekday()]} {now.day} {months_fr[now.month - 1]} {now.year}"
    time_str = now.strftime("%H:%M")

    matches = scan_data.get("matches", [])
    total_matches = len(matches)
    source_name = "TennisExplorer (Cotes Directes)"

    # Extraction des Value Bets qualifiés
    value_bets = []
    for m in matches:
        if m.get("has_value_bet") and m.get("top_value_bet"):
            tvb = m["top_value_bet"]
            value_bets.append({
                "match": f"{m.get('p1', 'P1')} vs {m.get('p2', 'P2')}",
                "tournament": m.get("tournament", "Tournoi"),
                "surface": m.get("surface", "Hard"),
                "time": m.get("time_display", "Aujourd'hui"),
                "selection": tvb.get("selection", "Sélection"),
                "market": tvb.get("market", "Vainqueur"),
                "offered_odds": tvb.get("offered_odds", 0.0),
                "fair_odds": tvb.get("fair_odds", 0.0),
                "prob_pct": tvb.get("prob", 0.0),
                "edge_pct": tvb.get("edge_pct", 0.0),
                "ev_pct": tvb.get("ev_pct", 0.0),
                "confidence_score": tvb.get("confidence", {}).get("score") or m.get("prediction", {}).get("match_confidence", 70.0),
                "confidence_status": tvb.get("confidence_status", "VALIDE"),
                "kelly_pct": tvb.get("kelly_pct", tvb.get("kelly_quarter_pct", 1.0))
            })

    # Trier par espérance de gain (EV) décroissante
    value_bets.sort(key=lambda x: x["ev_pct"], reverse=True)

    lines = []
    lines.append("🎾 <b>TENNIS PREDICTOR AI — BRIEFING QUOTIDIEN</b> 🎾")
    lines.append(f"📅 <i>{date_str} • {time_str}</i>")
    lines.append(f"📡 <b>Source</b> : {source_name} | <b>Analysés</b> : {total_matches} matchs")
    lines.append("")

    # --- SECTION VALUE BETS ---
    if value_bets:
        lines.append(f"🔥 <b>VALUE BETS DÉTECTÉS ({len(value_bets)})</b>")
        lines.append("────────────────────────")
        for idx, vb in enumerate(value_bets, 1):
            conf_icon = "🔥" if vb["confidence_score"] >= 72 else "🎯"
            lines.append(f"<b>{idx}. {vb['match']}</b>")
            lines.append(f"🏆 {vb['tournament']} ({vb['surface']}) • ⏰ {vb['time']}")
            lines.append(f"👉 <b>Sélection</b> : <u>{vb['selection']}</u>")
            lines.append(f"💰 <b>Cote offerte</b> : <b>{vb['offered_odds']}</b> <i>(Cote juste : {vb['fair_odds']})</i>")
            lines.append(f"📈 <b>Edge</b> : <b>+{vb['edge_pct']}%</b> | <b>EV</b> : <b>+{vb['ev_pct']}%</b>")
            lines.append(f"{conf_icon} <b>Confiance IA</b> : {vb['confidence_score']}% | <b>Mise conseillée</b> : {vb['kelly_pct']}% Kelly")
            lines.append("")
    else:
        lines.append("🛡️ <b>AUCUN VALUE BET QUALIFIÉ AUJOURD'HUI</b>")
        lines.append("<i>Le modèle n'a pas détecté de marge mathématique suffisante (Edge > 5.0%, EV > 5.5%) sur les matchs du jour face aux bookmakers.</i>")
        lines.append("💡 <i>Règle d'or : Savoir ne pas parier protège votre capital !</i>")
        lines.append("")

    # --- SECTION COMBINÉS DU JOUR ---
    parlays = scan_data.get("daily_parlays", {})
    if parlays and parlays.get("has_parlays"):
        lines.append("🎯 <b>COMBINÉS CONSEILLÉS DU JOUR</b>")
        lines.append("────────────────────────")

        # 1. Combiné IA Optimisé (Max Cote) - Flagship Recommandation du Jour
        max_parlay = parlays.get("max_odds")
        if max_parlay and max_parlay.get("selections"):
            count = len(max_parlay.get("selections", []))
            lines.append(f"🔥 <b>COMBINÉ IA OPTIMISÉ (MAX COTE • {count} SÉLECTIONS)</b>")
            lines.append("⭐ <i>Multiplicateur Maximal • Recommandation IA du Jour</i>")
            lines.append(f"• <b>Cote Totale</b> : <b>@{max_parlay.get('total_odds')}</b> | <b>Probabilité IA</b> : <b>{max_parlay.get('combined_prob_pct')}%</b>")
            lines.append(f"• <b>Confiance</b> : <b>{max_parlay.get('confidence_score')}%</b> ({max_parlay.get('confidence_label', 'Très haute')})")
            lines.append("📋 <b>Détail du ticket :</b>")
            for sel in max_parlay.get("selections", []):
                m_disp = sel.get("match_display", "")
                tourn = sel.get("tournament", "")
                pick = sel.get("selection", "")
                o_val = sel.get("odds", "")
                p_val = sel.get("prob_pct", "")
                lines.append(f"   ▫️ {m_disp} ({tourn})")
                lines.append(f"      👉 <b>Victoire {pick}</b> @ <b>{o_val}</b> <i>(IA: {p_val}%)</i>")
            lines.append("")

        # 2. Combiné Value Bets (EV+)
        val_parlay = parlays.get("value")
        if val_parlay and val_parlay.get("selections"):
            lines.append("🚀 <b>Combiné Value Bets (EV+)</b>")
            lines.append(f"• <b>Cote totale</b> : <b>@{val_parlay.get('total_odds')}</b> | <b>EV combinée</b> : <b>+{val_parlay.get('ev_pct')}%</b>")
            for sel in val_parlay.get("selections", []):
                lines.append(f"   ▫️ {sel.get('match_display')} : <b>{sel.get('selection')}</b> @ <b>{sel.get('odds')}</b>")
            lines.append("")

        # 3. Combiné Sécurité / Haute Probabilité
        safe_parlay = parlays.get("safe")
        if safe_parlay and safe_parlay.get("selections"):
            lines.append("🛡️ <b>Combiné Sécurité / Haute Confiance</b>")
            lines.append(f"• <b>Cote totale</b> : <b>@{safe_parlay.get('total_odds')}</b> | <b>Probabilité</b> : <b>{safe_parlay.get('combined_prob_pct')}%</b>")
            for sel in safe_parlay.get("selections", []):
                lines.append(f"   ▫️ {sel.get('match_display')} : <b>{sel.get('selection')}</b> @ <b>{sel.get('odds')}</b>")
            lines.append("")

    lines.append("───────────────")
    lines.append("🤖 <i>Généré automatiquement par Tennis Predictor AI (Stacking Ensemble XGBoost + Markov)</i>")

    return "\n".join(lines)


def run_daily_scan_and_notify(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    force_update_models: bool = True
) -> Dict[str, Any]:
    """
    Exécute le pipeline complet :
    1. Mise à jour des modèles via GitHub Release.
    2. Scraping et analyse complète des matchs TennisExplorer.
    3. Construction et envoi du briefing Telegram.
    """
    token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    # 1. Synchronisation des modèles récents
    if force_update_models:
        try:
            from download_release import download_release_assets
            logger.info("Synchronisation des modèles récents depuis la release GitHub...")
            download_release_assets()
        except Exception as e:
            logger.warning(f"Note: Impossible de rafraîchir les modèles depuis la release: {e}")

    # 2. Import des dépendances du pipeline d'analyse
    from odds_scanner import scan_daily_matches
    from app import predict_match, get_cached_resources, smart_resolve_name

    logger.info("Lancement de l'analyse des matchs quotidiens avec TennisExplorer...")
    scan_data = scan_daily_matches(
        circuit="all",
        source="tennisexplorer",
        force_refresh=True,
        predict_func=predict_match,
        get_resources_func=get_cached_resources,
        smart_resolve_func=smart_resolve_name
    )

    # 3. Formatage du message
    briefing_html = format_daily_telegram_briefing(scan_data)

    # 4. Envoi sur Telegram
    send_result = {"success": False, "message": "Tokens Telegram non configurés"}
    if token and cid:
        logger.info(f"Envoi du briefing sur Telegram (chat_id={cid})...")
        send_result = send_telegram_message(token, cid, briefing_html)
    else:
        logger.info("Alerte: TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID non configuré. Message généré sans envoi.")

    return {
        "success": True,
        "matches_count": len(scan_data.get("matches", [])),
        "value_bets_count": scan_data.get("value_bets_count", 0),
        "telegram_sent": send_result.get("success", False),
        "telegram_details": send_result,
        "message_preview": briefing_html
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scan quotidien TennisExplorer et notification Telegram")
    parser.add_argument("--dry-run", action="store_true", help="Générer le message sans l'envoyer")
    parser.add_argument("--no-update", action="store_true", help="Ne pas retélécharger les modèles")
    args = parser.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    cid = os.getenv("TELEGRAM_CHAT_ID")

    if args.dry_run:
        token, cid = None, None

    result = run_daily_scan_and_notify(
        bot_token=token,
        chat_id=cid,
        force_update_models=not args.no_update
    )

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("\n================== MESSAGE TELEGRAM GÉNÉRÉ ==================\n")
    try:
        print(result["message_preview"])
    except UnicodeEncodeError:
        print(result["message_preview"].encode("ascii", "replace").decode("ascii"))
    print("\n=============================================================")
    status_msg = "Envoye avec succes" if result["telegram_sent"] else "Non envoye (Tokens absents ou mode dry-run)"
    print(f"Statut envoi Telegram : {status_msg}")
