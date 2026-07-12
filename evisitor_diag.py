#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN)  v11 – Tourist mit sort=ID lesen
==============================================================

Fehler aus v10 verraten:
  * "Sort order must be set if paging is used"  -> sort-Parameter nötig
  * "Type 'Common.Queryable.Htz_Tourist' does not have property ..."
     -> die lesbare Tourist-Tabelle hat andere Feldnamen als die Aktion
Lösung: mit ?sort=ID&page=1&psize=.. lesen (kein geratener Filter),
dann die ECHTEN Feldnamen aus einem Datensatz ablesen.

SICHERHEIT: nur GET (Lesen) plus Login-POST. Schreiben ist blockiert.

Nutzung:
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/PLATZHALTER/evisitor_diag.py"
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
    op.addheaders = [("User-Agent", "eVisitor-Diag/11 (read-only)"),
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
            return r.status, r.read(800000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(900).decode("utf-8", "replace")
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


def total_of(raw):
    try:
        data = json.loads(raw)
        for k in ("TotalCount", "totalCount", "Count", "count"):
            if isinstance(data.get(k), int):
                return data[k]
    except Exception:
        pass
    return None


def fenc(conds):
    return urllib.parse.quote(json.dumps(conds))


def show(op, title, path):
    print("\n%s" % title)
    print("  GET %s" % path)
    code, raw = get(op, path)
    if code == 200:
        recs = records_of(raw)
        if recs is None:
            print("  200 (kein Records-Format): %s" % raw[:200])
            return None
        print("  200 ✅  %d Datensatz/Datensätze" % len(recs))
        if recs:
            fields = sorted(recs[0].keys())
            print("  Felder (%d): %s" % (len(fields), ", ".join(fields)))
            print("  Beispiel-Datensatz:")
            print("    " + json.dumps(recs[0], ensure_ascii=False, indent=1).replace("\n", "\n    ")[:1600])
        return recs
    print("  %s: %s" % (code, (raw or "").strip()[:500]))
    return None


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print("=" * 68)
    print(" eVisitor Diagnose v11 – Tourist mit sort=ID (NUR LESEN)")
    print("=" * 68)
    print("TLS: %s" % ssl.OPENSSL_VERSION)

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
    print("Login: %s %s" % (ans, "✅" if ans.lower() == "true" else "❌"))
    if ans.lower() != "true":
        return

    # A) Eigene Objekte
    show(op, "--- A) FacilityBrowse (eigene Objekte) ---",
         "/Rest/Htz/FacilityBrowse/?sort=ID&page=1&psize=5")

    # B) Tourist-Datensätze (echte Feldnamen!)
    recs = show(op, "--- B) Tourist (sort=ID) – echte Felder ---",
                "/Rest/Htz/Tourist/?sort=ID&page=1&psize=3")

    if not recs:
        print("\nKonnte keinen Tourist-Datensatz lesen. Volle Meldung oben schicken.")
        return

    fields = list(recs[0].keys())
    # Datumsfelder automatisch erkennen (Wert /Date(..)/ oder Name deutet auf Datum)
    date_fields = []
    for f in fields:
        v = recs[0].get(f)
        if isinstance(v, str) and v.startswith("/Date("):
            date_fields.append(f)
        elif re.search(r'stay|arriv|depart|checkin|checkout|datum|date|from|until', f, re.I):
            if f not in date_fields:
                date_fields.append(f)
    print("\n--- C) Erkannte Datums-/Aufenthaltsfelder ---")
    print("  %s" % (", ".join(date_fields) or "(keine eindeutig erkannt)"))

    # D) Jahres-Zählung über das erkannte Anreisefeld (mehrere Wertformate testen)
    year = dt.date.today().year
    jan1 = dt.date(year, 1, 1)
    arrival = None
    for cand in ("StayFrom", "CheckInDate", "ArrivalDate", "DateFrom", "StayDateFrom"):
        if cand in fields:
            arrival = cand
            break
    if not arrival and date_fields:
        arrival = date_fields[0]

    if arrival:
        print("\n--- D) Zählung %d über Feld '%s' (RecordsAndTotalCount) ---" % (year, arrival))
        for label, val in (("ISO", jan1.isoformat()),
                           ("ISO+Zeit", jan1.isoformat() + "T00:00:00")):
            q = "/Rest/Htz/Tourist/RecordsAndTotalCount?sort=ID&page=1&psize=1&filters=%s" % fenc(
                [{"Property": arrival, "Operation": "greaterequal", "Value": val}])
            code, raw = get(op, q)
            tc = total_of(raw)
            print("  Filter %-8s -> HTTP %s  TotalCount=%s  %s"
                  % (label, code, tc, "" if code == 200 else (raw or "")[:160]))

    print("\n--- Zusammenfassung ---")
    print("Echte Tourist-Felder: %s" % ", ".join(fields))
    print("Bitte KOMPLETTE Ausgabe schicken – v. a. Abschnitt B (Beispiel-Datensatz)")
    print("und D (welches Filterformat TotalCount liefert). Damit ist die App fertig.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
