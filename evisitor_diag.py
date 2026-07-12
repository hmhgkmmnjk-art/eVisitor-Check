#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN – read-only)  v6  – mit API-Schlüssel (apikey)
===========================================================================

Erkenntnis: Die Web-API-Anmeldung braucht userName + password + APIKEY.
Ohne apikey antwortet der Login mit Body "false" (das sahen wir). Der apikey
ersetzt bei der API die TAN-Liste der Website. Er muss im eVisitor-System
freigeschaltet/erzeugt werden (ggf. über die Turistička zajednica / HTZ).

Diese Diagnose probiert mehrere Login-Formate (Feldnamen/Groß-/Kleinschreibung,
apikey im Body oder als Header) durch und meldet, welches "true" liefert.

SICHERHEIT: nur GET (Lesen) plus Login-POSTs. Schreibende Methoden hart
blockiert.

Nutzung:
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/claude/evisitor-overnight-stays-74xv0o/evisitor_diag.py?cb=6"
    python3 evisitor_diag.py BENUTZER PASSWORT APISCHLUESSEL
(ohne apikey testet es nur, ob – wie erwartet – "false" kommt)
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
    "https://www.evisitor.hr/eVisitorApi",
    "https://www.evisitor.hr/testApi",
    "https://www.evisitor.hr/api",
    "https://www.evisitor.hr/webApi",
]

GUEST_RESOURCES = [
    "Turist", "Turisti", "Tourist", "Tourists",
    "TouristCheckIn", "TouristCheckin", "CheckIn",
    "Prijava", "Prijave", "PrijavaTurista",
    "EvidencijaGostiju", "Gost", "Gosti",
    "Boravak", "Boravci", "Nocenje", "Nocenja", "PopisTurista",
]

FORBIDDEN_GET = ("save", "import", "create", "update", "delete", "insert")
TIMEOUT = 20


def build_login_variants(u, p, k):
    """Liste (Name, body_dict, extra_headers) von Login-Formaten."""
    if k:
        return [
            ("userName/password/apikey (body)",
             {"userName": u, "password": p, "apikey": k}, {}),
            ("UserName/Password/ApiKey (body)",
             {"UserName": u, "Password": p, "ApiKey": k}, {}),
            ("userName/password/apiKey (body)",
             {"userName": u, "password": p, "apiKey": k}, {}),
            ("userName/password + Header apikey",
             {"userName": u, "password": p}, {"apikey": k}),
            ("UserName/Password/PersistCookie + Header apikey",
             {"UserName": u, "Password": p, "PersistCookie": False}, {"apikey": k}),
        ]
    return [
        ("UserName/Password/PersistCookie (ohne apikey)",
         {"UserName": u, "Password": p, "PersistCookie": False}, {}),
        ("userName/password/rememberMe (ohne apikey)",
         {"userName": u, "password": p, "rememberMe": False}, {}),
    ]


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
    op.addheaders = [("User-Agent", "eVisitor-Diag/6 (read-only)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op, cj


def try_login(base, body, extra_headers):
    url = base + LOGIN_PATH
    assert_read_only("POST", url)
    op, cj = new_session()
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for hk, hv in (extra_headers or {}).items():
        req.add_header(hk, hv)
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(200).decode("utf-8", "replace").strip(), op, cj
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace").strip(), op, cj
    except Exception as e:
        return None, str(e)[:100], None, None


def get(op, base, path):
    url = base + path
    assert_read_only("GET", url)
    req = urllib.request.Request(url, method="GET")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read(400000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(500).decode("utf-8", "replace")
    except Exception as e:
        return None, str(e)


def snip(raw, n=240):
    return (raw or "").strip().replace("\n", " ")[:n]


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print("=" * 66)
    print(" eVisitor Diagnose v6 – mit API-Schlüssel (NUR LESEN)")
    print("=" * 66)
    print("TLS: %s" % ssl.OPENSSL_VERSION)

    args = [a for a in sys.argv[1:]]
    if len(args) >= 2:
        user, pw = args[0].strip(), args[1]
        apikey = args[2].strip() if len(args) >= 3 else ""
    else:
        user = input("Benutzername: ").strip()
        pw = input("Passwort (sichtbar): ").strip()
        apikey = input("API-Schlüssel (leer lassen, falls keiner): ").strip()
    if not user or not pw:
        print("Abbruch: Benutzername und Passwort nötig.")
        return
    print("Benutzername: %s" % user)
    print("API-Schlüssel: %s" % ("vorhanden (%d Zeichen)" % len(apikey) if apikey else "KEINER angegeben"))

    variants = build_login_variants(user, pw, apikey)

    print("\n--- 1) Login-Formate testen (Erfolg = Body 'true' + Cookie) ---")
    session = None
    for base in BASES:
        tag = base.split("/")[-1]
        for name, body, hdrs in variants:
            code, resp, op, cj = try_login(base, body, hdrs)
            if code is None:
                print("  %-10s %-42s Fehler: %s" % (tag, name, resp))
                continue
            if code == 404:
                # Basis hat den Login-Pfad nicht -> restliche Varianten überspringen
                print("  %-10s (Login-Pfad 404 – Basis übersprungen)" % tag)
                break
            cookies = [c.name for c in cj] if cj else []
            auth = [c for c in cookies if "auth" in c.lower()]
            success = (code == 200 and resp.lower() == "true")
            print("  %-10s %-42s HTTP %s Body=%-6s Cookie=%s %s"
                  % (tag, name, code, (resp[:6] or "-"),
                     ",".join(auth) or "-", "✅" if success else ""))
            if success and session is None:
                session = (base, op, name)

    if not session:
        print("\nKein Login lieferte 'true'.")
        if not apikey:
            print("→ Erwartungsgemäß: ohne API-Schlüssel bleibt es 'false'.")
            print("  Bitte einen API-Schlüssel im eVisitor-System freischalten und")
            print("  dann: python3 evisitor_diag.py %s DEINPASSWORT APISCHLUESSEL" % user)
        else:
            print("→ Mit API-Schlüssel klappte es trotzdem nicht. Mögliche Gründe:")
            print("  Schlüssel noch nicht aktiv, anderes Login-Format, oder falsche Basis.")
            print("  Bitte komplette Ausgabe schicken.")
        return

    base, op, used = session
    print("\n✅ Login erfolgreich!  Basis: %s   Format: %s" % (base, used))

    print("\n--- 2) Auth-Kontrolle: /Rest/Htz/Country/ ---")
    code, raw = get(op, base, "/Rest/Htz/Country/")
    print("  HTTP %s  %s" % (code, "✅ authentifiziert" if code == 200 else "⚠️ " + snip(raw, 120)))

    print("\n--- 3) Gäste-/Übernachtungs-Ressourcen suchen (GET) ---")
    hits = []
    for name in GUEST_RESOURCES:
        code, raw = get(op, base, "/Rest/Htz/%s/?top=1" % name)
        if code == 400:
            code, raw = get(op, base, "/Rest/Htz/%s/" % name)
        if code == 200:
            has_date = bool(re.search(r'atum|Date|dolask|odlask', raw or ""))
            print("  200 ✅ %-20s %s" % (name, "(Datumsfelder!)" if has_date else ""))
            print("        %s" % snip(raw, 260))
            hits.append((name, raw, has_date))
        elif code in (401, 403):
            print("  %d 🔒 %s" % (code, name))
        elif code != 404:
            print("  %-4s   %s %s" % (code, name, snip(raw, 70)))

    print("\n--- Zusammenfassung ---")
    if hits:
        best = next((h for h in hits if h[2]), hits[0])
        print("Treffer: %s" % ", ".join(h[0] for h in hits))
        print("\n--- Vollausschnitt %s (erste 1500 Zeichen) ---" % best[0])
        print((best[1] or "")[:1500])
    print("\nBitte komplette Ausgabe schicken – dann trage ich Basis, Login-Format,")
    print("Ressource und Feldnamen final in die App ein.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
