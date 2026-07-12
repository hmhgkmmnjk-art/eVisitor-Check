#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN)  v10 – gezielt Ressource 'Tourist' lesen
======================================================================

Aus der Wiki: die Gäste-Anmeldung erzeugt einen 'Tourist'-Datensatz mit
Feldern StayFrom (Anreise), ForeseenStayUntil, CheckOutDate (Abreise),
CheckedOutTourist (bool), TouristCancelled (bool), Facility, TouristName,
TouristSurname. 'Tourist' braucht beim GET einen Filter -> hier werden
mehrere Filtervarianten probiert, um echte Daten + das Datumsformat zu sehen.

SICHERHEIT: nur GET (Lesen) plus Login-POST. Schreiben ist blockiert.

Nutzung (feste Commit-Adresse, cache-sicher):
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/PLATZHALTER_SHA/evisitor_diag.py"
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

BASE = "https://www.evisitor.hr/eVisitorRhetos_API"
LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login"
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
    op.addheaders = [("User-Agent", "eVisitor-Diag/10 (read-only)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op, cj


def login(op, user, pw):
    url = BASE + LOGIN_PATH
    assert_read_only("POST", url)
    body = json.dumps({"userName": user, "password": pw, "PersistCookie": False}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with op.open(req, timeout=TIMEOUT) as r:
        return r.read(50).decode("utf-8", "replace").strip()


def get(op, path):
    url = BASE + path
    assert_read_only("GET", url)
    req = urllib.request.Request(url, method="GET")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(500000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(700).decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def records_of(raw):
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        for k in ("Records", "records", "value", "Value"):
            if isinstance(data.get(k), list):
                return data[k]
    if isinstance(data, list):
        return data
    return None


def net_date(d):
    ms = int(dt.datetime(d.year, d.month, d.day).timestamp() * 1000)
    return "/Date(%d+0100)/" % ms


def fenc(conds):
    return urllib.parse.quote(json.dumps(conds))


def try_variant(op, resource, label, query):
    code, raw = get(op, "/Rest/Htz/%s/%s" % (resource, query))
    if code == 200:
        recs = records_of(raw)
        if recs is None:
            print("  200 ?  %-26s (kein Records-Format) %s" % (label, raw[:80]))
            return None
        fields = sorted(recs[0].keys()) if recs else []
        print("  200 ✅ %-26s -> %d Datensatz/Datensätze, %d Felder"
              % (label, len(recs), len(fields)))
        if fields:
            print("        Felder: %s" % ", ".join(fields))
            print("        Beispiel: %s" % json.dumps(recs[0], ensure_ascii=False)[:600])
        return (label, fields, recs)
    else:
        print("  %-4s   %-26s %s" % (code, label, (raw or "").strip()[:220]))
        return None


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print("=" * 68)
    print(" eVisitor Diagnose v10 – Ressource 'Tourist' lesen (NUR LESEN)")
    print("=" * 68)
    print("TLS: %s | Basis: %s" % (ssl.OPENSSL_VERSION, BASE))

    a = sys.argv[1:]
    if len(a) >= 2:
        user, pw = a[0].strip(), a[1]
    else:
        user = input("Benutzername: ").strip()
        pw = input("Passwort (sichtbar): ").strip()
    if not user or not pw:
        print("Abbruch: Zugangsdaten nötig.")
        return

    op, cj = new_session()
    try:
        ans = login(op, user, pw)
    except Exception as e:
        print("Login-Fehler: %s" % e)
        return
    print("\nLogin-Antwort: %s  %s" % (ans, "✅" if ans.lower() == "true" else "❌"))
    if ans.lower() != "true":
        print("Login nicht erfolgreich – bitte Passwort prüfen.")
        return

    year = dt.date.today().year
    jan1 = dt.date(year, 1, 1)

    # --- FacilityBrowse: eigene Objekte (liefert u. a. den Objekt-Code) ---
    print("\n--- A) Eigene Objekte (FacilityBrowse) ---")
    try_variant(op, "FacilityBrowse", "filters=[]", "?filters=%s&page=1&psize=5" % fenc([]))

    # --- Tourist: verschiedene Filter, um lesbare Daten zu bekommen ---
    print("\n--- B) Ressource 'Tourist' – Filtervarianten (%d) ---" % year)
    variants = [
        ("nur paging", "?page=1&psize=2"),
        ("filters=[]", "?filters=%s&page=1&psize=2" % fenc([])),
        ("TouristCancelled=false",
         "?filters=%s&page=1&psize=2" % fenc([{"Property": "TouristCancelled", "Operation": "equal", "Value": "false"}])),
        ("CheckedOutTourist=false",
         "?filters=%s&page=1&psize=2" % fenc([{"Property": "CheckedOutTourist", "Operation": "equal", "Value": "false"}])),
        ("StayFrom>=ISO %s" % jan1,
         "?filters=%s&page=1&psize=2" % fenc([{"Property": "StayFrom", "Operation": "greaterequal", "Value": jan1.isoformat()}])),
        ("StayFrom>=NETDate",
         "?filters=%s&page=1&psize=2" % fenc([{"Property": "StayFrom", "Operation": "greaterequal", "Value": net_date(jan1)}])),
    ]
    got = None
    for label, q in variants:
        res = try_variant(op, "Tourist", label, q)
        if res and res[1] and got is None:
            got = res

    # Zähl-Variante (TotalCount) für das ganze Jahr, falls Tourist lesbar ist
    if got:
        print("\n--- C) Zählung (RecordsAndTotalCount, StayFrom>=%s) ---" % jan1)
        code, raw = get(op, "/Rest/Htz/Tourist/RecordsAndTotalCount?filters=%s&page=1&psize=1"
                        % fenc([{"Property": "StayFrom", "Operation": "greaterequal", "Value": jan1.isoformat()}]))
        print("  HTTP %s  %s" % (code, (raw or "")[:200]))

    print("\n--- Zusammenfassung ---")
    if got:
        print("✅ 'Tourist' ist lesbar mit: %s" % got[0])
        print("   Felder: %s" % ", ".join(got[1]))
        print("Damit kann ich die App fertigstellen. Bitte komplette Ausgabe schicken.")
    else:
        print("Kein Tourist-Filter lieferte Daten. Bitte die vollständigen 400-Meldungen")
        print("oben schicken – sie sagen, welcher Filter/Parameter verlangt wird.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
