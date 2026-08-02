# Lokaler Worker (Krypto-Trader)

Rechnet Backtests, Optimierungen, Regime-Lab-Jobs und Daten-Downloads auf
deinem eigenen Rechner. Der Worker verbindet sich per Outbound-Polling mit der
Website – es sind **keine Portfreigaben** nötig.

## Installation

1. Python 3.11+ installieren (https://www.python.org/downloads/ – bei Windows
   "Add python.exe to PATH" anhaken).
2. In diesem Ordner ein Terminal öffnen und die Pakete installieren:

   ```
   pip install -r requirements.txt
   ```

## Start

Token findest du auf der Website unter **Ausführung → Lokal → ⚙ Verwalten**.

```
python worker.py --server https://DEINE-WEBSITE --token DEIN_TOKEN --name "Mein PC"
```

Server, Token und Name werden in `worker_config.json` gespeichert – danach
reicht `python worker.py`.

## Verlässlichkeit

- Verbindungsabbrüche (z.B. `WinError 121`, Timeouts) beendet der Worker
  **nicht** – er versucht es automatisch mit steigendem Abstand (max. 30s)
  erneut. Laufende Berechnungen laufen währenddessen einfach weiter.
- Ergebnisse werden nach Wiederverbindung hochgeladen (bis zu 7,5 Minuten
  Wiederholversuche).
- Kerzendaten liegen standardmäßig im Unterordner `candle_data/` (änderbar
  über die Website-Einstellungen oder `--data-dir`).

## GPU (optional)

NVIDIA-GPU-Beschleunigung: `pip install cupy-cuda12x` (CUDA 12) und auf der
Website "GPU nutzen" aktivieren.
