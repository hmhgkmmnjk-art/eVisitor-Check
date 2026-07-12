#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN – read-only)  v4  – Auth-Introspektion
===================================================================

Der Login liefert 200, aber Folgeaufrufe kommen als 401 zurück. Diese
Version legt offen, WAS der Login zurückgibt (Set-Cookie / Body / Token)
und was die geschützte Ressource antwortet (inkl. WWW-Authenticate), damit
die richtige Authentifizierung für die Folgeaufrufe gefunden werden kann.

SICHERHEIT: nur GET plus GENAU EIN Login-POST. Kein Schreiben.

Nutzung:
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/claude/evisitor-overnight-stays-74xv0o/evisitor_diag.py"
    python3 evisitor_diag.py 57344933760 DEINPASSWORT
Komplette Ausgabe bitte kopieren und schicken. (Cookie-WERTE sind
geschwärzt – nur Namen werden gezeigt.)
"""

import sys
import json
import ssl
import datetime as dt
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar

LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login"

# testApi hat sich als funktionierend erwiesen; Produktion zusätzlich mittesten.
CANDIDATE_BASES = [
    "https://www.evisitor.hr/testApi",
    "https://www.evisitor.hr/eVisitorApi",
    "https://www.evisitor.hr/api",
    "https://www.evisitor.hr/webApi",
]

# Discovery-Pfade (nur lesen) – Country ist das dokumentierte Beispiel.
PROBE_PATHS = [
    "/Rest/Htz/Country/",
    "/Rest/Htz/Country",
    "/Rest/Htz",
    "/Rest",
    "/",
    "/$metadata",
    "/Rest/$metadata",
]

FORBIDDEN = ("checkin", "checkout", "prijav", "odjav", "save", "import",
             "new", "create", "update", "delete", "insert", "add")
TIMEOUT = 20


def assert_read_only(method, url):
    m = method.upper()
    low = urllib.parse.urlparse(url).path.rstrip("/").lower()
    if m == "GET":
        for bad in FORBIDDEN:
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
    op.addheaders = [("User-Agent", "eVisitor-Diag/4 (read-only)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op, cj


def redact_headers(headers):
    """Header anzeigen, Cookie-/Token-WERTE aber schwärzen."""
    out = []
    for k, v in headers.items():
        kl = k.lower()
        if kl in ("set-cookie", "authorization"):
            # nur den Namen vor '=' zeigen
            name = v.split("=", 1)[0].split(";")[0]
            out.append("%s: %s=<geschwärzt>" % (k, name))
        else:
            out.append("%s: %s" % (k, v))
    return out


def body_snip(raw, n=500):
    if not raw:
        return "(leer)"
    s = raw.strip().replace("\r", "")
    return s[:n] + (" …" if len(s) > n else "")


def login(op, base, user, pw):
    url = base + LOGIN_PATH
    assert_read_only("POST", url)
    body = json.dumps({"userName": user, "password": pw, "rememberMe": False}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, dict(r.headers.items()), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, {}, str(e)


def get(op, base, path):
    url = base + path
    assert_read_only("GET", url)
    req = urllib.request.Request(url, method="GET")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, dict(r.headers.items()), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, {}, str(e)


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print("=" * 64)
    print(" eVisitor Diagnose v4 – Auth-Introspektion (NUR LESEN)")
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

    # 1) Basis finden, die den Login mit 200 annimmt
    print("\n--- 1) Login je Basis ---")
    chosen = None
    for base in CANDIDATE_BASES:
        op, cj = new_session()
        code, hdrs, raw = login(op, base, user, pw)
        tag = base.split("/")[-1]
        if code == 200:
            print("  %-14s -> 200 ✅" % tag)
            if chosen is None:
                chosen = (base, op, cj, hdrs, raw)
        elif code in (400, 401, 403):
            print("  %-14s -> %d (existiert, Login abgelehnt)" % (tag, code))
        elif code == 404:
            print("  %-14s -> 404" % tag)
        else:
            print("  %-14s -> Fehler: %s" % (tag, str(raw)[:120]))

    if not chosen:
        print("\nKein Login mit 200. Ausgabe bitte schicken.")
        return

    base, op, cj, login_hdrs, login_body = chosen
    print("\n--- 2) Login-Antwort von %s ---" % base)
    print("Response-Header:")
    for line in redact_headers(login_hdrs):
        print("   " + line)
    print("Body (Auszug): %s" % body_snip(login_body, 400))
    cookie_names = [c.name for c in cj]
    print("Gesetzte Cookies (Namen): %s" % (", ".join(cookie_names) or "KEINE ⚠️"))

    # 3) Geschützte Ressource + Discovery abfragen (mit derselben Sitzung)
    print("\n--- 3) Lese-Discovery (gleiche Sitzung) ---")
    for path in PROBE_PATHS:
        code, hdrs, raw = get(op, base, path)
        line = "  %-4s  %s" % (code, path)
        www = hdrs.get("WWW-Authenticate") or hdrs.get("www-authenticate")
        ctype = hdrs.get("Content-Type") or hdrs.get("content-type") or ""
        print(line + ("   [%s]" % ctype.split(";")[0] if ctype else ""))
        if www:
            print("        WWW-Authenticate: %s" % www)
        if code == 200 and raw:
            print("        Body: %s" % body_snip(raw, 300))
        elif code not in (404,) and raw:
            print("        Body: %s" % body_snip(raw, 200))

    print("\n--- Fertig. Bitte komplette Ausgabe schicken. ---")
    print("Wichtig für mich: welche Cookies der Login setzt und ob /Rest/Htz/Country/")
    print("mit dieser Sitzung 200 oder 401 liefert.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
