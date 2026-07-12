#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Übernachtungs-Auswertung (noćenja)
===========================================

Fragt für bis zu drei eVisitor-Accounts die angemeldeten ÜBERNACHTUNGEN
(kroatisch "noćenja" = Gäste × Nächte) in einem wählbaren Zeitraum ab und
erstellt eine Terminal-Tabelle sowie einen HTML-Report (mit Monats-
Balkendiagramm) zum Öffnen in Safari.

Warum ein Python-Skript und keine reine HTML-Datei?
---------------------------------------------------
Die eVisitor-API ist eine ASP.NET-Forms-Auth-API: Der Login liefert
Session-Cookies zurück, die bei jedem weiteren Aufruf mitgeschickt werden
müssen. Ein Aufruf aus einer lokalen HTML-Datei (Origin "null") im Browser
scheitert zwingend an CORS + Safari-Cookie-Schutz (ITP). CORS betrifft aber
NUR Browser – ein lokales Python-Programm (z. B. in der iPad-App "a-Shell")
hat diese Beschränkung nicht und verwaltet Cookies problemlos.

Nur Standardbibliothek – läuft in a-Shell ohne "pip install".

Bedienung (in a-Shell auf dem iPad):
    python3 evisitor_nocenja.py
    python3 evisitor_nocenja.py --year 2024
    python3 evisitor_nocenja.py --from 2025-06-01 --to 2025-08-31
    python3 evisitor_nocenja.py --discover     # Rohdaten des Report-Endpunkts anzeigen
    python3 evisitor_nocenja.py --test         # Test-API statt Produktion

Ergebnis: Datei "evisitor_report.html" – in Safari öffnen.
"""

import sys
import os
import re
import json
import argparse
import getpass
import datetime as dt
import urllib.request
import urllib.error
import http.cookiejar
import ssl
from collections import defaultdict

# ---------------------------------------------------------------------------
# KONFIGURATION
# ---------------------------------------------------------------------------
# API-Wurzel. Produktion ist der Standard; die Test-Umgebung ("--test")
# ist offiziell dokumentiert (Daten-Snapshot der Produktion, eigene Zugänge).
API_ROOT_PROD = "https://www.evisitor.hr/eVisitorRhetos_API"   # Produktion (kein apikey)
API_ROOT_TEST = "https://www.evisitor.hr/testApi"             # Test (braucht apikey)

# Login-Pfad (ASP.NET Forms Auth – laut offizieller Web-API-Doku).
LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login"
# API-Schlüssel nur für die Testplattform nötig; Produktion braucht keinen.
API_KEY = ""

# eVisitor-Ressource für Gäste-/Aufenthaltsdaten (per Diagnose bestätigt).
# Gelesen wird mit ?sort=ID&page=..&psize=..&filters=[..]; Antwort {Records:[]}.
REPORT_PATH = "/Rest/Htz/Tourist/"
ARRIVAL_FIELD = "TimeStayFrom"         # Anreise-Zeitpunkt (Filterfeld)
REPORT_LOOKBACK_DAYS = 370             # Vorlauf, um hineinragende Aufenthalte zu erfassen

CHECKIN_FIELDS = ["TimeStayFrom", "StayFrom", "DatumDolaska"]
CHECKOUT_FIELDS = ["CheckOutTime", "CheckOutDate", "DatumOdlaska"]
FIRSTNAME_FIELDS = ["TouristName", "Ime", "FirstName"]
LASTNAME_FIELDS = ["TouristSurname", "Prezime", "LastName"]
NAME_FIELDS = ["ImePrezime", "Naziv", "Name"]

ACCOUNTS_FILE = "evisitor_accounts.json"   # speichert NUR Benutzernamen (optional)
REPORT_FILE = "evisitor_report.html"
NUM_ACCOUNTS = 3
HTTP_TIMEOUT = 45  # Sekunden

MONTH_NAMES_DE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                  "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


# ---------------------------------------------------------------------------
# Terminal-Hilfen
# ---------------------------------------------------------------------------
def _use_color():
    return sys.stdout.isatty()


def c(text, code):
    if not _use_color():
        return text
    return "\033[%sm%s\033[0m" % (code, text)


def bold(t):   return c(t, "1")
def green(t):  return c(t, "32")
def red(t):    return c(t, "31")
def yellow(t): return c(t, "33")
def cyan(t):   return c(t, "36")


def fail(msg):
    """Verständliche Fehlermeldung auf Deutsch, dann Abbruch."""
    print(red("\nFEHLER: " + msg), file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Datum-Hilfen
# ---------------------------------------------------------------------------
def parse_date(value):
    """Robustes Parsen: ISO (2025-06-01), mit Zeit, oder dd.mm.yyyy."""
    if value is None:
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    # /Date(1725208200000+0200)/  (.NET-JSON-Format mit Zeitzonen-Offset)
    if s.startswith("/Date(") and s.endswith(")/"):
        try:
            inner = s[6:-2]
            m = re.match(r'(-?\d+)([+-]\d{2})(\d{2})', inner)
            if m:
                ms = int(m.group(1))
                oh = int(m.group(2))
                off_min = (abs(oh) * 60 + int(m.group(3))) * (1 if oh >= 0 else -1)
            else:
                ms = int(re.match(r'(-?\d+)', inner).group(1))
                off_min = 0
            return dt.datetime.utcfromtimestamp((ms + off_min * 60000) / 1000.0).date()
        except Exception:
            return None
    s = s.replace("T", " ").split(" ")[0]     # Zeitanteil abschneiden
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%Y.", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def first_field(record, candidates):
    for key in candidates:
        if key in record and record[key] not in (None, ""):
            return record[key]
    # Fallback: case-insensitiver Vergleich
    lower = {k.lower(): v for k, v in record.items()}
    for key in candidates:
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    return None


def months_between(start, end_excl):
    """Nächte je (Jahr, Monat) für das Halb-offene Intervall [start, end_excl)."""
    res = defaultdict(int)
    if end_excl <= start:
        return res
    cur = start
    while cur < end_excl:
        if cur.month == 12:
            next_month = dt.date(cur.year + 1, 1, 1)
        else:
            next_month = dt.date(cur.year, cur.month + 1, 1)
        seg_end = min(next_month, end_excl)
        res[(cur.year, cur.month)] += (seg_end - cur).days
        cur = seg_end
    return res


# ---------------------------------------------------------------------------
# Eingabe: Accounts & Zeitraum
# ---------------------------------------------------------------------------
def load_saved_usernames():
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [str(u) for u in data.get("usernames", [])]
        except Exception:
            return []
    return []


def save_usernames(usernames):
    """Speichert NUR Benutzernamen – niemals Passwörter."""
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"usernames": usernames}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(yellow("Hinweis: Benutzernamen konnten nicht gemerkt werden (%s)." % e))


def prompt_accounts():
    """Fragt bis zu drei Accounts ab. Passwörter bleiben nur im Speicher."""
    saved = load_saved_usernames()
    print(bold("\n=== eVisitor-Accounts ==="))
    print("Passwörter werden NUR im Speicher gehalten und nirgends gespeichert.")
    print("Benutzernamen dürfen optional gemerkt werden. Account leer lassen = überspringen.\n")

    accounts = []
    new_usernames = []
    for i in range(NUM_ACCOUNTS):
        default_user = saved[i] if i < len(saved) else ""
        hint = (" [%s]" % default_user) if default_user else ""
        user = input("Account %d – Benutzername%s: " % (i + 1, hint)).strip()
        if not user and default_user:
            user = default_user
        if not user:
            new_usernames.append("")
            continue
        pw = getpass.getpass("Account %d – Passwort (Eingabe unsichtbar): " % (i + 1))
        if not pw:
            print(yellow("Kein Passwort eingegeben – Account %d wird übersprungen." % (i + 1)))
            new_usernames.append(user)
            continue
        accounts.append({"index": i + 1, "username": user, "password": pw})
        new_usernames.append(user)

    if not accounts:
        fail("Es wurde kein einziger Account mit Passwort eingegeben.")

    ans = input("\nBenutzernamen für nächstes Mal merken? (j/N): ").strip().lower()
    if ans in ("j", "ja", "y", "yes"):
        save_usernames(new_usernames)
        print(green("Benutzernamen gemerkt (Passwörter NICHT)."))
    return accounts


def current_year_range(today):
    return dt.date(today.year, 1, 1), dt.date(today.year, 12, 31)


def prompt_period(args, today):
    """Liefert (date_from, date_to) inklusiv. CLI-Argumente haben Vorrang."""
    if args.year:
        return dt.date(args.year, 1, 1), dt.date(args.year, 12, 31)
    if args.date_from or args.date_to:
        df = parse_date(args.date_from) or current_year_range(today)[0]
        dtx = parse_date(args.date_to) or today
        return df, dtx

    print(bold("\n=== Zeitraum ==="))
    print("  [1] Laufendes Kalenderjahr %d  (Standard)" % today.year)
    print("  [2] Anderes Jahr (Historie)")
    print("  [3] Freier Von-Bis-Bereich")
    choice = input("Auswahl [1]: ").strip() or "1"

    if choice == "2":
        y = input("Jahr (z. B. 2024): ").strip()
        if not y.isdigit():
            fail("Ungültiges Jahr.")
        return dt.date(int(y), 1, 1), dt.date(int(y), 12, 31)
    if choice == "3":
        df = parse_date(input("Von (JJJJ-MM-TT): ").strip())
        dtx = parse_date(input("Bis (JJJJ-MM-TT): ").strip())
        if not df or not dtx:
            fail("Ungültiges Datum. Bitte Format JJJJ-MM-TT verwenden.")
        if dtx < df:
            fail("Das Bis-Datum liegt vor dem Von-Datum.")
        return df, dtx
    return current_year_range(today)


# ---------------------------------------------------------------------------
# eVisitor-API
# ---------------------------------------------------------------------------
def make_opener(insecure):
    cookiejar = http.cookiejar.CookieJar()
    # SECLEVEL=0 akzeptiert den veralteten schwachen DH-Schlüssel von
    # evisitor.hr. Zertifikatsprüfung bleibt aktiv (außer bei --insecure).
    ctx = ssl.create_default_context()
    for spec in ("DEFAULT@SECLEVEL=0", "ALL@SECLEVEL=0"):
        try:
            ctx.set_ciphers(spec)
            break
        except ssl.SSLError:
            continue
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPCookieProcessor(cookiejar),
                urllib.request.HTTPSHandler(context=ctx)]
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [
        ("User-Agent", "eVisitor-Nocenja/1.0 (a-Shell iPad)"),
        ("Accept", "application/json, text/plain, */*"),
    ]
    return opener, cookiejar


# SICHERHEIT: Nur GET (reines Lesen) + der EINE Login-POST erlaubt.
# Datenänderungen gehen in dieser API nur über POST-Aktionen/PUT/DELETE –
# all das wird blockiert. Lesende GETs auf Ressourcen namens "CheckIn"/
# "Prijava" (Gäste-DATEN) sind harmlos und erlaubt.
_FORBIDDEN = ("save", "import", "create", "update", "delete", "insert")


def assert_read_only(req):
    """Harte Sperre: kein Schreiben auf eVisitor (keine Gäste-Anmeldung)."""
    method = req.get_method().upper()
    low = urllib.parse.urlparse(req.full_url).path.rstrip("/").lower()
    if method == "GET":
        for bad in _FORBIDDEN:
            if bad in low:
                raise RuntimeError("SICHERHEIT: verdächtiger Pfad blockiert (%s)." % low)
        return
    if method == "POST" and low.endswith(LOGIN_PATH.rstrip("/").lower()):
        return
    raise RuntimeError("SICHERHEIT: nicht-lesender Zugriff blockiert (%s)." % method)


def _open(opener, req):
    assert_read_only(req)          # Sicherheitssperre vor JEDEM Aufruf
    return opener.open(req, timeout=HTTP_TIMEOUT)


def api_login(opener, root, username, password):
    """Loggt einen Account ein. Cookies landen automatisch im CookieJar.
    Rhetos/AspNetFormsAuth erwartet exakt diese Feldnamen (Großschreibung!)
    und antwortet mit HTTP 200 + Body "true"/"false"."""
    url = root + LOGIN_PATH
    payload = {"userName": username, "password": password, "PersistCookie": False}
    if API_KEY:
        payload["apikey"] = API_KEY
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with _open(opener, req) as resp:
            answer = resp.read(100).decode("utf-8", "replace").strip().lower()
        if answer != "true":
            raise RuntimeError("Login fehlgeschlagen (Benutzername/Passwort falsch).")
        return True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("Login fehlgeschlagen (Benutzername/Passwort falsch).")
        raise RuntimeError("Login-Server antwortete mit HTTP %d." % e.code)
    except urllib.error.URLError as e:
        raise RuntimeError("Keine Verbindung zum Server (%s)." % e.reason)


def api_get_json(opener, root, path, params=None):
    url = root + path
    if params:
        query = "&".join("%s=%s" % (k, urllib.parse.quote(str(v))) for k, v in params.items())
        url += ("&" if "?" in url else "?") + query
    req = urllib.request.Request(url, method="GET")
    try:
        with _open(opener, req) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError("Report-Aufruf HTTP %d bei %s" % (e.code, path))
    except urllib.error.URLError as e:
        raise RuntimeError("Keine Verbindung beim Report-Aufruf (%s)." % e.reason)
    try:
        return json.loads(raw), raw
    except ValueError:
        return None, raw


def extract_records(payload):
    """Findet die Liste der Gäste-Datensätze in verschiedenen Antwort-Strukturen."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("Records", "records", "value", "Value", "data", "Data",
                    "items", "Items", "result", "Result", "rows", "Rows", "d"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        # Manche APIs verschachteln unter "d": {"results": [...]}
        d = payload.get("d")
        if isinstance(d, dict) and isinstance(d.get("results"), list):
            return d["results"]
    return []


def fetch_records(opener, root, date_from, date_to):
    """Holt die Tourist-Anmeldungen im (erweiterten) Zeitraum, seitenweise.
    Gefiltert über den Anreise-Zeitpunkt; Vorlauf-Fenster erfasst
    hineinragende Aufenthalte. Antwortformat {Records:[...]}."""
    lo = (date_from - dt.timedelta(days=REPORT_LOOKBACK_DAYS)).isoformat() + "T00:00:00"
    hi = date_to.isoformat() + "T23:59:59"
    filters = [
        {"Property": ARRIVAL_FIELD, "Operation": "greaterequal", "Value": lo},
        {"Property": ARRIVAL_FIELD, "Operation": "lessequal", "Value": hi},
    ]
    fenc = urllib.parse.quote(json.dumps(filters))
    all_records, last_raw = [], ""
    page, psize = 1, 500
    while True:
        path = "%s?sort=ID&page=%d&psize=%d&filters=%s" % (REPORT_PATH, page, psize, fenc)
        payload, last_raw = api_get_json(opener, root, path)
        recs = extract_records(payload)
        all_records.extend(recs)
        if len(recs) < psize:
            break
        page += 1
        if page > 100:
            break
    return all_records, last_raw


# ---------------------------------------------------------------------------
# Übernachtungs-Berechnung (noćenja)
# ---------------------------------------------------------------------------
def compute_account(records, date_from, date_to, today):
    """
    Zählt Übernachtungen (Nächte) im Zeitraum. Aufenthalte werden auf den
    Zeitraum zugeschnitten; Nacht des Datums D = Nacht von D auf D+1; die
    Abreisenacht zählt nicht (Abreisedatum = exklusiv). Offene Aufenthalte
    (ohne Abmeldung) werden bis heute bzw. Zeitraumende gezählt und separat
    ausgewiesen.
    """
    range_start = date_from
    range_end_excl = date_to + dt.timedelta(days=1)

    total_nights = 0
    open_nights = 0
    guests = 0
    open_guests = 0
    monthly = defaultdict(int)
    skipped = 0

    for rec in records:
        if not isinstance(rec, dict):
            skipped += 1
            continue
        ci = parse_date(first_field(rec, CHECKIN_FIELDS))
        co_raw = first_field(rec, CHECKOUT_FIELDS)
        co = parse_date(co_raw)
        if ci is None:
            skipped += 1
            continue

        is_open = co is None
        if is_open:
            checkout_excl = min(today, range_end_excl)      # heute = obere Schranke
        else:
            checkout_excl = min(co, range_end_excl)         # Abreise = exklusiv

        start = max(ci, range_start)
        if checkout_excl <= start:
            continue

        nights = (checkout_excl - start).days
        if nights <= 0:
            continue

        total_nights += nights
        guests += 1
        for ym, n in months_between(start, checkout_excl).items():
            monthly[ym] += n
        if is_open:
            open_nights += nights
            open_guests += 1

    return {
        "total_nights": total_nights,
        "open_nights": open_nights,
        "guests": guests,
        "open_guests": open_guests,
        "monthly": dict(monthly),
        "records": len(records),
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Ausgabe: Terminal
# ---------------------------------------------------------------------------
def print_terminal(results, date_from, date_to):
    print(bold("\n" + "=" * 58))
    print(bold("  Übernachtungen (noćenja) %s bis %s" %
               (date_from.isoformat(), date_to.isoformat())))
    print(bold("=" * 58))

    header = "%-22s %12s %10s %10s" % ("Account", "Übernacht.", "davon offen", "Gäste")
    print(cyan(header))
    print("-" * 58)

    tot_n = tot_open = tot_g = 0
    for r in results:
        if r.get("error"):
            print("%-22s %s" % (r["username"][:22], red("FEHLER: " + r["error"])))
            continue
        s = r["stats"]
        print("%-22s %12d %10d %10d" %
              (r["username"][:22], s["total_nights"], s["open_nights"], s["guests"]))
        tot_n += s["total_nights"]
        tot_open += s["open_nights"]
        tot_g += s["guests"]

    print("-" * 58)
    print(bold("%-22s %12d %10d %10d" % ("GESAMT", tot_n, tot_open, tot_g)))
    print("\n(\"davon offen\" = Übernachtungen von noch nicht abgemeldeten Gästen,\n"
          " bis heute bzw. Zeitraumende gezählt.)")


# ---------------------------------------------------------------------------
# Ausgabe: HTML-Report (self-contained, mit Monats-Balkendiagramm)
# ---------------------------------------------------------------------------
def build_html(results, date_from, date_to, generated):
    # Monatssummen über alle Accounts sammeln
    all_months = defaultdict(int)
    for r in results:
        if r.get("stats"):
            for ym, n in r["stats"]["monthly"].items():
                all_months[ym] += n
    month_keys = sorted(all_months.keys())
    max_val = max([all_months[m] for m in month_keys], default=0) or 1

    tot_n = sum(r["stats"]["total_nights"] for r in results if r.get("stats"))
    tot_open = sum(r["stats"]["open_nights"] for r in results if r.get("stats"))
    tot_g = sum(r["stats"]["guests"] for r in results if r.get("stats"))

    def esc(t):
        return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    rows = []
    for r in results:
        if r.get("error"):
            rows.append(
                "<tr class='err'><td>%s</td><td colspan='3'>Fehler: %s</td></tr>"
                % (esc(r["username"]), esc(r["error"])))
            continue
        s = r["stats"]
        rows.append(
            "<tr><td>%s</td><td class='num big'>%d</td>"
            "<td class='num'>%d</td><td class='num'>%d</td></tr>"
            % (esc(r["username"]), s["total_nights"], s["open_nights"], s["guests"]))
    rows.append(
        "<tr class='total'><td>GESAMT</td><td class='num big'>%d</td>"
        "<td class='num'>%d</td><td class='num'>%d</td></tr>"
        % (tot_n, tot_open, tot_g))

    bars = []
    for (y, m) in month_keys:
        val = all_months[(y, m)]
        pct = int(round(val / max_val * 100))
        label = "%s %d" % (MONTH_NAMES_DE[m - 1], y)
        bars.append(
            "<div class='barrow'><div class='barlabel'>%s</div>"
            "<div class='bartrack'><div class='bar' style='width:%d%%'>"
            "<span>%d</span></div></div></div>" % (label, pct, val))
    if not bars:
        bars.append("<p class='muted'>Keine Monatsdaten im Zeitraum.</p>")

    return """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>eVisitor Übernachtungen</title>
<style>
  :root{{--bg:#f4f6f9;--card:#fff;--ink:#1c2430;--muted:#6b7685;
        --accent:#2563eb;--accent2:#1d4ed8;--line:#e3e8ef;--total:#eef4ff;}}
  @media (prefers-color-scheme:dark){{
    :root{{--bg:#0f141b;--card:#1a212b;--ink:#e8edf3;--muted:#95a1b0;
          --accent:#3b82f6;--accent2:#60a5fa;--line:#2a333f;--total:#1e2836;}}}}
  *{{box-sizing:border-box}}
  body{{margin:0;font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",
        Roboto,sans-serif;background:var(--bg);color:var(--ink);
        padding:16px;-webkit-text-size-adjust:100%;}}
  .wrap{{max-width:820px;margin:0 auto}}
  h1{{font-size:22px;margin:.2em 0}}
  .sub{{color:var(--muted);margin-bottom:18px}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;
         padding:18px;margin-bottom:18px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
  .kpis{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}}
  .kpi{{flex:1;min-width:150px;background:var(--total);border-radius:12px;
        padding:14px 16px}}
  .kpi .n{{font-size:30px;font-weight:700;color:var(--accent2)}}
  .kpi .l{{color:var(--muted);font-size:13px}}
  table{{width:100%;border-collapse:collapse;font-size:15px}}
  th,td{{padding:12px 10px;text-align:left;border-bottom:1px solid var(--line)}}
  th{{color:var(--muted);font-weight:600;font-size:13px;text-transform:uppercase;
      letter-spacing:.03em}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  td.big{{font-weight:700;font-size:17px;color:var(--accent2)}}
  tr.total td{{background:var(--total);font-weight:700;border-top:2px solid var(--accent)}}
  tr.err td{{color:#c0392b}}
  .barrow{{display:flex;align-items:center;gap:10px;margin:7px 0}}
  .barlabel{{width:74px;font-size:13px;color:var(--muted);flex:none;text-align:right}}
  .bartrack{{flex:1;background:var(--total);border-radius:8px;overflow:hidden;height:26px}}
  .bar{{background:linear-gradient(90deg,var(--accent),var(--accent2));height:100%;
        min-width:2px;border-radius:8px;display:flex;align-items:center;
        justify-content:flex-end;transition:width .3s}}
  .bar span{{color:#fff;font-size:12px;font-weight:600;padding:0 8px}}
  .muted{{color:var(--muted)}}
  footer{{color:var(--muted);font-size:12px;text-align:center;margin-top:8px}}
</style></head><body><div class="wrap">
  <h1>eVisitor – Übernachtungen (noćenja)</h1>
  <div class="sub">Zeitraum {df} bis {dtx}</div>

  <div class="card">
    <div class="kpis">
      <div class="kpi"><div class="n">{tot_n}</div><div class="l">Übernachtungen gesamt</div></div>
      <div class="kpi"><div class="n">{tot_open}</div><div class="l">davon offen (nicht abgemeldet)</div></div>
      <div class="kpi"><div class="n">{tot_g}</div><div class="l">Gäste gesamt</div></div>
    </div>
  </div>

  <div class="card">
    <h2 style="font-size:17px;margin:.1em 0 .6em">Pro Account</h2>
    <table><thead><tr><th>Account</th><th style="text-align:right">Übernachtungen</th>
      <th style="text-align:right">davon offen</th><th style="text-align:right">Gäste</th></tr></thead>
      <tbody>{rows}</tbody></table>
  </div>

  <div class="card">
    <h2 style="font-size:17px;margin:.1em 0 .6em">Übernachtungen pro Monat</h2>
    {bars}
  </div>

  <footer>Erstellt am {gen} · lokal auf dem iPad berechnet · Passwörter wurden nicht gespeichert</footer>
</div></body></html>""".format(
        df=esc(date_from.isoformat()), dtx=esc(date_to.isoformat()),
        tot_n=tot_n, tot_open=tot_open, tot_g=tot_g,
        rows="".join(rows), bars="".join(bars), gen=esc(generated))


def write_report(results, date_from, date_to):
    generated = dt.datetime.now().strftime("%d.%m.%Y %H:%M")
    html = build_html(results, date_from, date_to, generated)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.abspath(REPORT_FILE)


# ---------------------------------------------------------------------------
# Discover-Modus
# ---------------------------------------------------------------------------
def run_discover(root, insecure):
    print(bold("\n=== Discover-Modus ==="))
    print("Loggt EINEN Account ein und zeigt die Roh-Antwort des Report-Endpunkts,\n"
          "damit du REPORT_PATH und die Feldnamen in der Konfiguration setzen kannst.\n")
    user = input("Benutzername: ").strip()
    pw = getpass.getpass("Passwort: ")
    if not user or not pw:
        fail("Benutzername und Passwort erforderlich.")
    opener, _ = make_opener(insecure)
    try:
        api_login(opener, root, user, pw)
        print(green("Login OK."))
    except RuntimeError as e:
        fail(str(e))
    today = dt.date.today()
    df = dt.date(today.year, 1, 1).isoformat() + "T00:00:00"
    path = "%s?sort=ID&page=1&psize=3&filters=%s" % (
        REPORT_PATH,
        urllib.parse.quote(json.dumps(
            [{"Property": ARRIVAL_FIELD, "Operation": "greaterequal", "Value": df}])))
    print("Rufe %s ab ...\n" % REPORT_PATH)
    try:
        _, raw = api_get_json(opener, root, path)
    except RuntimeError as e:
        print(red(str(e)))
        return
    print(bold("--- Roh-Antwort (erste 3000 Zeichen) ---"))
    print(raw[:3000])
    print(bold("\n--- Ende ---"))


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="eVisitor Übernachtungen (noćenja) auswerten.")
    ap.add_argument("--year", type=int, help="Ganzes Kalenderjahr (z. B. 2024).")
    ap.add_argument("--from", dest="date_from", help="Von-Datum JJJJ-MM-TT.")
    ap.add_argument("--to", dest="date_to", help="Bis-Datum JJJJ-MM-TT.")
    ap.add_argument("--test", action="store_true", help="Test-API statt Produktion nutzen.")
    ap.add_argument("--discover", action="store_true", help="Report-Endpunkt erkunden.")
    ap.add_argument("--insecure", action="store_true", help="TLS-Zertifikatsprüfung deaktivieren (nur Notfall).")
    args = ap.parse_args()

    root = API_ROOT_TEST if args.test else API_ROOT_PROD
    today = dt.date.today()

    print(bold("eVisitor Übernachtungs-Auswertung"))
    print("API: %s\n" % root)

    if args.discover:
        run_discover(root, args.insecure)
        return

    accounts = prompt_accounts()
    date_from, date_to = prompt_period(args, today)
    print("\nZeitraum: %s bis %s" % (date_from.isoformat(), date_to.isoformat()))

    results = []
    for acc in accounts:
        label = "Account %d (%s)" % (acc["index"], acc["username"])
        print(cyan("\n> Verarbeite %s ..." % label))
        opener, _ = make_opener(args.insecure)
        try:
            api_login(opener, root, acc["username"], acc["password"])
            print("  Login OK.")
            records, _ = fetch_records(opener, root, date_from, date_to)
            print("  %d Datensätze geladen." % len(records))
            stats = compute_account(records, date_from, date_to, today)
            print(green("  %d Übernachtungen, %d Gäste." %
                        (stats["total_nights"], stats["guests"])))
            results.append({"username": acc["username"], "stats": stats})
        except RuntimeError as e:
            print(red("  " + str(e)))
            results.append({"username": acc["username"],
                            "error": str(e).replace("\n", " ")})
        finally:
            acc["password"] = None   # Passwort so früh wie möglich verwerfen

    print_terminal(results, date_from, date_to)
    path = write_report(results, date_from, date_to)
    print(bold("\nHTML-Report geschrieben:"))
    print("  " + path)
    print("In a-Shell öffnen mit:  " + cyan("open " + REPORT_FILE)
          + "   (oder Datei in Safari öffnen)")


if __name__ == "__main__":
    try:
        import urllib.parse  # noqa: für api_get_json
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(130)
