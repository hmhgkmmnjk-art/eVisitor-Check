# eVisitor Übernachtungs-Auswertung (noćenja) für iPad

Fragt für bis zu **drei eVisitor-Accounts** die angemeldeten **Übernachtungen**
(kroatisch *noćenja* = Gäste × Nächte) in einem wählbaren Zeitraum ab und
erzeugt eine Terminal-Tabelle **und** einen HTML-Report mit Monats-Balkendiagramm
zum Öffnen in Safari.

---

## Warum ein Python-Skript und nicht die gewünschte reine HTML-Datei?

Das war ausdrücklich zu prüfen (Anforderung 5). Kurzfassung: **Eine lokale
HTML-Datei in Safari kann die eVisitor-API nicht direkt abfragen.**

Die eVisitor-API ist eine **ASP.NET-Forms-Auth-API**:

- Login: `POST https://www.evisitor.hr/eVisitorApi/Resources/AspNetFormsAuth/Authentication/Login`
- Der Login liefert **Session-Cookies**, die bei **jedem** Folgeaufruf mitmüssen.

Ein Aufruf aus einer `file://`-HTML-Datei scheitert **zwingend**:

| Hürde | Grund |
|---|---|
| **CORS** | Die Datei sendet `Origin: null`. Für eine Cookie-Anmeldung müsste der Server `Access-Control-Allow-Credentials: true` **plus** exaktes Origin-Echo senden – ein Regierungssystem tut das für fremde Origins nicht. |
| **Wildcard verboten** | Mit Cookies ist `Access-Control-Allow-Origin: *` nicht erlaubt. |
| **Safari ITP** | Safari (v. a. iPad) blockt Cross-Site-Cookies – die Session bräche selbst bei offenem CORS. |

**CORS betrifft aber nur Browser.** Ein lokales Python-Programm (in der iPad-App
**a-Shell**) kennt diese Beschränkung nicht und verwaltet Cookies problemlos.
Deshalb ist die Lösung ein Python-Skript, das die gewünschte HTML-Ausgabe
**erzeugt** – du bekommst die schöne Safari-Ansicht, nur ohne die CORS-Sackgasse.

> Alternativen, die auch gingen: HTML-Oberfläche + winziger lokaler Proxy in
> a-Shell (`http://localhost:8080`, same-origin → kein CORS) oder Pythonista
> (kostenpflichtig). Sag Bescheid, wenn du lieber die Proxy-Variante möchtest.

---

## So bringst du es aufs iPad (rein auf dem iPad, gratis)

1. **a-Shell installieren** – kostenlos im App Store (Terminal mit Python 3).
2. **Datei `evisitor_nocenja.py` aufs iPad holen**, z. B.:
   - In der **Dateien-App** speichern (iCloud Drive / „Auf meinem iPad").
   - Oder per AirDrop / E-Mail an dich schicken und in Dateien sichern.
   - Oder in a-Shell direkt laden (falls die Datei online liegt):
     `curl -O <URL>/evisitor_nocenja.py`
3. **In a-Shell ins richtige Verzeichnis wechseln.** a-Shell startet im eigenen
   Ordner. Am einfachsten die Datei per `pickFolder` / Dateien-App-Import
   verfügbar machen; a-Shell zeigt importierte Dateien mit `ls`.
   (Menü in a-Shell: Dateien importieren.)
4. **Starten:**
   ```
   python3 evisitor_nocenja.py
   ```

---

## Bedienung

```
python3 evisitor_nocenja.py                 # interaktiv, laufendes Jahr
python3 evisitor_nocenja.py --year 2024     # ganzes Jahr 2024 (Historie)
python3 evisitor_nocenja.py --from 2025-06-01 --to 2025-08-31   # freier Bereich
python3 evisitor_nocenja.py --discover      # Report-Endpunkt erkunden (siehe unten)
python3 evisitor_nocenja.py --test          # Test-API statt Produktion
```

Ablauf:

1. Du gibst 1–3 **Benutzernamen + Passwörter** ein.
   - **Passwörter bleiben nur im Speicher** – sie werden nie gespeichert.
   - **Benutzernamen** dürfen optional gemerkt werden (Datei `evisitor_accounts.json`,
     enthält **nur** Benutzernamen).
2. Du wählst den **Zeitraum** (laufendes Jahr / anderes Jahr / Von-Bis).
3. Das Skript loggt sich nacheinander in jeden Account ein, holt die
   Gäste-Anmeldungen und berechnet die **Übernachtungen**.
4. Ergebnis: Tabelle im Terminal **und** Datei **`evisitor_report.html`**.
   In a-Shell öffnen mit `open evisitor_report.html` oder die Datei in Safari öffnen.

---

## Wie die Übernachtungen gezählt werden

- Eine Übernachtung ist die **Nacht von Tag D auf D+1**; die **Abreisenacht zählt
  nicht** (Abreisedatum = exklusiv). Ein Aufenthalt vom 5.–8. = **3 Nächte**.
- **Zuschnitt auf den Zeitraum:** Aufenthalte, die in den Zeitraum hineinragen
  oder darüber hinausgehen, werden **nur mit den Nächten im Zeitraum** gezählt.
- **Offene Aufenthalte** (Gast noch nicht abgemeldet) werden **bis heute** bzw.
  bis zum Zeitraumende gezählt und **separat** als „davon offen" ausgewiesen.
- Ausgegeben werden: Übernachtungen pro Account, Gesamtsumme, Gäste pro Account
  (Zweitinfo) und eine **Aufschlüsselung pro Monat** als Balkendiagramm.

---

## ⚠️ Ein Wert muss evtl. noch angepasst werden

Der Endpunkt, der die einzelnen Gäste-Anmeldungen mit An-/Abreise liefert, steht
nur in der **login-geschützten** offiziellen Web-API-Wiki. Ich konnte ihn nicht
öffentlich auslesen. Im Skript sind oben Platzhalter gesetzt:

```python
REPORT_PATH     = "/Rest/Htz/EvidencijaGostiju"   # <-- ggf. anpassen
DATE_PARAM_FROM = "datumOd"                        # <-- ggf. anpassen
DATE_PARAM_TO   = "datumDo"                        # <-- ggf. anpassen
```

**So ermittelst du den echten Wert (einmalig, ~2 Min):**

- Bequem: `python3 evisitor_nocenja.py --discover` → loggt einen Account ein und
  zeigt die Roh-Antwort; passt der Pfad nicht, siehst du sofort den Fehler.
- Oder: im Browser auf evisitor.hr einloggen, die Gäste-/Evidenzliste öffnen,
  **Web-Inspektor → Netzwerk** → den JSON-Aufruf ansehen (Pfad + Datums-Parameter
  + Feldnamen für An-/Abreise).

Die eigentliche Übernachtungs-Berechnung ist bereits fertig und getestet – sobald
der richtige Pfad eingetragen ist, läuft alles durch. Die Feldnamen für An-/Abreise
werden bereits in vielen Varianten automatisch erkannt (siehe `CHECKIN_FIELDS` /
`CHECKOUT_FIELDS`).

Wenn du mir eine Beispiel-Antwort von `--discover` schickst, trage ich Pfad und
Feldnamen exakt für dich ein.

---

## Fehlermeldungen (Deutsch)

- „Login fehlgeschlagen (Benutzername/Passwort falsch)."
- „Keine Verbindung zum Server (…)."
- „Report-Endpunkt lieferte keine erkennbare Liste …" (→ `REPORT_PATH` prüfen)

Ein Fehler bei einem Account stoppt die anderen nicht – er wird in Tabelle und
Report klar markiert.
