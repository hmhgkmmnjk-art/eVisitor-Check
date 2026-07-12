#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Übernachtungs-Auswertung – HTML-Oberfläche mit lokalem Proxy
=====================================================================

EINE Datei, EIN Befehl. Startet einen kleinen lokalen Webserver in a-Shell,
liefert die HTML-Oberfläche aus und leitet die eVisitor-API-Aufrufe
serverseitig weiter. Weil die Seite von http://localhost:8080 geladen wird
und ihre Aufrufe an dieselbe Adresse (/api/...) gehen, ist das für Safari
"same-origin" -> KEIN CORS, KEINE Cookie-Blockade. Die eigentlichen Aufrufe
an evisitor.hr macht Python (kennt kein CORS) mit nativem Cookie-Handling.

Nutzung (in der iPad-App a-Shell):
    python3 evisitor_proxy.py
Dann in Safari öffnen:  http://localhost:8080

Optionen:
    python3 evisitor_proxy.py --port 8080     # anderen Port wählen
    python3 evisitor_proxy.py --test          # eVisitor-Test-API nutzen
    python3 evisitor_proxy.py --insecure      # TLS-Prüfung aus (nur Notfall)

Nur Standardbibliothek – läuft in a-Shell ohne "pip install".
Passwörter laufen ausschließlich über localhost, werden nie gespeichert.
"""

import sys
import re
import json
import argparse
import datetime as dt
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
import http.server
import socketserver
import ssl
from collections import defaultdict

# ---------------------------------------------------------------------------
# KONFIGURATION  (identisch zu evisitor_nocenja.py)
# ---------------------------------------------------------------------------
API_ROOT_PROD = "https://www.evisitor.hr/eVisitorRhetos_API"   # Produktion (kein apikey)
API_ROOT_TEST = "https://www.evisitor.hr/testApi"             # Test (braucht apikey)
LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login"
# API-Schlüssel nur für die Testplattform nötig; Produktion braucht keinen.
API_KEY = ""

# eVisitor-Ressource für Gäste-/Aufenthaltsdaten (per Diagnose bestätigt):
REPORT_PATH = "/Rest/Htz/Tourist/"
ARRIVAL_FIELD = "TimeStayFrom"         # Anreise-Zeitpunkt (Filterfeld)
# Aufenthalte, die vor dem Zeitraum begannen und hineinragen, werden über
# ein Vorlauf-Fenster mitgeholt und clientseitig auf den Zeitraum zugeschnitten:
REPORT_LOOKBACK_DAYS = 370

CHECKIN_FIELDS = ["TimeStayFrom", "StayFrom", "DatumDolaska"]
CHECKOUT_FIELDS = ["CheckOutTime", "CheckOutDate", "DatumOdlaska"]
# Namensfelder für die Gästeliste:
FIRSTNAME_FIELDS = ["TouristName", "Ime", "ime", "FirstName"]
LASTNAME_FIELDS = ["TouristSurname", "Prezime", "prezime", "LastName"]
NAME_FIELDS = ["ImePrezime", "Naziv", "Name"]

# SICHERHEIT: Nur GET (reines Lesen) + der EINE Login-POST sind erlaubt.
# Datenänderungen sind in dieser API ausschließlich über POST-Aktionen/PUT/
# DELETE möglich – all das wird blockiert. Die Gäste-DATEN-Ressourcen heißen
# teils "CheckIn"/"Prijava" – lesende GETs darauf sind harmlos und erlaubt.
FORBIDDEN = ("save", "import", "create", "update", "delete", "insert")

HTTP_TIMEOUT = 20   # zügiges Timeout, damit die Abfrage bei falscher Adresse nicht hängt

# Zur Laufzeit gesetzt (aus Kommandozeile):
API_ROOT = API_ROOT_PROD
INSECURE = False


# ---------------------------------------------------------------------------
# Datum- & Berechnungslogik  (getestet, aus evisitor_nocenja.py)
# ---------------------------------------------------------------------------
def parse_date(value):
    if value is None:
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    if s.startswith("/Date(") and s.endswith(")/"):
        # .NET-JSON-Datum, z. B. /Date(1725208200000+0200)/ – Offset auf die
        # lokale Kalenderdatum-Bestimmung anwenden (sonst kippt Mitternacht).
        try:
            inner = s[6:-2]
            m = re.match(r'(-?\d+)([+-]\d{2})(\d{2})', inner)
            if m:
                ms = int(m.group(1))
                oh = int(m.group(2))
                om = int(m.group(3))
                off_min = (abs(oh) * 60 + om) * (1 if oh >= 0 else -1)
            else:
                ms = int(re.match(r'(-?\d+)', inner).group(1))
                off_min = 0
            return dt.datetime.utcfromtimestamp((ms + off_min * 60000) / 1000.0).date()
        except Exception:
            return None
    s = s.replace("T", " ").split(" ")[0]
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
    lower = {k.lower(): v for k, v in record.items()}
    for key in candidates:
        if key.lower() in lower and lower[key.lower()] not in (None, ""):
            return lower[key.lower()]
    return None


def months_between(start, end_excl):
    res = defaultdict(int)
    if end_excl <= start:
        return res
    cur = start
    while cur < end_excl:
        if cur.month == 12:
            nxt = dt.date(cur.year + 1, 1, 1)
        else:
            nxt = dt.date(cur.year, cur.month + 1, 1)
        seg_end = min(nxt, end_excl)
        res[(cur.year, cur.month)] += (seg_end - cur).days
        cur = seg_end
    return res


def guest_name(rec):
    """Anzeigename aus verschiedenen Feldvarianten zusammensetzen."""
    full = first_field(rec, NAME_FIELDS)
    if full:
        return str(full)
    vor = first_field(rec, FIRSTNAME_FIELDS)
    nach = first_field(rec, LASTNAME_FIELDS)
    name = " ".join(str(x) for x in (vor, nach) if x)
    return name or "(ohne Namen)"


def compute_account(records, date_from, date_to, today):
    range_start = date_from
    range_end_excl = date_to + dt.timedelta(days=1)
    total_nights = open_nights = guests = open_guests = 0
    monthly = defaultdict(int)
    guest_list = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ci = parse_date(first_field(rec, CHECKIN_FIELDS))
        co = parse_date(first_field(rec, CHECKOUT_FIELDS))
        if ci is None:
            continue
        is_open = co is None
        checkout_excl = min(today, range_end_excl) if is_open else min(co, range_end_excl)
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
        guest_list.append({
            "name": guest_name(rec),
            "checkin": ci.isoformat(),
            "checkout": (None if is_open else co.isoformat()),
            "nights": nights,
            "open": is_open,
        })
    guest_list.sort(key=lambda g: g["checkin"])
    monthly_json = {"%04d-%02d" % (y, m): n for (y, m), n in monthly.items()}
    return {
        "total_nights": total_nights, "open_nights": open_nights,
        "guests": guests, "open_guests": open_guests,
        "monthly": monthly_json, "records": len(records),
        "guest_list": guest_list,
    }


# ---------------------------------------------------------------------------
# eVisitor-API-Aufrufe (serverseitig, mit Cookies)
# ---------------------------------------------------------------------------
def assert_read_only(req):
    """Harte Sperre: nur GET, plus der EINE Login-POST. Sonst Abbruch.
    Garantiert: keine Gäste-Anmeldung, kein Schreiben auf eVisitor."""
    method = req.get_method().upper()
    low = urllib.parse.urlparse(req.full_url).path.rstrip("/").lower()
    if method == "GET":
        for bad in FORBIDDEN:
            if bad in low:
                raise RuntimeError("SICHERHEIT: verdächtiger Pfad blockiert (%s)." % low)
        return
    if method == "POST" and low.endswith(LOGIN_PATH.rstrip("/").lower()):
        return
    raise RuntimeError("SICHERHEIT: nicht-lesender Zugriff blockiert (%s)." % method)


def do(opener, req):
    assert_read_only(req)          # Sicherheitssperre vor JEDEM Aufruf
    return opener.open(req, timeout=HTTP_TIMEOUT)


def make_ssl_context():
    """Akzeptiert den veralteten schwachen DH-Schlüssel von evisitor.hr
    (SECLEVEL=0). Zertifikatsprüfung bleibt aktiv (außer bei --insecure)."""
    ctx = ssl.create_default_context()
    for spec in ("DEFAULT@SECLEVEL=0", "ALL@SECLEVEL=0"):
        try:
            ctx.set_ciphers(spec)
            break
        except ssl.SSLError:
            continue
    if INSECURE:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def make_opener():
    cj = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(cj),
                urllib.request.HTTPSHandler(context=make_ssl_context())]
    op = urllib.request.build_opener(*handlers)
    op.addheaders = [("User-Agent", "eVisitor-Nocenja/1.0 (a-Shell)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op


def api_login(opener, username, password):
    # Rhetos/AspNetFormsAuth: Felder klein (userName/password), Antwort
    # HTTP 200 + Body "true"/"false". apikey nur auf der Testplattform.
    payload = {"userName": username, "password": password, "PersistCookie": False}
    if API_KEY:
        payload["apikey"] = API_KEY
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(API_ROOT + LOGIN_PATH, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with do(opener, req) as resp:
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


def extract_records(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("Records", "records", "value", "Value", "data", "Data",
                    "items", "Items", "result", "Result", "rows", "Rows", "d"):
            if isinstance(payload.get(key), list):
                return payload[key]
        d = payload.get("d")
        if isinstance(d, dict) and isinstance(d.get("results"), list):
            return d["results"]
    return []


def fetch_records(opener, date_from, date_to):
    """Holt die Tourist-Anmeldungen im (erweiterten) Zeitraum, seitenweise.
    Gefiltert wird über den Anreise-Zeitpunkt; ein Vorlauf-Fenster fängt
    Aufenthalte ab, die vor dem Zeitraum begannen und hineinragen."""
    lo = (date_from - dt.timedelta(days=REPORT_LOOKBACK_DAYS)).isoformat() + "T00:00:00"
    hi = date_to.isoformat() + "T23:59:59"
    filters = [
        {"Property": ARRIVAL_FIELD, "Operation": "greaterequal", "Value": lo},
        {"Property": ARRIVAL_FIELD, "Operation": "lessequal", "Value": hi},
    ]
    fenc = urllib.parse.quote(json.dumps(filters))
    all_records = []
    page, psize = 1, 500
    while True:
        url = "%s%s?sort=ID&page=%d&psize=%d&filters=%s" % (
            API_ROOT, REPORT_PATH, page, psize, fenc)
        req = urllib.request.Request(url, method="GET")
        try:
            with do(opener, req) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise RuntimeError("Report-Aufruf HTTP %d." % e.code)
        except urllib.error.URLError as e:
            raise RuntimeError("Keine Verbindung beim Report-Aufruf (%s)." % e.reason)
        try:
            payload = json.loads(raw)
        except ValueError:
            raise RuntimeError("Report-Antwort ist kein JSON.")
        recs = extract_records(payload)
        all_records.extend(recs)
        if len(recs) < psize:
            break
        page += 1
        if page > 100:      # Sicherheitsgrenze (max. 50.000 Datensätze)
            break
    return all_records


def run_account(username, password, date_from, date_to, today):
    opener = make_opener()
    api_login(opener, username, password)
    records = fetch_records(opener, date_from, date_to)
    return compute_account(records, date_from, date_to, today)


# ---------------------------------------------------------------------------
# HTTP-Server:  /  -> HTML,   /api/run -> Abfrage
# ---------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # ruhiges Terminal

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, HTML_PAGE, "text/html; charset=utf-8")
        elif path == "/health":
            self._send(200, json.dumps({"ok": True, "api": API_ROOT}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/run":
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send(400, json.dumps({"error": "Ungültige Anfrage.", "code": "bad_request"}))
            return

        date_from = parse_date(payload.get("date_from"))
        date_to = parse_date(payload.get("date_to"))
        accounts = payload.get("accounts") or []
        if not date_from or not date_to:
            self._send(400, json.dumps({"error": "Zeitraum fehlt oder ungültig.",
                                        "code": "period_missing"}))
            return
        if date_to < date_from:
            self._send(400, json.dumps({"error": "Bis-Datum liegt vor Von-Datum.",
                                        "code": "period_order"}))
            return

        today = dt.date.today()
        results = []
        for i, acc in enumerate(accounts, 1):
            user = (acc.get("username") or "").strip()
            pw = acc.get("password") or ""
            if not user or not pw:
                continue
            entry = {"index": i, "username": user}
            try:
                entry["stats"] = run_account(user, pw, date_from, date_to, today)
                entry["ok"] = True
            except RuntimeError as e:
                entry["ok"] = False
                entry["error"] = str(e).replace("\n", " ")
                entry["code"] = err_code(entry["error"])
            except Exception as e:
                entry["ok"] = False
                entry["error"] = "Unerwarteter Fehler: %s" % e
                entry["code"] = "generic"
            results.append(entry)

        if not results:
            self._send(400, json.dumps(
                {"error": "Kein Account mit Benutzername UND Passwort angegeben.",
                 "code": "no_account"}))
            return

        self._send(200, json.dumps({
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "results": results,
        }, ensure_ascii=False))


def err_code(msg):
    """Ordnet einer deutschen Fehlermeldung einen Sprach-Code zu (für DE/HR)."""
    m = (msg or "").lower()
    if "login fehlgeschlagen" in m:
        return "login_failed"
    if "keine verbindung zum server" in m:
        return "no_connection"
    if "login-server" in m:
        return "login_http"
    if "report-aufruf http" in m:
        return "report_http"
    if "keine verbindung beim report" in m:
        return "report_conn"
    if "kein json" in m:
        return "report_json"
    if "sicherheit" in m:
        return "security"
    return "generic"


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# HTML-Oberfläche (wird als statische Seite ausgeliefert; JS erledigt alles)
# ---------------------------------------------------------------------------
HTML_PAGE = r"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>eVisitor Übernachtungen</title>
<style>
  :root{--bg:#f4f6f9;--card:#fff;--ink:#1c2430;--muted:#6b7685;--accent:#2563eb;
        --accent2:#1d4ed8;--line:#e3e8ef;--total:#eef4ff;--err:#c0392b;--ok:#15803d;}
  @media (prefers-color-scheme:dark){
    :root{--bg:#0f141b;--card:#1a212b;--ink:#e8edf3;--muted:#95a1b0;--accent:#3b82f6;
          --accent2:#60a5fa;--line:#2a333f;--total:#1e2836;--err:#f87171;--ok:#4ade80;}}
  *{box-sizing:border-box}
  html{-webkit-text-size-adjust:100%}
  body{margin:0;font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       background:var(--bg);color:var(--ink);padding:16px;padding:max(16px,env(safe-area-inset-top)) 16px 40px;}
  .wrap{max-width:860px;margin:0 auto}
  h1{font-size:23px;margin:.1em 0}
  .sub{color:var(--muted);margin-bottom:18px;font-size:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;
        padding:18px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
  h2{font-size:17px;margin:.1em 0 .7em}
  label{display:block;font-size:13px;color:var(--muted);margin:0 0 4px}
  .acc{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
  .acc .tag{grid-column:1/-1;font-weight:600;color:var(--ink);font-size:14px;margin-top:4px}
  input[type=text],input[type=password],input[type=number],input[type=date],select{
    width:100%;padding:13px 12px;font-size:16px;border:1px solid var(--line);
    border-radius:11px;background:var(--bg);color:var(--ink);-webkit-appearance:none;}
  input:focus,select:focus{outline:2px solid var(--accent);border-color:var(--accent)}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  .seg{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
  .seg button{flex:1;min-width:130px;padding:12px;border-radius:11px;border:1px solid var(--line);
    background:var(--bg);color:var(--ink);font-size:15px;font-weight:600}
  .seg button.active{background:var(--accent);color:#fff;border-color:var(--accent)}
  .chk{display:flex;align-items:center;gap:9px;margin-top:6px;color:var(--muted);font-size:14px}
  .chk input{width:22px;height:22px}
  .go{width:100%;padding:16px;font-size:18px;font-weight:700;color:#fff;border:none;
      border-radius:13px;background:linear-gradient(90deg,var(--accent),var(--accent2));margin-top:4px}
  .go:disabled{opacity:.6}
  .hidden{display:none}
  .msg{padding:12px 14px;border-radius:11px;margin-bottom:14px;font-size:14px}
  .msg.error{background:rgba(192,57,43,.12);color:var(--err);border:1px solid rgba(192,57,43,.3)}
  .kpis{display:flex;gap:12px;flex-wrap:wrap}
  .kpi{flex:1;min-width:150px;background:var(--total);border-radius:13px;padding:14px 16px}
  .kpi .n{font-size:30px;font-weight:700;color:var(--accent2)}
  .kpi .l{color:var(--muted);font-size:13px}
  table{width:100%;border-collapse:collapse;font-size:15px}
  th,td{padding:12px 8px;text-align:left;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  td.big{font-weight:700;font-size:17px;color:var(--accent2)}
  tr.total td{background:var(--total);font-weight:700;border-top:2px solid var(--accent)}
  tr.err td{color:var(--err)}
  .barrow{display:flex;align-items:center;gap:10px;margin:7px 0}
  .barlabel{width:76px;font-size:13px;color:var(--muted);flex:none;text-align:right}
  .bartrack{flex:1;background:var(--total);border-radius:8px;overflow:hidden;height:26px}
  .bar{background:linear-gradient(90deg,var(--accent),var(--accent2));height:100%;min-width:2px;
       border-radius:8px;display:flex;align-items:center;justify-content:flex-end}
  .bar span{color:#fff;font-size:12px;font-weight:600;padding:0 8px}
  .spin{display:inline-block;width:20px;height:20px;border:3px solid rgba(255,255,255,.4);
        border-top-color:#fff;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-4px;margin-right:8px}
  @keyframes sp{to{transform:rotate(360deg)}}
  footer{color:var(--muted);font-size:12px;text-align:center;margin-top:6px}
  .note{font-size:12px;color:var(--muted);margin-top:8px}
  details{border:1px solid var(--line);border-radius:11px;margin-bottom:10px;overflow:hidden}
  summary{padding:12px 14px;cursor:pointer;font-weight:600;background:var(--total);
          list-style:none;display:flex;justify-content:space-between;gap:10px}
  summary::-webkit-details-marker{display:none}
  summary .badge{font-weight:400;color:var(--muted);font-size:13px}
  .gtable{font-size:14px}
  .gtable td,.gtable th{padding:9px 8px}
  .tagopen{display:inline-block;background:rgba(37,99,235,.15);color:var(--accent2);
           border-radius:6px;padding:1px 7px;font-size:12px;font-weight:600}
  .hdr{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:4px}
  .lang{display:flex;gap:4px;flex:none}
  .lang button{padding:7px 11px;border-radius:9px;border:1px solid var(--line);
    background:var(--bg);color:var(--ink);font-size:13px;font-weight:700;min-width:40px}
  .lang button.active{background:var(--accent);color:#fff;border-color:var(--accent)}
</style></head><body><div class="wrap">
  <div class="hdr">
    <div>
      <h1 data-i18n="title">eVisitor</h1>
      <div class="sub" data-i18n="sub"></div>
    </div>
    <div class="lang">
      <button id="langDe" class="active">DE</button>
      <button id="langHr">HR</button>
    </div>
  </div>

  <div id="err" class="msg error hidden"></div>

  <div class="card">
    <h2 data-i18n="accounts"></h2>
    <div id="accounts"></div>
    <label class="chk"><input type="checkbox" id="remember"> <span data-i18n="remember"></span></label>
  </div>

  <div class="card">
    <h2 data-i18n="period"></h2>
    <div class="seg" id="seg">
      <button data-mode="year" class="active" data-i18n="curYear"></button>
      <button data-mode="pastyear" data-i18n="otherYear"></button>
      <button data-mode="range" data-i18n="range"></button>
    </div>
    <div id="pastyearBox" class="hidden">
      <label data-i18n="year"></label>
      <select id="yearSel"></select>
    </div>
    <div id="rangeBox" class="hidden">
      <div class="acc">
        <div><label data-i18n="from"></label><input type="date" id="dFrom"></div>
        <div><label data-i18n="to"></label><input type="date" id="dTo"></div>
      </div>
    </div>
    <div class="note" id="periodInfo"></div>
  </div>

  <button class="go" id="go" data-i18n="go"></button>

  <div id="results" class="hidden">
    <div class="card">
      <h2><span data-i18n="result"></span> <span id="periodLabel" style="font-weight:400;color:var(--muted)"></span></h2>
      <div class="kpis">
        <div class="kpi"><div class="n" id="kNights">0</div><div class="l" data-i18n="kpiNights"></div></div>
        <div class="kpi"><div class="n" id="kOpen">0</div><div class="l" data-i18n="kpiOpen"></div></div>
        <div class="kpi"><div class="n" id="kGuests">0</div><div class="l" data-i18n="kpiGuests"></div></div>
      </div>
    </div>
    <div class="card">
      <h2 data-i18n="perAccount"></h2>
      <table><thead><tr><th data-i18n="thAccount"></th><th style="text-align:right" data-i18n="thNights"></th>
        <th style="text-align:right" data-i18n="thOpen"></th><th style="text-align:right" data-i18n="thGuests"></th></tr></thead>
        <tbody id="accRows"></tbody></table>
    </div>
    <div class="card">
      <h2 data-i18n="perMonth"></h2>
      <div id="bars"></div>
    </div>
    <div class="card">
      <h2 data-i18n="guestsInPeriod"></h2>
      <div id="guests"></div>
    </div>
    <footer id="footer"></footer>
  </div>
</div>
<script>
"use strict";

// --- Übersetzungen ---
var I18N={
 de:{title:"eVisitor – Übernachtungen (noćenja)",
     sub:"Läuft lokal auf dem iPad · Passwörter werden nicht gespeichert",
     accounts:"Accounts", account:"Account", user:"Benutzername", pass:"Passwort",
     remember:"Benutzernamen merken (nur Namen, keine Passwörter)",
     period:"Zeitraum", curYear:"Laufendes Jahr", otherYear:"Anderes Jahr", range:"Von–Bis",
     year:"Jahr", from:"Von", to:"Bis", go:"Übernachtungen abfragen",
     loading:"Abfrage läuft …", result:"Ergebnis",
     kpiNights:"Übernachtungen gesamt", kpiOpen:"davon offen (nicht abgemeldet)", kpiGuests:"Gäste gesamt",
     perAccount:"Pro Account", thAccount:"Account", thNights:"Übernacht.", thOpen:"davon offen", thGuests:"Gäste",
     total:"GESAMT", perMonth:"Übernachtungen pro Monat", guestsInPeriod:"Angemeldete Gäste im Zeitraum",
     thGuest:"Gast", thArrival:"Anreise", thDeparture:"Abreise", thNightsShort:"Nächte",
     open:"offen", guestsWord:"Gäste", nightsWord:"Nächte",
     noGuests:"Keine Gäste im Zeitraum.", noGuestData:"Keine Gästedaten.",
     noMonthData:"Keine Monatsdaten im Zeitraum.",
     query:"Abfrage:", between:"bis", pickPeriod:"Bitte Von- und Bis-Datum wählen.",
     footerTxt:"lokal auf dem iPad berechnet", errWord:"Fehler",
     v_account:"Bitte für mindestens einen Account Benutzername UND Passwort eingeben.",
     v_period:"Bitte einen gültigen Zeitraum wählen.",
     v_order:"Das Bis-Datum liegt vor dem Von-Datum.",
     e_conn:"Keine Verbindung zum lokalen Server. Läuft evisitor_proxy.py noch in a-Shell?",
     e_query:"Fehler bei der Abfrage.",
     err_login_failed:"Login fehlgeschlagen (Benutzername/Passwort falsch).",
     err_no_connection:"Keine Verbindung zum Server.",
     err_login_http:"Login-Server-Fehler.", err_report_http:"Fehler beim Datenabruf.",
     err_report_conn:"Keine Verbindung beim Datenabruf.", err_report_json:"Ungültige Server-Antwort.",
     err_security:"Sicherheitssperre ausgelöst.", err_bad_request:"Ungültige Anfrage.",
     err_period_missing:"Zeitraum fehlt oder ungültig.", err_period_order:"Bis-Datum liegt vor Von-Datum.",
     err_no_account:"Kein Account mit Benutzername UND Passwort angegeben.", err_generic:"Fehler."},
 hr:{title:"eVisitor – Noćenja",
     sub:"Radi lokalno na iPadu · lozinke se ne spremaju",
     accounts:"Računi", account:"Račun", user:"Korisničko ime", pass:"Lozinka",
     remember:"Zapamti korisnička imena (samo imena, ne lozinke)",
     period:"Razdoblje", curYear:"Tekuća godina", otherYear:"Druga godina", range:"Od–Do",
     year:"Godina", from:"Od", to:"Do", go:"Dohvati noćenja",
     loading:"Dohvaćanje …", result:"Rezultat",
     kpiNights:"Ukupno noćenja", kpiOpen:"od toga otvoreno (bez odjave)", kpiGuests:"Ukupno gostiju",
     perAccount:"Po računu", thAccount:"Račun", thNights:"Noćenja", thOpen:"otvoreno", thGuests:"Gosti",
     total:"UKUPNO", perMonth:"Noćenja po mjesecu", guestsInPeriod:"Prijavljeni gosti u razdoblju",
     thGuest:"Gost", thArrival:"Dolazak", thDeparture:"Odlazak", thNightsShort:"Noći",
     open:"otvoreno", guestsWord:"gostiju", nightsWord:"noći",
     noGuests:"Nema gostiju u razdoblju.", noGuestData:"Nema podataka o gostima.",
     noMonthData:"Nema mjesečnih podataka u razdoblju.",
     query:"Upit:", between:"do", pickPeriod:"Odaberite datum Od i Do.",
     footerTxt:"izračunato lokalno na iPadu", errWord:"Greška",
     v_account:"Unesite korisničko ime I lozinku za barem jedan račun.",
     v_period:"Odaberite ispravno razdoblje.",
     v_order:"Datum Do je prije datuma Od.",
     e_conn:"Nema veze s lokalnim poslužiteljem. Radi li evisitor_proxy.py još u a-Shellu?",
     e_query:"Greška pri dohvaćanju.",
     err_login_failed:"Prijava nije uspjela (pogrešno korisničko ime/lozinka).",
     err_no_connection:"Nema veze s poslužiteljem.",
     err_login_http:"Greška poslužitelja pri prijavi.", err_report_http:"Greška pri dohvaćanju podataka.",
     err_report_conn:"Nema veze pri dohvaćanju podataka.", err_report_json:"Neispravan odgovor poslužitelja.",
     err_security:"Sigurnosna blokada aktivirana.", err_bad_request:"Neispravan zahtjev.",
     err_period_missing:"Razdoblje nedostaje ili nije ispravno.", err_period_order:"Datum Do je prije datuma Od.",
     err_no_account:"Nije unesen račun s korisničkim imenom I lozinkom.", err_generic:"Greška."}
};
var MONTHS={de:["Jan","Feb","Mär","Apr","Mai","Jun","Jul","Aug","Sep","Okt","Nov","Dez"],
            hr:["Sij","Velj","Ožu","Tra","Svi","Lip","Srp","Kol","Ruj","Lis","Stu","Pro"]};

var LS_KEY="evisitor_usernames", LS_LANG="evisitor_lang";
var mode="year", LAST=null;
var lang="de";
try{lang=localStorage.getItem(LS_LANG)||"de";}catch(e){}
if(lang!=="hr") lang="de";

function el(id){return document.getElementById(id);}
function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function todayISO(){return new Date().toISOString().slice(0,10);}
function pad(n){return (n<10?"0":"")+n;}
function t(k){return (I18N[lang]&&I18N[lang][k])||I18N.de[k]||k;}
function tr(k){return (I18N[lang]&&I18N[lang][k])||I18N.de[k]||null;} // null falls unbekannt

// --- Account-Felder aufbauen (Werte bleiben bei Sprachwechsel erhalten) ---
(function buildAccounts(){
  var saved=[];
  try{saved=JSON.parse(localStorage.getItem(LS_KEY)||"[]");}catch(e){}
  var box=el("accounts"),html="";
  for(var i=0;i<3;i++){
    var u=saved[i]?esc(saved[i]):"";
    html+='<div class="acc"><div class="tag" data-acctag></div>'+
      '<div><label data-i18n="user"></label><input type="text" autocomplete="off" '+
      'autocapitalize="none" spellcheck="false" id="u'+i+'" value="'+u+'"></div>'+
      '<div><label data-i18n="pass"></label><input type="password" autocomplete="off" id="p'+i+'"></div></div>';
  }
  box.innerHTML=html;
  if(saved.length) el("remember").checked=true;
})();

// --- Jahr-Dropdown ---
(function buildYears(){
  var y=new Date().getFullYear(),sel=el("yearSel"),h="";
  for(var yy=y;yy>=y-8;yy--) h+='<option value="'+yy+'">'+yy+'</option>';
  sel.innerHTML=h; sel.value=y-1;
})();

// --- Sprache anwenden ---
function applyLang(){
  document.documentElement.setAttribute("lang",lang);
  document.title=t("title");
  var nodes=document.querySelectorAll("[data-i18n]");
  Array.prototype.forEach.call(nodes,function(n){n.textContent=t(n.getAttribute("data-i18n"));});
  var tags=document.querySelectorAll("[data-acctag]");
  Array.prototype.forEach.call(tags,function(n,i){n.textContent=t("account")+" "+(i+1);});
  el("langDe").classList.toggle("active",lang==="de");
  el("langHr").classList.toggle("active",lang==="hr");
  updateInfo();
  if(LAST) render(LAST);
}
function setLang(l){lang=(l==="hr")?"hr":"de";try{localStorage.setItem(LS_LANG,lang);}catch(e){}applyLang();}
el("langDe").addEventListener("click",function(){setLang("de");});
el("langHr").addEventListener("click",function(){setLang("hr");});

// --- Zeitraum-Umschalter ---
el("seg").addEventListener("click",function(e){
  var b=e.target.closest("button"); if(!b) return;
  mode=b.getAttribute("data-mode");
  Array.prototype.forEach.call(el("seg").children,function(x){x.classList.remove("active");});
  b.classList.add("active");
  el("pastyearBox").classList.toggle("hidden",mode!=="pastyear");
  el("rangeBox").classList.toggle("hidden",mode!=="range");
  updateInfo();
});
["change","input"].forEach(function(ev){
  el("yearSel").addEventListener(ev,updateInfo);
  el("dFrom").addEventListener(ev,updateInfo);
  el("dTo").addEventListener(ev,updateInfo);
});

function currentPeriod(){
  var y=new Date().getFullYear();
  if(mode==="year") return {from:y+"-01-01",to:y+"-12-31"};
  if(mode==="pastyear"){var yy=el("yearSel").value;return {from:yy+"-01-01",to:yy+"-12-31"};}
  return {from:el("dFrom").value,to:el("dTo").value};
}
function updateInfo(){
  var p=currentPeriod();
  el("periodInfo").textContent = p.from&&p.to
    ? (t("query")+" "+p.from+" "+t("between")+" "+p.to) : t("pickPeriod");
}
(function(){var y=new Date().getFullYear();el("dFrom").value=y+"-01-01";el("dTo").value=todayISO();})();

function showError(msg){el("err").textContent=msg;el("err").classList.remove("hidden");window.scrollTo(0,0);}
function clearError(){el("err").classList.add("hidden");}
function serverMsg(body){
  if(body&&body.code&&tr("err_"+body.code)) return tr("err_"+body.code);
  if(body&&body.error) return body.error;
  return t("e_query");
}

// --- Abfrage senden ---
el("go").addEventListener("click",function(){
  clearError();
  var accounts=[];
  for(var i=0;i<3;i++){
    var u=el("u"+i).value.trim(),p=el("p"+i).value;
    if(u&&p) accounts.push({username:u,password:p});
  }
  if(!accounts.length){showError(t("v_account"));return;}

  var p=currentPeriod();
  if(!p.from||!p.to){showError(t("v_period"));return;}
  if(p.to<p.from){showError(t("v_order"));return;}

  if(el("remember").checked){
    var names=[]; for(var j=0;j<3;j++) names.push(el("u"+j).value.trim());
    try{localStorage.setItem(LS_KEY,JSON.stringify(names));}catch(e){}
  } else { try{localStorage.removeItem(LS_KEY);}catch(e){} }

  var btn=el("go"); btn.disabled=true;
  btn.innerHTML='<span class="spin"></span>'+t("loading")+" ("+accounts.length+")";

  fetch("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({accounts:accounts,date_from:p.from,date_to:p.to})})
  .then(function(r){return r.json().then(function(j){return {status:r.status,body:j};});})
  .then(function(res){
    if(res.status!==200){showError(serverMsg(res.body));return;}
    LAST=res.body; render(res.body);
  })
  .catch(function(e){showError(t("e_conn")+" ("+e+")");})
  .finally(function(){btn.disabled=false;btn.textContent=t("go");
    for(var i=0;i<3;i++) el("p"+i).value="";});
});

function render(data){
  var totN=0,totOpen=0,totG=0,months={};
  var rows="";
  data.results.forEach(function(r){
    if(!r.ok){
      var emsg=(r.code&&tr("err_"+r.code))||r.error||"";
      rows+='<tr class="err"><td>'+esc(r.username)+'</td><td colspan="3">'+esc(t("errWord")+": "+emsg)+'</td></tr>';
      return;
    }
    var s=r.stats;
    totN+=s.total_nights;totOpen+=s.open_nights;totG+=s.guests;
    for(var k in s.monthly){months[k]=(months[k]||0)+s.monthly[k];}
    rows+='<tr><td>'+esc(r.username)+'</td><td class="num big">'+s.total_nights+
          '</td><td class="num">'+s.open_nights+'</td><td class="num">'+s.guests+'</td></tr>';
  });
  rows+='<tr class="total"><td>'+t("total")+'</td><td class="num big">'+totN+
        '</td><td class="num">'+totOpen+'</td><td class="num">'+totG+'</td></tr>';
  el("accRows").innerHTML=rows;
  el("kNights").textContent=totN;el("kOpen").textContent=totOpen;el("kGuests").textContent=totG;
  el("periodLabel").textContent="("+data.date_from+" "+t("between")+" "+data.date_to+")";

  // Balkendiagramm
  var keys=Object.keys(months).sort();
  var max=0; keys.forEach(function(k){if(months[k]>max)max=months[k];}); if(!max)max=1;
  var bars="";
  keys.forEach(function(k){
    var parts=k.split("-"),lab=MONTHS[lang][parseInt(parts[1],10)-1]+" "+parts[0];
    var pct=Math.round(months[k]/max*100);
    bars+='<div class="barrow"><div class="barlabel">'+lab+'</div>'+
      '<div class="bartrack"><div class="bar" style="width:'+pct+'%"><span>'+months[k]+'</span></div></div></div>';
  });
  el("bars").innerHTML=bars||'<p class="note">'+t("noMonthData")+'</p>';

  // Gästeliste pro Account (aufklappbar)
  var gh="";
  data.results.forEach(function(r){
    if(!r.ok){return;}
    var list=r.stats.guest_list||[];
    var rowsG="";
    list.forEach(function(g){
      var co = g.checkout ? g.checkout : '<span class="tagopen">'+t("open")+'</span>';
      rowsG+='<tr><td>'+esc(g.name)+'</td><td>'+g.checkin+'</td><td>'+co+
             '</td><td class="num">'+g.nights+'</td></tr>';
    });
    if(!rowsG) rowsG='<tr><td colspan="4" class="note">'+t("noGuests")+'</td></tr>';
    gh+='<details><summary><span>'+esc(r.username)+'</span>'+
        '<span class="badge">'+list.length+' '+t("guestsWord")+' · '+r.stats.total_nights+' '+t("nightsWord")+'</span></summary>'+
        '<table class="gtable"><thead><tr><th>'+t("thGuest")+'</th><th>'+t("thArrival")+'</th>'+
        '<th>'+t("thDeparture")+'</th><th style="text-align:right">'+t("thNightsShort")+'</th></tr></thead>'+
        '<tbody>'+rowsG+'</tbody></table></details>';
  });
  el("guests").innerHTML=gh||'<p class="note">'+t("noGuestData")+'</p>';

  var d=new Date();
  el("footer").textContent=pad(d.getDate())+"."+pad(d.getMonth()+1)+"."+d.getFullYear()+
    " "+pad(d.getHours())+":"+pad(d.getMinutes())+" · "+t("footerTxt");
  el("results").classList.remove("hidden");
}

applyLang();  // initiale Sprache setzen
</script></body></html>"""


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
def main():
    global API_ROOT, INSECURE
    ap = argparse.ArgumentParser(description="eVisitor Übernachtungen – HTML-UI + lokaler Proxy.")
    ap.add_argument("--port", type=int, default=8080, help="Port (Standard 8080).")
    ap.add_argument("--test", action="store_true", help="eVisitor-Test-API statt Produktion.")
    ap.add_argument("--insecure", action="store_true", help="TLS-Prüfung deaktivieren (nur Notfall).")
    args = ap.parse_args()

    API_ROOT = API_ROOT_TEST if args.test else API_ROOT_PROD
    INSECURE = args.insecure

    addr = ("127.0.0.1", args.port)
    try:
        httpd = ThreadingServer(addr, Handler)
    except OSError as e:
        print("Konnte Port %d nicht öffnen: %s\nTipp: anderer Port mit --port 8090"
              % (args.port, e))
        sys.exit(1)

    print("=" * 56)
    print(" eVisitor Übernachtungen – lokaler Server läuft")
    print(" API-Ziel : %s" % API_ROOT)
    print(" Jetzt in Safari öffnen:  http://localhost:%d" % args.port)
    print(" (Server läuft nur lokal auf dem iPad. Beenden: Strg-C)")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
