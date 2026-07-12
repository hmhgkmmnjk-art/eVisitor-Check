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

# WICHTIG – ggf. anpassen (siehe README / --discover in evisitor_nocenja.py):
REPORT_PATH = "/Rest/Htz/EvidencijaGostiju"       # <-- ggf. anpassen
DATE_PARAM_FROM = "datumOd"                        # <-- ggf. anpassen
DATE_PARAM_TO = "datumDo"                          # <-- ggf. anpassen

CHECKIN_FIELDS = [
    "DatumDolaska", "datumDolaska", "DatumPrijave", "datumPrijave",
    "CheckIn", "checkIn", "ArrivalDate", "arrivalDate", "DatumOd", "datumOd",
]
CHECKOUT_FIELDS = [
    "DatumOdlaska", "datumOdlaska", "DatumOdjave", "datumOdjave",
    "CheckOut", "checkOut", "DepartureDate", "departureDate", "DatumDo", "datumDo",
]
# Namensfelder für die Gästeliste (mehrere Varianten):
FIRSTNAME_FIELDS = ["Ime", "ime", "FirstName", "firstName", "ImeGosta", "imeGosta"]
LASTNAME_FIELDS = ["Prezime", "prezime", "LastName", "lastName", "PrezimeGosta", "prezimeGosta"]
NAME_FIELDS = ["ImePrezime", "imePrezime", "Naziv", "naziv", "Name", "name", "PunoIme"]

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
        try:
            ms = int(s[6:-2].split("+")[0].split("-")[0])
            return dt.datetime.utcfromtimestamp(ms / 1000.0).date()
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
    params = {DATE_PARAM_FROM: date_from.isoformat(), DATE_PARAM_TO: date_to.isoformat()}
    query = "&".join("%s=%s" % (k, urllib.parse.quote(str(v))) for k, v in params.items())
    url = API_ROOT + REPORT_PATH + "?" + query
    req = urllib.request.Request(url, method="GET")
    try:
        with do(opener, req) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError("Report-Aufruf HTTP %d – bitte REPORT_PATH prüfen." % e.code)
    except urllib.error.URLError as e:
        raise RuntimeError("Keine Verbindung beim Report-Aufruf (%s)." % e.reason)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise RuntimeError("Report-Antwort ist kein JSON – bitte REPORT_PATH prüfen.")
    records = extract_records(payload)
    if not records and raw.strip() not in ("[]", "{}"):
        raise RuntimeError("Report-Endpunkt lieferte keine erkennbare Liste "
                           "– bitte REPORT_PATH/Feldnamen prüfen.")
    return records


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
            self._send(400, json.dumps({"error": "Ungültige Anfrage."}))
            return

        date_from = parse_date(payload.get("date_from"))
        date_to = parse_date(payload.get("date_to"))
        accounts = payload.get("accounts") or []
        if not date_from or not date_to:
            self._send(400, json.dumps({"error": "Zeitraum fehlt oder ungültig."}))
            return
        if date_to < date_from:
            self._send(400, json.dumps({"error": "Bis-Datum liegt vor Von-Datum."}))
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
            except Exception as e:
                entry["ok"] = False
                entry["error"] = "Unerwarteter Fehler: %s" % e
            results.append(entry)

        if not results:
            self._send(400, json.dumps(
                {"error": "Kein Account mit Benutzername UND Passwort angegeben."}))
            return

        self._send(200, json.dumps({
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "results": results,
        }, ensure_ascii=False))


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
</style></head><body><div class="wrap">
  <h1>eVisitor – Übernachtungen (noćenja)</h1>
  <div class="sub">Läuft lokal auf dem iPad · Passwörter werden nicht gespeichert</div>

  <div id="err" class="msg error hidden"></div>

  <div class="card">
    <h2>Accounts</h2>
    <div id="accounts"></div>
    <label class="chk"><input type="checkbox" id="remember"> Benutzernamen merken (nur Namen, keine Passwörter)</label>
  </div>

  <div class="card">
    <h2>Zeitraum</h2>
    <div class="seg" id="seg">
      <button data-mode="year" class="active">Laufendes Jahr</button>
      <button data-mode="pastyear">Anderes Jahr</button>
      <button data-mode="range">Von–Bis</button>
    </div>
    <div id="pastyearBox" class="hidden">
      <label>Jahr</label>
      <select id="yearSel"></select>
    </div>
    <div id="rangeBox" class="hidden">
      <div class="acc">
        <div><label>Von</label><input type="date" id="dFrom"></div>
        <div><label>Bis</label><input type="date" id="dTo"></div>
      </div>
    </div>
    <div class="note" id="periodInfo"></div>
  </div>

  <button class="go" id="go">Übernachtungen abfragen</button>

  <div id="results" class="hidden">
    <div class="card">
      <h2>Ergebnis <span id="periodLabel" style="font-weight:400;color:var(--muted)"></span></h2>
      <div class="kpis">
        <div class="kpi"><div class="n" id="kNights">0</div><div class="l">Übernachtungen gesamt</div></div>
        <div class="kpi"><div class="n" id="kOpen">0</div><div class="l">davon offen (nicht abgemeldet)</div></div>
        <div class="kpi"><div class="n" id="kGuests">0</div><div class="l">Gäste gesamt</div></div>
      </div>
    </div>
    <div class="card">
      <h2>Pro Account</h2>
      <table><thead><tr><th>Account</th><th style="text-align:right">Übernacht.</th>
        <th style="text-align:right">davon offen</th><th style="text-align:right">Gäste</th></tr></thead>
        <tbody id="accRows"></tbody></table>
    </div>
    <div class="card">
      <h2>Übernachtungen pro Monat</h2>
      <div id="bars"></div>
    </div>
    <div class="card">
      <h2>Angemeldete Gäste im Zeitraum</h2>
      <div id="guests"></div>
    </div>
    <footer id="footer"></footer>
  </div>
</div>
<script>
"use strict";
var MONTHS=["Jan","Feb","Mär","Apr","Mai","Jun","Jul","Aug","Sep","Okt","Nov","Dez"];
var LS_KEY="evisitor_usernames";
var mode="year";

function el(id){return document.getElementById(id);}
function esc(s){return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function todayISO(){return new Date().toISOString().slice(0,10);}
function pad(n){return (n<10?"0":"")+n;}

// --- Account-Felder aufbauen ---
(function buildAccounts(){
  var saved=[];
  try{saved=JSON.parse(localStorage.getItem(LS_KEY)||"[]");}catch(e){}
  var box=el("accounts"),html="";
  for(var i=0;i<3;i++){
    var u=saved[i]?esc(saved[i]):"";
    html+='<div class="acc"><div class="tag">Account '+(i+1)+'</div>'+
      '<div><label>Benutzername</label><input type="text" autocomplete="off" '+
      'autocapitalize="none" spellcheck="false" id="u'+i+'" value="'+u+'"></div>'+
      '<div><label>Passwort</label><input type="password" autocomplete="off" id="p'+i+'"></div></div>';
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
  if(mode==="year") return {from:y+"-01-01",to:y+"-12-31",label:"Kalenderjahr "+y};
  if(mode==="pastyear"){var yy=el("yearSel").value;return {from:yy+"-01-01",to:yy+"-12-31",label:"Kalenderjahr "+yy};}
  return {from:el("dFrom").value,to:el("dTo").value,label:null};
}
function updateInfo(){
  var p=currentPeriod();
  el("periodInfo").textContent = p.from&&p.to ? ("Abfrage: "+p.from+" bis "+p.to) : "Bitte Von- und Bis-Datum wählen.";
}
// Standard-Datumsfelder vorbelegen
(function(){var y=new Date().getFullYear();el("dFrom").value=y+"-01-01";el("dTo").value=todayISO();updateInfo();})();

function showError(msg){el("err").textContent=msg;el("err").classList.remove("hidden");window.scrollTo(0,0);}
function clearError(){el("err").classList.add("hidden");}

// --- Abfrage senden ---
el("go").addEventListener("click",function(){
  clearError();
  var accounts=[];
  for(var i=0;i<3;i++){
    var u=el("u"+i).value.trim(),p=el("p"+i).value;
    if(u&&p) accounts.push({username:u,password:p});
  }
  if(!accounts.length){showError("Bitte für mindestens einen Account Benutzername UND Passwort eingeben.");return;}

  var p=currentPeriod();
  if(!p.from||!p.to){showError("Bitte einen gültigen Zeitraum wählen.");return;}
  if(p.to<p.from){showError("Das Bis-Datum liegt vor dem Von-Datum.");return;}

  // Benutzernamen merken (nur Namen)
  if(el("remember").checked){
    var names=[]; for(var j=0;j<3;j++) names.push(el("u"+j).value.trim());
    try{localStorage.setItem(LS_KEY,JSON.stringify(names));}catch(e){}
  } else { try{localStorage.removeItem(LS_KEY);}catch(e){} }

  var btn=el("go"); btn.disabled=true;
  btn.innerHTML='<span class="spin"></span>Abfrage läuft … ('+accounts.length+' Account/s)';

  fetch("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({accounts:accounts,date_from:p.from,date_to:p.to})})
  .then(function(r){return r.json().then(function(j){return {status:r.status,body:j};});})
  .then(function(res){
    if(res.status!==200){showError(res.body&&res.body.error?res.body.error:"Fehler bei der Abfrage.");return;}
    render(res.body);
  })
  .catch(function(e){showError("Keine Verbindung zum lokalen Server. Läuft evisitor_proxy.py noch in a-Shell? ("+e+")");})
  .finally(function(){btn.disabled=false;btn.textContent="Übernachtungen abfragen";
    // Passwortfelder aus dem DOM leeren (nur im Speicher, nichts gespeichert)
    for(var i=0;i<3;i++) el("p"+i).value="";});
});

function render(data){
  var totN=0,totOpen=0,totG=0,months={};
  var rows="";
  data.results.forEach(function(r){
    if(!r.ok){
      rows+='<tr class="err"><td>'+esc(r.username)+'</td><td colspan="3">Fehler: '+esc(r.error||"")+'</td></tr>';
      return;
    }
    var s=r.stats;
    totN+=s.total_nights;totOpen+=s.open_nights;totG+=s.guests;
    for(var k in s.monthly){months[k]=(months[k]||0)+s.monthly[k];}
    rows+='<tr><td>'+esc(r.username)+'</td><td class="num big">'+s.total_nights+
          '</td><td class="num">'+s.open_nights+'</td><td class="num">'+s.guests+'</td></tr>';
  });
  rows+='<tr class="total"><td>GESAMT</td><td class="num big">'+totN+
        '</td><td class="num">'+totOpen+'</td><td class="num">'+totG+'</td></tr>';
  el("accRows").innerHTML=rows;
  el("kNights").textContent=totN;el("kOpen").textContent=totOpen;el("kGuests").textContent=totG;
  el("periodLabel").textContent="("+data.date_from+" bis "+data.date_to+")";

  // Balkendiagramm
  var keys=Object.keys(months).sort();
  var max=0; keys.forEach(function(k){if(months[k]>max)max=months[k];}); if(!max)max=1;
  var bars="";
  keys.forEach(function(k){
    var parts=k.split("-"),lab=MONTHS[parseInt(parts[1],10)-1]+" "+parts[0];
    var pct=Math.round(months[k]/max*100);
    bars+='<div class="barrow"><div class="barlabel">'+lab+'</div>'+
      '<div class="bartrack"><div class="bar" style="width:'+pct+'%"><span>'+months[k]+'</span></div></div></div>';
  });
  el("bars").innerHTML=bars||'<p class="note">Keine Monatsdaten im Zeitraum.</p>';

  // Gästeliste pro Account (aufklappbar)
  var gh="";
  data.results.forEach(function(r){
    if(!r.ok){return;}
    var list=r.stats.guest_list||[];
    var rowsG="";
    list.forEach(function(g){
      var co = g.checkout ? g.checkout : '<span class="tagopen">offen</span>';
      rowsG+='<tr><td>'+esc(g.name)+'</td><td>'+g.checkin+'</td><td>'+co+
             '</td><td class="num">'+g.nights+'</td></tr>';
    });
    if(!rowsG) rowsG='<tr><td colspan="4" class="note">Keine Gäste im Zeitraum.</td></tr>';
    gh+='<details><summary><span>'+esc(r.username)+'</span>'+
        '<span class="badge">'+list.length+' Gäste · '+r.stats.total_nights+' Nächte</span></summary>'+
        '<table class="gtable"><thead><tr><th>Gast</th><th>Anreise</th>'+
        '<th>Abreise</th><th style="text-align:right">Nächte</th></tr></thead>'+
        '<tbody>'+rowsG+'</tbody></table></details>';
  });
  el("guests").innerHTML=gh||'<p class="note">Keine Gästedaten.</p>';

  var d=new Date();
  el("footer").textContent="Erstellt am "+pad(d.getDate())+"."+pad(d.getMonth()+1)+"."+d.getFullYear()+
    " "+pad(d.getHours())+":"+pad(d.getMinutes())+" · lokal auf dem iPad berechnet";
  el("results").classList.remove("hidden");
  el("results").scrollIntoView({behavior:"smooth"});
}
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
