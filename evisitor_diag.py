#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eVisitor Diagnose (NUR LESEN – read-only)  v3
=============================================

Findet heraus, welche API-Basisadresse mit deinem Login funktioniert und
welcher Endpunkt die Gäste-/Übernachtungsdaten liefert. Ändert NICHTS.

Behandelt den veralteten, zu schwachen TLS-Schlüssel (DH_KEY_TOO_SMALL) von
evisitor.hr auf zwei Wegen:
  1) Python-TLS mit gesenkter Sicherheitsstufe (SECLEVEL=0)
  2) automatischer Rückfall auf das externe Programm `curl`

SICHERHEIT: nur GET-Abfragen plus GENAU EIN Login-POST. Kein Schreiben,
keine Gäste-Anmeldung. Es werden nur Substantiv-Ressourcen gelesen.

Nutzung in a-Shell:
    cd Documents
    curl -L -o evisitor_diag.py "https://raw.githubusercontent.com/hmhgkmmnjk-art/eVisitor-Check/claude/evisitor-overnight-stays-74xv0o/evisitor_diag.py"
    python3 evisitor_diag.py 57344933760 DEINPASSWORT
Die komplette Ausgabe bitte kopieren und mir schicken.
"""

import sys
import os
import json
import ssl
import shutil
import tempfile
import subprocess
import datetime as dt
import urllib.request
import urllib.error
import urllib.parse
import http.cookiejar

LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login"

CANDIDATE_BASES = [
    "https://www.evisitor.hr/eVisitorApi",
    "https://www.evisitor.hr/testApi",
    "https://www.evisitor.hr/api",
]

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

FORBIDDEN = ("checkin", "checkout", "prijav", "odjav", "save", "import",
             "new", "create", "update", "delete", "insert", "add")

TIMEOUT = 20
CURL = shutil.which("curl") or ("curl" if os.path.exists("/usr/bin/curl") else None)


# ---------------------------------------------------------------------------
# Sicherheitssperre
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Backend 1: Python-TLS (SECLEVEL=0)
# ---------------------------------------------------------------------------
def make_ssl_context():
    ctx = ssl.create_default_context()
    for spec in ("DEFAULT@SECLEVEL=0", "ALL@SECLEVEL=0"):
        try:
            ctx.set_ciphers(spec)
            break
        except ssl.SSLError:
            continue
    return ctx


def py_opener():
    cj = http.cookiejar.CookieJar()
    https = urllib.request.HTTPSHandler(context=make_ssl_context())
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), https)
    op.addheaders = [("User-Agent", "eVisitor-Diag/3 (read-only)"),
                     ("Accept", "application/json, text/plain, */*")]
    return op


def py_login(base, user, pw):
    url = base + LOGIN_PATH
    assert_read_only("POST", url)
    body = json.dumps({"userName": user, "password": pw, "rememberMe": False}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    op = py_opener()
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            r.read()
        return op, 200, ""
    except urllib.error.HTTPError as e:
        return op, e.code, ""
    except Exception as e:
        return None, None, str(e)


def py_get(op, base, path):
    url = base + path
    assert_read_only("GET", url)
    req = urllib.request.Request(url, method="GET")
    try:
        with op.open(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# Backend 2: curl (eigene TLS-Bibliothek, verträgt den alten Server oft)
# ---------------------------------------------------------------------------
def curl_run(method, url, cookie_file, data=None):
    assert_read_only(method, url)
    marker = "\n__HTTP__%{http_code}"
    base_cmd = [CURL, "-sS", "-m", str(TIMEOUT), "-w", marker,
                "-c", cookie_file, "-b", cookie_file]
    if method == "POST":
        base_cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", data or "{}"]
    # Erst mit erlaubtem schwachem DH, dann notfalls ohne die Option:
    for extra in (["--ciphers", "DEFAULT@SECLEVEL=0"], []):
        try:
            out = subprocess.run(base_cmd + extra + [url],
                                 capture_output=True, text=True, timeout=TIMEOUT + 5)
        except Exception as e:
            return None, "", str(e)
        stdout, _, code = out.stdout.rpartition("__HTTP__")
        code = code.strip()
        if code and code != "000":
            return int(code), stdout, ""
        # wenn die --ciphers-Option nicht unterstützt wird, ohne sie erneut versuchen
        if "option" in (out.stderr or "").lower() and extra:
            continue
        return (None, "", (out.stderr or "").strip())
    return None, "", "curl: kein HTTP-Code"


def curl_login(base, user, pw, cookie_file):
    data = json.dumps({"userName": user, "password": pw, "rememberMe": False})
    return curl_run("POST", base + LOGIN_PATH, cookie_file, data)


def curl_get(base, path, cookie_file):
    return curl_run("GET", base + path, cookie_file)


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------
def resource_url(res, df, dtx):
    if res in ("/Rest/Htz/Country/", "/Rest/Htz/"):
        return res
    return res + "?datumOd=%s&datumDo=%s" % (df, dtx)


def summarize(found):
    print("\n--- Zusammenfassung ---")
    if found:
        print("Diese Ressourcen liefern Daten (200) – bitte 'Beispiel:'-Zeilen schicken.")
        for res, raw in found:
            if res not in ("/Rest/Htz/Country/", "/Rest/Htz/"):
                print("\n--- Vollausschnitt %s (erste 1500 Zeichen) ---" % res)
                print((raw or "")[:1500])
                break
    else:
        print("Login OK, aber keine geratene Gäste-Ressource passte.")
        print("Bitte in der eingeloggten Web-API-Wiki den Htz-Ressourcennamen")
        print("für Gäste/Übernachtungen nachsehen und mir nennen.")


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    print("=" * 62)
    print(" eVisitor Diagnose v3 – NUR LESEN (ändert nichts)")
    print("=" * 62)
    print("TLS-Bibliothek (Python): %s" % ssl.OPENSSL_VERSION)
    print("curl verfügbar: %s" % ("ja (" + CURL + ")" if CURL else "NEIN"))

    if len(sys.argv) >= 3:
        user, pw = sys.argv[1].strip(), sys.argv[2]
        print("Benutzername: %s (aus Aufruf übernommen)" % user)
    else:
        print("Tipp: python3 evisitor_diag.py BENUTZER PASSWORT")
        user = input("Benutzername: ").strip()
        pw = input("Passwort (sichtbar): ").strip()
    if not user or not pw:
        print("Abbruch: Benutzername und Passwort nötig.")
        return

    today = dt.date.today()
    df = dt.date(today.year, 1, 1).isoformat()
    dtx = today.isoformat()

    # ---- Versuch 1: Python-TLS (SECLEVEL=0) ----
    print("\n--- Versuch 1: Python-TLS (SECLEVEL=0) ---")
    working = None      # (base, backend, handle)
    ssl_problem = False
    for base in CANDIDATE_BASES:
        tag = base.split("/")[-1]
        op, code, err = py_login(base, user, pw)
        if code == 200:
            print("  %-14s Login -> 200  ✅ LOGIN OK (Python)" % tag)
            working = (base, "py", op)
            break
        elif code in (400, 401, 403):
            print("  %-14s Login -> %d (Endpunkt existiert, Login abgelehnt)" % (tag, code))
        elif code == 404:
            print("  %-14s Login -> 404 (Adresse falsch)" % tag)
        else:
            print("  %-14s Fehler: %s" % (tag, err))
            if err and ("SSL" in err or "DH_KEY" in err or "dh key" in err):
                ssl_problem = True

    # ---- Versuch 2: curl-Rückfall, falls Python an TLS scheitert ----
    cookie_file = os.path.join(tempfile.gettempdir(), "evisitor_diag_cookies.txt")
    if not working and CURL:
        print("\n--- Versuch 2: Rückfall auf curl ---")
        for base in CANDIDATE_BASES:
            tag = base.split("/")[-1]
            try:
                if os.path.exists(cookie_file):
                    os.remove(cookie_file)
            except Exception:
                pass
            code, body, err = curl_login(base, user, pw, cookie_file)
            if code == 200:
                print("  %-14s Login -> 200  ✅ LOGIN OK (curl)" % tag)
                working = (base, "curl", cookie_file)
                break
            elif code in (400, 401, 403):
                print("  %-14s Login -> %d (Endpunkt existiert, Login abgelehnt)" % (tag, code))
            elif code == 404:
                print("  %-14s Login -> 404 (Adresse falsch)" % tag)
            else:
                print("  %-14s curl-Fehler: %s" % (tag, err[:160]))

    if not working:
        print("\nKein Login erfolgreich.")
        if ssl_problem and not CURL:
            print("Ursache: TLS (schwacher DH-Schlüssel) UND kein curl gefunden.")
        print("Bitte die komplette Ausgabe oben schicken.")
        return

    base, backend, handle = working
    print("\nFunktionierende Basis: %s   (Backend: %s)" % (base, backend))
    print("\n--- Lesende Ressourcen testen (GET) ---")
    found = []
    for res in CANDIDATE_RESOURCES:
        url = resource_url(res, df, dtx)
        if backend == "py":
            code, raw = py_get(handle, base, url)
        else:
            code, raw, _ = curl_get(base, url, handle)
        sample = (raw[:160].replace("\n", " ")) if isinstance(raw, str) else ""
        if code == 200:
            print("  200 ✅  %s" % res)
            print("        Beispiel: %s" % sample)
            found.append((res, raw))
        elif code in (401, 403):
            print("  %d 🔒  %s" % (code, res))
        elif code == 404:
            print("  404     %s" % res)
        else:
            print("  %-4s    %s  %s" % (code, res, sample[:60]))

    summarize(found)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
