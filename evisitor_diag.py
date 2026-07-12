#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN – read-only)  v5  – korrektes Login-Format
=======================================================================

Erkenntnis aus v4: Der Login antwortete zwar mit HTTP 200, aber der Body war
"false" und es wurde KEIN Anmelde-Cookie gesetzt -> der Login war in Wahrheit
abgelehnt. Diese API (Rhetos/AspNetFormsAuth) erwartet die Feldnamen
  {"UserName": ..., "Password": ..., "PersistCookie": false}
(Großschreibung!). v5 sendet beide Varianten und wertet den Body ("true")
sowie das Anmelde-Cookie aus. Zusätzlich wird die Produktions-Basis
/eVisitor (Portal-Root) getestet, nicht nur die Test-Umgebung /testApi.

SICHERHEIT: nur GET-Abfragen plus Login-POSTs. Schreibende Methoden
(POST außer Login, PUT, DELETE, PATCH) sind hart blockiert – Daten ändern
ist in dieser API nur über solche Methoden möglich, GET ist reines Lesen.

Nutzung:
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/claude/evisitor-overnight-stays-74xv0o/evisitor_diag.py?cb=5"
    python3 evisitor_diag.py BENUTZER PASSWORT
Komplette Ausgabe bitte kopieren und schicken (Cookie-Werte geschwärzt).
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

# Produktion (Portal-Root) zuerst, dann Test-Umgebung:
BASES = [
    "https://www.evisitor.hr/eVisitor",
    "https://www.evisitor.hr/testApi",
]

# Login-Feldvarianten – Rhetos-Standard zuerst:
PAYLOADS = [
    ("UserName/Password/PersistCookie",
     lambda u, p: {"UserName": u, "Password": p, "PersistCookie": False}),
    ("userName/password/rememberMe",
     lambda u, p: {"userName": u, "password": p, "rememberMe": False}),
]

# Kandidaten für die Gäste-/Übernachtungs-Ressource (NUR GET = nur lesen):
GUEST_RESOURCES = [
    "Turist", "Turisti", "Tourist", "Tourists",
    "TouristCheckIn", "TouristCheckin", "CheckIn", "Checkin",
    "Prijava", "Prijave", "PrijavaTurista", "PrijaveTurista",
    "EvidencijaGostiju", "Gost", "Gosti", "Guest",
    "Boravak", "Boravci", "TuristBoravak", "TouristStay",
    "Nocenje", "Nocenja", "PopisTurista",
    "Objekt", "Facility", "SmjestajnaJedinica", "AccommodationUnit",
]

# Schreibende Begriffe, die auch als GET nie aufgerufen werden:
FORBIDDEN_GET = ("save", "import", "create", "update", "delete", "insert")
TIMEOUT = 20
READ_CAP = 400000   # max. Bytes pro Antwort (Diagnose braucht nicht mehr)


# ---------------------------------------------------------------------------
# Sicherheitssperre: GET = lesen (erlaubt), sonst nur der Login-POST.
# Datenänderungen sind in dieser API ausschließlich über POST-Aktionen /
# PUT / DELETE möglich – alles davon wird hier blockiert.
# ---------------------------------------------------------------------------
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
    op.addheaders = [("User-Agent", "eVisitor-Diag/5 (read-only)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op, cj


def try_login(base, payload):
    url = base + LOGIN_PATH
    assert_read_only("POST", url)
    op, cj = new_session()
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(200).decode("utf-8", "replace").strip(), op, cj, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace").strip(), op, cj, ""
    except Exception as e:
        return None, "", None, None, str(e)


def get(op, base, path):
    url = base + path
    assert_read_only("GET", url)
    req = urllib.request.Request(url, method="GET")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(READ_CAP).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(500).decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def snip(raw, n=220):
    return (raw or "").strip().replace("\n", " ")[:n]


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print("=" * 64)
    print(" eVisitor Diagnose v5 – korrektes Login-Format (NUR LESEN)")
    print("=" * 64)
    print("TLS: %s" % ssl.OPENSSL_VERSION)

    if len(sys.argv) >= 3:
        user, pw = sys.argv[1].strip(), sys.argv[2]
        print("Benutzername: %s" % user)
    else:
        user = input("Benutzername: ").strip()
        pw = input("Passwort (sichtbar): ").strip()
    if not user or not pw:
        print("Abbruch: Zugangsdaten nötig.")
        return

    # ---- 1) Login-Matrix: Basis x Feldvariante ----
    print("\n--- 1) Login-Tests (Erfolg = Body 'true' + Anmelde-Cookie) ---")
    session = None   # (base, opener)
    for base in BASES:
        tag = base.split("/")[-1]
        for pname, pfun in PAYLOADS:
            code, body, op, cj, err = try_login(base, pfun(user, pw))
            if code is None:
                print("  %-10s %-34s Fehler: %s" % (tag, pname, err[:90]))
                continue
            cookies = [c.name for c in cj] if cj else []
            auth_cookie = [c for c in cookies if "aspxauth" in c.lower() or "auth" in c.lower()]
            success = (code == 200 and body.lower() == "true")
            mark = "✅ ERFOLG" if success else ""
            print("  %-10s %-34s HTTP %-4s Body=%-6s Cookies=%s %s"
                  % (tag, pname, code, body[:5] or "-",
                     ",".join(auth_cookie) or (",".join(cookies) or "-"), mark))
            if success and session is None:
                session = (base, op)
        if session and session[0] == base:
            pass  # weiter, Matrix trotzdem vollständig zeigen

    if not session:
        print("\nKein Login erfolgreich (kein Body 'true').")
        print("-> Benutzername/Passwort bitte prüfen; Ausgabe oben schicken.")
        return

    base, op = session
    print("\nAngemeldet an: %s" % base)

    # ---- 2) Auth-Kontrolle mit dokumentierter Ressource ----
    print("\n--- 2) Auth-Kontrolle: /Rest/Htz/Country/ ---")
    code, raw = get(op, base, "/Rest/Htz/Country/")
    n = ""
    if code == 200:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                n = " (%d Einträge)" % len(data)
            elif isinstance(data, dict):
                for k in ("Records", "records", "value"):
                    if isinstance(data.get(k), list):
                        n = " (%d Einträge unter '%s')" % (len(data[k]), k)
                        break
        except ValueError:
            pass
        print("  200 ✅ Sitzung ist authentifiziert%s" % n)
        print("  Beispiel: %s" % snip(raw, 200))
    else:
        print("  %s – Sitzung NICHT authentifiziert. Body: %s" % (code, snip(raw, 150)))
        print("  (Bitte Ausgabe schicken – dann stimmt noch etwas am Cookie-Handling.)")
        return

    # ---- 3) Gäste-Ressource finden ----
    print("\n--- 3) Gäste-/Übernachtungs-Ressourcen (GET, ?top=1) ---")
    hits = []
    for name in GUEST_RESOURCES:
        code, raw = get(op, base, "/Rest/Htz/%s/?top=1" % name)
        if code == 400:   # falls 'top' unbekannt ist
            code, raw = get(op, base, "/Rest/Htz/%s/" % name)
        if code == 200:
            has_date = bool(re.search(r'atum|Date|dolask|odlask', raw or ""))
            print("  200 ✅ %-22s %s" % (name, "(enthält Datumsfelder!)" if has_date else ""))
            print("        %s" % snip(raw, 260))
            hits.append((name, raw, has_date))
        elif code in (401, 403):
            print("  %d 🔒 %s" % (code, name))
        elif code != 404:
            print("  %-4s   %-22s %s" % (code, name, snip(raw, 80)))

    print("\n--- Zusammenfassung ---")
    if hits:
        best = next((h for h in hits if h[2]), hits[0])
        print("Beste Kandidaten: %s" % ", ".join(h[0] for h in hits))
        print("\n--- Vollausschnitt %s (erste 1500 Zeichen) ---" % best[0])
        print((best[1] or "")[:1500])
        print("\nBitte KOMPLETTE Ausgabe schicken – daraus lese ich Ressource und")
        print("Feldnamen ab und trage sie fest in die App ein.")
    else:
        print("Login + Auth OK, aber keine geratene Ressource passte (alle 404).")
        print("Bitte Ausgabe schicken – dann sind die Entitäten anders benannt und")
        print("ich erweitere die Kandidatenliste gezielt.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
