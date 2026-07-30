# Krypto_Trader – Lokaler Worker (v1.6.0)

Rechnet **Backtests**, **Optimierungen / Strategie-Suche**, **Regime-Lab-Jobs**
(Analyse, Regime-Optimierung, Walk-Forward) und **Kerzen-Downloads** auf dem
eigenen Rechner. Der Worker verbindet sich per Outbound-Polling zum Server –
es sind **keine Portfreigaben oder feste IP** nötig.

Die Rechen-Module (`services/`, `strategies/`, `core/`, `models/`) liegen im
Paket und sind identisch zum Server. Ergebnisse sind damit 1:1 vergleichbar mit
der Cloud-Ausführung.

---

## 1. Voraussetzungen

* **Python 3.11 oder 3.12** (3.13 wird von einigen Abhängigkeiten noch nicht
  vollständig unterstützt)
  Windows: <https://www.python.org/downloads/> – beim Installieren
  **„Add python.exe to PATH“** anhaken.

Prüfen:

```
python --version
```

## 2. Installation (Windows)

Im entpackten Ordner (dort wo `worker.py` liegt) eine Eingabeaufforderung
öffnen (Adresszeile im Explorer: `cmd` eintippen + Enter) und ausführen:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux / macOS:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Starten

```
python worker.py
```

Beim ersten Start wird gefragt nach:

| Frage | Wert |
|---|---|
| Server-URL | die Adresse der Website, z.B. `https://meine-app.example.com` |
| Worker-Token | Website → **Ausführung → Lokal → ⚙ Verwalten → Token anzeigen** |
| Anzeigename | freier Name, erscheint in der Worker-Liste |

Die Angaben landen in `worker_config.json` (bleibt lokal, wird **nicht** mit
dem Paket ausgeliefert). Alternativ direkt per Parameter:

```
python worker.py --url https://meine-app.example.com --token DEIN_TOKEN --name "Gaming-PC"
```

Windows-Komfort: `start_worker.bat` doppelklicken (legt venv an, installiert
Abhängigkeiten, startet den Worker).

Erfolgreich verbunden, wenn im Log steht:

```
Rechen-Module geladen – warte auf Jobs
```

und die Website unter **Ausführung → Lokal** den Worker als *online* zeigt.

## 4. Einstellungen

Alle Einstellungen werden auf der **Website** gepflegt
(Ausführung → Lokal → ⚙ Verwalten) und beim Polling automatisch übernommen:

| Einstellung | Wirkung |
|---|---|
| CPU-Kerne | `0` = alle Kerne; steuert die parallele Simulation |
| RAM-Limit (MB) | Obergrenze des Kerzen-Caches im Speicher |
| GPU nutzen | NVIDIA/CuPy für Indikator-Vorberechnung (optional) |
| Parallele Jobs | wie viele Jobs gleichzeitig laufen dürfen |
| Datenordner | Ablage der Kerzen-Dateien (Standard: `./candle_data`) |

## 5. Daten (Kerzen-Cache)

Downloads werden als Job auf dem Worker ausgeführt
(Website → Ausführung → Lokal → Daten). Die Dateien liegen im Datenordner und
werden beim nächsten Backtest sofort genutzt – lange Zeiträume (z.B. 2000+
Tage) laufen dadurch ein Vielfaches schneller.

## 6. Update

Der Server verlangt eine Mindest-Version. Zeigt die Website
*„Worker veraltet“*:

1. Website → Ausführung → Lokal → ⚙ Verwalten → **Worker-Paket herunterladen**
2. ZIP in den bestehenden Ordner entpacken und überschreiben
   (`worker_config.json` bleibt erhalten – sie ist nicht im ZIP)
3. `pip install -r requirements.txt` erneut ausführen
4. `python worker.py` neu starten

Version prüfen: `python worker.py --version`

## 7. Fehlersuche

| Meldung | Ursache / Lösung |
|---|---|
| `401 Ungültiges Worker-Token` | Token neu kopieren (ggf. wurde er regeneriert) |
| `Kein lokaler Worker verbunden` | Worker läuft nicht oder URL falsch |
| `Der verbundene lokale Worker ist veraltet` | Paket neu herunterladen (Punkt 6) |
| `ModuleNotFoundError` | `pip install -r requirements.txt` im aktivierten venv |
| Job bleibt bei „Wartet auf lokalen Worker“ | Worker-Log prüfen (`worker.log`) |

Alle Ausgaben landen zusätzlich in `worker.log` im Worker-Ordner.

## 8. Paketinhalt

```
worker.py           Worker selbst (dieser Prozess)
requirements.txt    Python-Abhängigkeiten
README.md           diese Anleitung
start_worker.bat    Windows-Starthilfe
core/ services/ strategies/ models/   Rechen-Module (identisch zum Server)
```
