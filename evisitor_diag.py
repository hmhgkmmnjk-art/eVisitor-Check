#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN – read-only)  v7  – richtige Produktions-Adresse
=============================================================================

Aus der offiziellen Doku:
  * Produktions-API-ROOT: https://www.evisitor.hr/eVisitorRhetos_API
  * Login: .../Resources/AspNetFormsAuth/Authentication/Login
           Body {"userName":..,"password":..}  (apikey NUR auf Testplattform!)
           -> Antwort "true"/"false", bei Erfolg Auth-Cookies
  * Abfragen: .../Rest/Htz/<Resource>/?page=1&psize=..&filters=[..]&sort=..
              Ergebnis {Records:[...]}

Diese Diagnose loggt sich in die PRODUKTION ein (ohne apikey) und sucht die
Ressource mit den Gäste-/Aufenthaltsdaten (Check-in/Check-out).

SICHERHEIT: nur GET (Lesen) plus Login-POST. Schreiben ist blockiert.

Nutzung:
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/claude/evisitor-overnight-stays-74xv0o/evisitor_diag.py?cb=7"
    python3 evisitor_diag.py 57344933760 Jure2234
"""

import sys
import json
import re
import ssl
import datetime as dt
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar

LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login"

BASES = [
    "https://www.evisitor.hr/eVisitorRhetos_API",   # PRODUKTION (kein apikey)
    "https://www.evisitor.hr/testApi",              # Test (braucht apikey)
]

# Kandidaten für lesbare Ressourcen (nur GET). Zuerst eigene Objekte des
# Vermieters (zur Bestätigung der Autorisierung), dann Gäste/Aufenthalte.
GUEST_RESOURCES = [
    # Objekte / Vermieter (sollten für den Obveznik lesbar sein):
    "FacilityBrowse", "Facility", "TTPayerUnion", "TTPayer",
    # Gäste / Check-in / Aufenthalte:
    "TouristCheckIn", "TouristCheckInBrowse", "CheckedInTourist",
    "CheckedInTouristBrowse", "ActiveTourist", "ActiveTouristBrowse",
    "Tourist", "TouristBrowse", "TouristStay", "TouristStayBrowse",
    "TouristReport", "TouristReportBrowse", "TouristCheckInReport",
    "TouristCheckInReportBrowse", "CheckInBrowse", "GuestBook", "GuestBookBrowse",
    "TouristTraffic", "TouristTrafficBrowse", "EvidencijaTurista",
    "PopisTurista", "Boravak",
    # Übernachtungsberechnung (nur lesen; keine Aktion auslösen):
    "TTCalculationItemSourceByTourist", "TTCalculationItemSourceByFacility",
]

FORBIDDEN_GET = ("save", "import", "create", "update", "delete", "insert")
TIMEOUT = 25


def assert_read_only(method, url):
    m = method.upper()
    low = urllib.parse.urlparse(url).path.rstrip("/").lower()
    if m == "GET":
        for bad in FORBIDDEN_GET:
            if bad in low:
                raise RuntimeError("SICHERHEIT: verdächtiger Pfad blockiert (%s)." % low)
        return
    if m == "POST" and low.endswith(LOGIN_PATH.rstrip("/").lower()):
        return
    raise RuntimeError("SICHERHEIT: nicht-lesender Zugriff blockiert (%s)." % m)


def make_ssl_context():
    ctx = ssl.create_default_context()
    for spec in ("DEFAULT@SECLEVEL=0", "ALL@SECLEVEL=0"):
        try:
            ctx.set_ciphers(spec)
            break
        except ssl.SSLError:
            continue
    return ctx


def new_session():
    cj = http.cookiejar.CookieJar()
    https = urllib.request.HTTPSHandler(context=make_ssl_context())
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), https)
    op.addheaders = [("User-Agent", "eVisitor-Diag/7 (read-only)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op, cj


def do_login(base, user, pw, apikey):
    url = base + LOGIN_PATH
    assert_read_only("POST", url)
    payload = {"userName": user, "password": pw, "PersistCookie": False}
    if apikey:
        payload["apikey"] = apikey
    op, cj = new_session()
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(300).decode("utf-8", "replace").strip(), op, cj
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "replace").strip(), op, cj
    except Exception as e:
        return None, str(e)[:140], None, None


def get(op, base, path):
    url = base + path
    assert_read_only("GET", url)
    req = urllib.request.Request(url, method="GET")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(500000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(600).decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def records_of(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if isinstance(data, dict):
        for k in ("Records", "records", "value", "Value"):
            if isinstance(data.get(k), list):
                return data[k]
    if isinstance(data, list):
        return data
    return None


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print("=" * 68)
    print(" eVisitor Diagnose v8 – Ressourcensuche (NUR LESEN)")
    print("=" * 68)
    print("TLS: %s" % ssl.OPENSSL_VERSION)

    a = sys.argv[1:]
    if len(a) >= 2:
        user, pw = a[0].strip(), a[1]
        apikey = a[2].strip() if len(a) >= 3 else ""
    else:
        user = input("Benutzername: ").strip()
        pw = input("Passwort (sichtbar): ").strip()
        apikey = ""
    if not user or not pw:
        print("Abbruch: Zugangsdaten nötig.")
        return
    print("Benutzername: %s   apikey: %s" % (user, "ja" if apikey else "nein (Produktion)"))

    # 1) Login (Produktion zuerst, ohne apikey)
    print("\n--- 1) Login ---")
    session = None
    for base in BASES:
        tag = base.split("/")[-1]
        use_key = apikey if "test" in tag.lower() else ""
        code, resp, op, cj = do_login(base, user, pw, use_key)
        if code is None:
            print("  %-18s Fehler: %s" % (tag, resp))
            continue
        cookies = [c.name for c in (cj or [])]
        success = (code == 200 and resp.lower() == "true")
        print("  %-18s HTTP %s Body=%-6s Cookies=%s %s"
              % (tag, code, resp[:6] or "-", ",".join(cookies) or "-", "✅" if success else ""))
        if not success and resp and resp.lower() != "false":
            print("       Antwort: %s" % resp[:180])
        if success and session is None:
            session = (base, op)

    if not session:
        print("\nKein Login mit 'true'.")
        print("Wenn Produktion 'false': Passwort prüfen. Ausgabe bitte schicken.")
        return

    base, op = session
    print("\n✅ Angemeldet an: %s" % base)

    # 2) Ressourcen probieren:
    #    200      = lesbar (Treffer, Felder anzeigen)
    #    400/401  = Ressource existiert, aber nicht berechtigt
    #    404      = Ressource gibt es nicht (unter diesem Namen)
    print("\n--- 2) Lesbare Ressourcen suchen ---")
    print("  (200 = lesbar ✅ | 'not authorized' = existiert, keine Berechtigung | 404 = kein solcher Name)")
    hits = []
    for name in GUEST_RESOURCES:
        code, raw = get(op, base, "/Rest/Htz/%s/?page=1&psize=1" % name)
        low = (raw or "").lower()
        if code == 200:
            recs = records_of(raw)
            if recs is None:
                print("  200 ?  %-30s (unerwartetes Format: %s)" % (name, (raw or "")[:60]))
                continue
            fields = sorted(recs[0].keys()) if recs else []
            date_fields = [f for f in fields if re.search(
                r'date|datum|dolask|odlask|checkin|checkout|arriv|depart', f, re.I)]
            print("  200 ✅ %-30s Felder=%d %s" % (
                name, len(fields),
                ("| Datum: " + ", ".join(date_fields)) if date_fields else
                ("(0 Datensätze auf Seite 1)" if not fields else "")))
            if fields:
                print("        Felder: %s" % ", ".join(fields))
            hits.append((name, fields, date_fields, recs))
        elif "not authorized" in low or code in (401, 403):
            print("  🔒     %-30s existiert, aber keine Leseberechtigung" % name)
        elif code == 404:
            pass  # gibt's nicht – still
        else:
            print("  %-4s   %-30s %s" % (code, name, (raw or "")[:70]))

    print("\n--- Zusammenfassung ---")
    if hits:
        withdate = [h for h in hits if h[2]]
        print("Lesbare Ressourcen: %s" % ", ".join(h[0] for h in hits))
        if withdate:
            best = withdate[0]
            print("Mit Check-in/Check-out-Feldern: %s" % ", ".join(h[0] for h in withdate))
            print("\n--- Beispiel-Datensatz von %s ---" % best[0])
            if best[3]:
                print(json.dumps(best[3][0], ensure_ascii=False, indent=1)[:1800])
        else:
            print("Noch keine mit Datumsfeldern gefunden – evtl. 0 Gäste im Jahr oder")
            print("anderer Ressourcenname. Bitte Wiki-Abschnitt 'Resursi' ansehen.")
    else:
        print("Keine der geratenen Ressourcen war lesbar.")
        print("Bitte in der Wiki (direkt nach deinem Zitat) unter 'Resursi' die Liste")
        print("ansehen und mir Namen + 'Atributi' der Tourist-/CheckIn-Ressource nennen.")
    print("\nBitte komplette Ausgabe schicken.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
