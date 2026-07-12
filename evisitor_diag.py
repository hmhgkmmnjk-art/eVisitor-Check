#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN – read-only)
=========================================

Findet heraus, welche API-Basisadresse mit deinem Login funktioniert und
welcher Endpunkt die Gäste-/Übernachtungsdaten liefert. Ändert NICHTS.

SICHERHEIT: Dieses Skript sendet ausschließlich
  - GENAU EINEN POST an den Login (nur Einloggen, KEINE Gäste-Anmeldung)
  - danach nur GET-Abfragen (Lesen)
Jeder andere Zugriff wird durch assert_read_only() hart blockiert.
Es werden nur Substantiv-Ressourcen abgefragt – niemals Aktionen wie
CheckIn/Prijava/Save/Import/Odjava.

Nutzung in a-Shell:
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/claude/evisitor-overnight-stays-74xv0o/evisitor_diag.py"
    python3 evisitor_diag.py
Die Ausgabe bitte kopieren und mir schicken.
"""

import sys
import json
import datetime as dt
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar
import ssl

LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login"

# Basis-Adressen, die getestet werden (Produktion zuerst):
CANDIDATE_BASES = [
    "https://www.evisitor.hr/eVisitorApi",
    "https://www.evisitor.hr/testApi",
    "https://www.evisitor.hr/api",
]

# Nur LESENDE Substantiv-Ressourcen (KEINE Aktionen!). "Country" ist laut
# offizieller Doku ein funktionierendes Beispiel -> Auth-Test.
CANDIDATE_RESOURCES = [
    "/Rest/Htz/Country/",
    "/Rest/Htz/",
    "/Rest/Htz/EvidencijaGostiju/",
    "/Rest/Htz/EvidencijaGostiju",
    "/Rest/Htz/Turist/",
    "/Rest/Htz/Turisti/",
    "/Rest/Htz/Gost/",
    "/Rest/Htz/Gosti/",
    "/Rest/Htz/Boravak/",
    "/Rest/Htz/Nocenje/",
    "/Rest/Htz/Nocenja/",
    "/Rest/Htz/TuristickiPromet/",
    "/Rest/Htz/Promet/",
]

# Verbotene (schreibende / aktive) Wörter – zur Sicherheit doppelt geprüft:
FORBIDDEN = ("checkin", "checkout", "prijav", "odjav", "save", "import",
             "new", "create", "update", "delete", "insert", "post", "add")

TIMEOUT = 20


def assert_read_only(req):
    """Harte Sperre: nur GET, plus der EINE Login-POST. Sonst Abbruch."""
    method = req.get_method().upper()
    low = urllib.parse.urlparse(req.full_url).path.rstrip("/").lower()
    if method == "GET":
        # zusätzlich: keine offensichtlich schreibenden Aktionen als GET
        for bad in FORBIDDEN:
            if bad in low:
                raise RuntimeError("SICHERHEIT: verdächtiger Pfad blockiert: %s" % low)
        return
    if method == "POST" and low.endswith(LOGIN_PATH.rstrip("/").lower()):
        return
    raise RuntimeError("SICHERHEIT: nicht-lesender Zugriff blockiert: %s %s"
                       % (method, req.full_url))


def make_ssl_context():
    """TLS-Kontext, der auch den veralteten schwachen DH-Schlüssel von
    evisitor.hr akzeptiert (SECLEVEL=1). Zertifikatsprüfung bleibt aktiv."""
    ctx = ssl.create_default_context()
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    return ctx


def make_opener():
    cj = http.cookiejar.CookieJar()
    https = urllib.request.HTTPSHandler(context=make_ssl_context())
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), https)
    op.addheaders = [("User-Agent", "eVisitor-Diag/1.0 (read-only)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op


def do(opener, req):
    assert_read_only(req)          # <- Sicherheitssperre vor JEDEM Aufruf
    return opener.open(req, timeout=TIMEOUT)


def try_login(base, user, pw):
    body = json.dumps({"userName": user, "password": pw, "rememberMe": False}).encode()
    req = urllib.request.Request(base + LOGIN_PATH, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    opener = make_opener()
    try:
        with do(opener, req) as r:
            r.read()
        return opener, 200, ""
    except urllib.error.HTTPError as e:
        return opener, e.code, ""
    except urllib.error.URLError as e:
        return None, 0, str(e.reason)
    except Exception as e:
        return None, -1, str(e)


def get(opener, base, path):
    req = urllib.request.Request(base + path, method="GET")
    try:
        with do(opener, req) as r:
            raw = r.read().decode("utf-8", "replace")
        return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, ""
    except urllib.error.URLError as e:
        return 0, str(e.reason)
    except Exception as e:
        return -1, str(e)


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)   # Ausgabe sofort anzeigen
    except Exception:
        pass
    print("=" * 60)
    print(" eVisitor Diagnose – NUR LESEN (ändert nichts)")
    print("=" * 60)

    # Zugangsdaten am liebsten als Argumente (keine unsichtbare Eingabe):
    #   python3 evisitor_diag.py BENUTZERNAME PASSWORT
    if len(sys.argv) >= 3:
        user = sys.argv[1].strip()
        pw = sys.argv[2]
        print("Benutzername: %s (aus Aufruf übernommen)" % user)
    else:
        print("Tipp: Du kannst auch so starten:  python3 evisitor_diag.py BENUTZER PASSWORT")
        print("(Die folgende Eingabe ist SICHTBAR, damit du siehst, dass sie ankommt.)\n")
        user = input("Benutzername: ").strip()
        pw = input("Passwort (sichtbar): ").strip()

    if not user or not pw:
        print("Abbruch: Benutzername und Passwort nötig.")
        return
    print("\n→ Eingaben erhalten. Starte jetzt den Login-Test ...")
    sys.stdout.flush()

    today = dt.date.today()
    df = dt.date(today.year, 1, 1).isoformat()
    dtx = today.isoformat()

    working = None
    print("\n--- Login-Test je Basis-Adresse ---")
    for base in CANDIDATE_BASES:
        opener, code, reason = try_login(base, user, pw)
        tag = base.split("/")[-1]
        if code == 200 and opener is not None:
            print("  %-14s Login -> 200  ✅ LOGIN OK" % tag)
            working = (base, opener)
            break
        elif code in (400, 401, 403):
            print("  %-14s Login -> %d  (Endpunkt existiert, Login abgelehnt)" % (tag, code))
        elif code == 404:
            print("  %-14s Login -> 404 (Adresse falsch)" % tag)
        elif code == 0:
            print("  %-14s keine Verbindung (%s)" % (tag, reason))
        else:
            print("  %-14s Login -> %s" % (tag, code))

    if not working:
        print("\nKein Login erfolgreich. Bitte Ausgabe oben schicken.")
        print("(Wenn ALLE 404 sind, ist die Basis-Adresse eine andere – dann brauche")
        print(" ich den Report-Pfad aus der eingeloggten Web-API-Wiki.)")
        return

    base, opener = working
    print("\nFunktionierende Basis: %s" % base)
    print("\n--- Lesende Ressourcen testen (GET) ---")
    found = []
    for res in CANDIDATE_RESOURCES:
        # Für Gäste-Ressourcen Datumsparameter anhängen (schadet bei anderen nicht)
        url = res
        if res not in ("/Rest/Htz/Country/", "/Rest/Htz/"):
            url = res + "?datumOd=%s&datumDo=%s" % (df, dtx)
        code, raw = get(opener, base, url)
        sample = (raw[:160].replace("\n", " ")) if isinstance(raw, str) else ""
        if code == 200:
            print("  200 ✅  %s" % res)
            print("        Beispiel: %s" % sample)
            found.append((res, raw))
        elif code in (401, 403):
            print("  %d 🔒  %s (nicht berechtigt)" % (code, res))
        elif code == 404:
            print("  404     %s" % res)
        else:
            print("  %-3s     %s  %s" % (code, res, sample[:60]))

    print("\n--- Zusammenfassung ---")
    if found:
        print("Diese Ressourcen liefern Daten (200). Bitte MIR die Ausgabe schicken –")
        print("besonders die 'Beispiel:'-Zeilen, damit ich die Feldnamen (An-/Abreise)")
        print("exakt zuordnen kann.")
        # Von der ersten vielversprechenden Gäste-Ressource mehr zeigen:
        for res, raw in found:
            if res not in ("/Rest/Htz/Country/", "/Rest/Htz/"):
                print("\n--- Vollausschnitt %s (erste 1500 Zeichen) ---" % res)
                print(raw[:1500])
                break
    else:
        print("Login OK, aber keine der geratenen Gäste-Ressourcen passte.")
        print("Bitte in der eingeloggten Web-API-Wiki den Htz-Ressourcennamen für")
        print("Gäste/Übernachtungen ansehen und mir nennen – dann trage ich ihn ein.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
