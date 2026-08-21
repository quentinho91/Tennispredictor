---
title: Tennis Match Predictor AI
emoji: 🎾
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# 🎾 Tennis Match Predictor AI (ATP & WTA) • XGBoost Pure & Value Betting

Application web & mobile de prédiction de tennis en temps réel propulsée par **XGBoost pur**, **modèle markovien point-par-point (Barnett & Clarke)** et **critère de Kelly fractionnaire**.

---

## 🚀 1. Lancer l'application en local (Sur votre PC)

```bash
python app.py
```
Ouvrez ensuite [http://localhost:7860](http://localhost:7860) dans votre navigateur.

---

## 📱 2. Ouvrir sur votre Téléphone (Sur le même Wi-Fi)

1. Lancez `python app.py` sur votre PC.
2. Trouvez l'adresse IP locale de votre PC (ex: `192.168.1.XX`).
3. Ouvrez Safari (iPhone) ou Chrome (Android) et allez sur `http://192.168.1.XX:7860`.
4. Appuyez sur **Partager $\to$ Ajouter sur l'écran d'accueil** pour avoir l'application mobile avec icône !

---

## ☁️ 3. Déployer en ligne 100% GRATUITEMENT sur Hugging Face Spaces (Fonctionne 24h/24 PC éteint)

1. Rendez-vous sur **[huggingface.co/spaces](https://huggingface.co/spaces)** (Créez un compte gratuit si ce n'est pas fait).
2. Cliquez sur **"Create new Space"** :
   - **Space Name** : `tennis-predictor` (ou le nom de votre choix)
   - **Space SDK** : Sélectionnez **Docker** (Blank)
   - **Space Hardware** : **CPU Basic (16 GB RAM) - 100% Free**
   - **Visibility** : Public ou Private
3. Poussez votre code avec Git ou liez votre dépôt GitHub :
   ```bash
   git remote add space https://huggingface.co/spaces/VOTRE_PSEUDO/tennis-predictor
   git push space main
   ```
4. 🎉 **C'est terminé !** Votre application dispose d'un lien permanent HTTPS `https://VOTRE_PSEUDO-tennis-predictor.hf.space` accessible 24h/24 partout sur votre téléphone en 4G/5G !
