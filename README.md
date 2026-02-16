# ⚡ Power Scheduler v9.0 Duration Aware

Das vollständige Handbuch zur intelligenten, strompreisgeführten Task-Steuerung für Unraid.

💰Dynamische Preise Automatischer API-Abruf (CKW). Umrechnung in Rappen, Berechnung von Preis-Tiers (1-20) und Hard-Cap Schutz.

⏳Smart Duration Berechnet den Durchschnittspreis über die **gesamte Laufzeit** des Jobs. Verhindert, dass lange Jobs in den Hochtarif laufen.

🚀Parallele Ausführung Der Executor startet mehrere Jobs **gleichzeitig**, wenn Preis und Bedingungen stimmen.

🔗Dependency Manager Definiere **Abhängigkeiten** (`Groups` & `Order`). Job B startet erst, wenn Job A fertig ist.

📈Self-Learning Lernt die Laufzeit deiner Scripte (Moving Average) für präzisere Planung.

🚨Disk Full Protection Überwacht den Cache-Speicher. Bei >90% Belegung wird **SOFORT** der Mover gestartet.

🕒Time Profiles Definiere Sperrzeiten (z.B. TV-Zeit 18-22 Uhr) mit Unterscheidung zwischen Werktag und Wochenende.

🔍Smart First Run Scannt beim ersten Start sofort alle verfügbaren Daten (Heute+Morgen) für den optimalen Einstieg.

Inhaltsverzeichnis

*   [1\. Installation & Einrichtung](#installation)
*   [2\. Die Script-Dateien](#files)
*   [3\. Konfiguration & Parameter](#config)
*   [4\. Tier-Logik & Mathematik](#tiers)
*   [5\. Praxis-Beispiele](#examples)
*   [6\. Notfall-System](#emergency)
*   [7\. FAQ & Troubleshooting](#faq)

## <a id="installation"></a>1\. Installation & Einrichtung

*   Schritt 1: User Scripts Plugin Installiere das Plugin **"User Scripts"** über den Unraid "Apps" Tab, falls noch nicht vorhanden.
*   Schritt 2: Planner anlegen Erstelle ein neues Script: `Power_Planner_Daily`.  
    Füge den Code aus `power_planner.py` ein.  
    Setze den Schedule auf **"Daily"** (oder Custom `0 13 * * *` für 13:00 Uhr).
*   Schritt 3: Executor anlegen Erstelle ein neues Script: `Power_Executor_15min`.  
    Füge den Code aus `executor_15min.py` ein.  
    Setze den Schedule auf **"Custom"** und trage `*/15 * * * *` ein.
*   Schritt 4: Konfiguration & Test Öffne den Executor und passe die `SCRIPTS_CONFIG` an.  
    Setze `DRY_RUN = True` zum Testen.  
    Führe erst den **Planner**, dann den **Executor** manuell aus ("Run Script") und prüfe die Logs.

## <a id="files"></a>2\. Die Script-Dateien

📄

power\_planner.py Daily Verbindet sich mit der CKW API, lädt Strompreise, beachtet Hard-Cap (6 Rp), berechnet Tiers (1-20) und speichert das JSON.

⚙️

executor\_15min.py Alle 15 Min Der Manager. Prüft Disk-Platz, Cooldowns, Deadlines und Preise. Startet Jobs und schreibt Logs.

## <a id="config"></a>3\. Konfiguration (Parameter)

Die Konfiguration findet im Executor-Script in der Liste `SCRIPTS_CONFIG` statt.

| Parameter | Typ | Beschreibung |
| --- | --- | --- |
| `id` | String | Eindeutiger Name (ohne Leerzeichen).  <br>_Bsp: "Backup"_ |
| `command` | String | Absoluter Pfad zum Befehl.  <br>_Bsp: "bash /mnt/user/scripts/run.sh >> /log/path 2>&1"_ |
| `min_interval_hours` | Int | **Cooldown:** Minimale Pause zwischen zwei Starts. |
| `max_interval_hours` | Int | **Deadline:** Spätestens hier MUSS gestartet werden (suche besten Preis bis dahin). |
| `max_tier` | Int | **Preis-Limit (1-20):** 1=Billigst, 20=Teuer. |
| `profile_mode` | String | Verweis auf Zeit-Profil (z.B. "STRICT", "IGNORE\_TIME"). |
| `group` | String | (Optional) Nur EIN Script pro Gruppe läuft gleichzeitig. |
| `order` | Int | (Optional) Reihenfolge in der Gruppe (1 vor 2). |

### Komplettes Konfigurations-Beispiel

SCRIPTS\_CONFIG = \[
    {
        \# --- BASIS EINSTELLUNGEN ---
        "id": "Appdata\_Backup",
        "command": "bash /mnt/user/scripts/backup.sh >> /mnt/user/appdata/logs/backup.log 2>&1",
        "initial\_runtime\_min": 45,  \# Schätzwert für den ersten Lauf

        \# --- TIMING ---
        "min\_interval\_hours": 24,   \# 1x Täglich
        "max\_interval\_hours": 30,   \# Max 6h warten auf besseren Preis
        
        \# --- LOGIK ---
        "max\_tier": 10,             \# Durchschnittspreis ist OK
        "profile\_mode": "STRICT",   \# Beachtet TV-Sperrzeiten
        
        \# --- ABHÄNGIGKEITEN (Optional) ---
        "group": "backup\_chain",    \# Gehört zur Backup-Gruppe
        "order": 1                  \# Läuft als Erstes
    }
\]

## <a id="tiers"></a>4\. Tier-Logik & Mathematik

Um Preise vergleichbar zu machen, nutzt das System keine absoluten Rappen-Werte (da diese im Winter hoch und im Sommer niedrig sind), sondern **relative Tiers (Ränge)** von 1 bis 20.

### Die Berechnung

1.  Alle Preise des Tages (00:00 - 23:45), die **unter** dem Hard-Cap (6 Rp) liegen, werden gesammelt.
2.  Diese Preise werden von **billig nach teuer** sortiert.
3.  Die Liste wird in 20 gleich große Teile (Quantile) unterteilt.

### Die Bedeutung

| Tier | Prozentrang | Bedeutung |
| --- | --- | --- |
| **1** | Top 5% | Die absolut billigsten 5% des Tages. |
| **5** | Top 25% | Unteres Viertel. Sehr guter Preis. |
| **10** | Top 50% | Genau der Durchschnitt (Median). |
| **20** | Top 100% | Die teuersten Stunden des Tages (aber noch unter 6 Rp). |
| **99** | Hard Cap | Preis ist höher als 6 Rp. **Ausführung Blockiert.** |

## <a id="examples"></a>5\. Praxis-Beispiele

### Beispiel A: Unraid Mover (Standard)

Soll einmal täglich laufen, aber nicht zur TV-Zeit.

{
    "id": "Mover",
    "command": "/usr/local/sbin/mover",
    "min\_interval\_hours": 20,
    "max\_interval\_hours": 48,
    "max\_tier": 10,
    "profile\_mode": "STRICT"
}

### Beispiel B: Backup Kette (Abhängigkeiten)

Erst lokales Backup. Wenn fertig, dann (im nächsten Zyklus) Cloud Upload.

{
    \# SCHRITT 1: Lokal
    "id": "Local\_Backup",
    "command": "bash /mnt/user/scripts/backup.sh >> /mnt/user/logs/backup.log 2>&1",
    "min\_interval\_hours": 24,
    "max\_interval\_hours": 30,
    "group": "backup\_chain",
    "order": 1
},
{
    \# SCHRITT 2: Upload (Wartet auf 1)
    "id": "Cloud\_Upload",
    "command": "bash /mnt/user/scripts/upload.sh >> /mnt/user/logs/upload.log 2>&1",
    "min\_interval\_hours": 24,
    "max\_interval\_hours": 30,
    "group": "backup\_chain",
    "order": 2
}

### Beispiel C: Download / Cache

Hintergrundjob. Darf immer laufen (leise).

{
    "id": "Emby\_Cache",
    "command": "/usr/bin/python3 .../emby\_cache.py",
    "min\_interval\_hours": 20,
    "max\_interval\_hours": 30,
    "max\_tier": 5,
    "profile\_mode": "IGNORE\_TIME"
}

## <a id="emergency"></a>6\. 🚨 Notfall-System (Disk Full)

🚨 Priorität 1: Überlauf-Schutz

Der Executor prüft **vor jedem Lauf** den freien Speicherplatz.

*   **Auslöser:** Disk > `DISK_FULL_THRESHOLD` (Standard: 90%).
*   **Reaktion:** Alle normalen Jobs in der Liste werden **ignoriert / übersprungen**.
*   **Aktion:** Das definierte `EMERGENCY_COMMAND` wird ausgeführt.

**Config im Script:**

DISK\_PATH\_CHECK = "/mnt/cache"
DISK\_FULL\_THRESHOLD = 90
EMERGENCY\_COMMAND = "/usr/local/sbin/mover"

## <a id="faq"></a>7\. FAQ & Troubleshooting

### Wie setze ich das "Gedächtnis" zurück?

Wenn du einen sauberen "First Run" simulieren willst:

rm /mnt/user/appdata/power\_scheduler/executor\_state.json

### Wie teste ich sicher (Dry Run)?

Setze im Executor-Script:

DRY\_RUN = True

Das Script schreibt dann ausführliche Logs, führt aber keine Befehle aus und nutzt eine separate Test-Datenbank (`executor_state_dryrun.json`).

### Log-Meldung: "Executor Busy"?

\[BLOCK\] Executor is busy (Jobs running). Exiting.

Das ist **gut**. Es bedeutet, dass ein vorheriger Job noch läuft. Das Script verhindert automatisch, dass Jobs doppelt gestartet werden.

### Wo sind meine Logs?

Wenn du die Ausgabeumleitung (`>>`) wie oben beschrieben nutzt, liegen die Logs in dem Ordner, den du angegeben hast (z.B. `/mnt/user/appdata/logs/`).

Unraid Power Scheduler | Version 8.0 Ultimate
