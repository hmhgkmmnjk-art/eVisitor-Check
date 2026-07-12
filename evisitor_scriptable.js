// ===========================================================================
// eVisitor – Übernachtungen (noćenja) für die iPad-App "Scriptable"
// ===========================================================================
// Version OHNE a-Shell / Terminal / localhost:
//   1. Gratis-App "Scriptable" aus dem App Store installieren.
//   2. Diese Datei in den Ordner iCloud Drive > Scriptable legen
//      (oder Inhalt in ein neues Scriptable-Skript einfügen).
//   3. In Scriptable antippen – fertig.
//
// Die Netzwerkaufrufe macht iOS nativ (kein CORS, Cookies automatisch,
// auch der alte TLS-Schlüssel von evisitor.hr ist kein Problem).
//
// SICHERHEIT: Es werden ausschließlich GET-Abfragen (Lesen) plus der
// Login/Logout-POST gesendet. Jeder andere Zugriff wird von assertReadOnly()
// hart blockiert – Gäste-Anmeldungen oder Änderungen sind unmöglich.
// Passwörter werden nur im Speicher gehalten; gemerkt werden (optional über
// iOS-Schlüsselbund) nur Benutzernamen und die Sprachwahl.
// ===========================================================================

"use strict";

// ----------------------------- Konfiguration ------------------------------
const BASE = "https://www.evisitor.hr/eVisitorRhetos_API";
const LOGIN_PATH = "/Resources/AspNetFormsAuth/Authentication/Login";
const LOGOUT_PATH = "/Resources/AspNetFormsAuth/Authentication/Logout";
const REPORT_PATH = "/Rest/Htz/Tourist/";
const ARRIVAL_FIELD = "TimeStayFrom";     // Anreise (Filterfeld)
const CHECKOUT_FIELD = "CheckOutTime";    // Abreise (leer = noch anwesend)
const LOOKBACK_DAYS = 370;                // Vorlauf für hineinragende Aufenthalte
const PSIZE = 500;
const KC_USERS = "evisitor_usernames";
const KC_LANG = "evisitor_lang";

// ------------------------------ Übersetzungen -----------------------------
const I18N = {
  de: {
    title: "eVisitor – Übernachtungen",
    subtitle: "Nur lesend · Passwörter werden nicht gespeichert",
    user: "Benutzername", pass: "Passwort",
    runYear: "Abfragen: laufendes Jahr", runOther: "Abfragen: anderes Jahr",
    runRange: "Abfragen: Von–Bis", langBtn: "Jezik: Hrvatski",
    cancel: "Abbrechen", ok: "OK",
    yearTitle: "Jahr wählen", yearField: "Jahr (z. B. 2024)",
    rangeTitle: "Zeitraum wählen", fromField: "Von (JJJJ-MM-TT)", toField: "Bis (JJJJ-MM-TT)",
    vAccount: "Bitte für mindestens einen Account Benutzername UND Passwort eingeben.",
    vPeriod: "Ungültiger Zeitraum. Bitte Format JJJJ-MM-TT verwenden.",
    vOrder: "Das Bis-Datum liegt vor dem Von-Datum.",
    errLogin: "Login fehlgeschlagen (Benutzername/Passwort falsch).",
    errHttp: "Fehler beim Datenabruf (HTTP {s}).",
    errConn: "Keine Verbindung zum Server.",
    running: "Frage ab … Account {i} von {n}",
    result: "Ergebnis", period: "Zeitraum",
    kpiNights: "Übernachtungen gesamt", kpiOpen: "davon offen (nicht abgemeldet)",
    kpiGuests: "Gäste gesamt",
    perAccount: "Pro Account", thAccount: "Account", thNights: "Übernacht.",
    thOpen: "davon offen", thGuests: "Gäste", total: "GESAMT",
    perMonth: "Übernachtungen pro Monat", guestsInPeriod: "Angemeldete Gäste im Zeitraum",
    thGuest: "Gast", thArrival: "Anreise", thDeparture: "Abreise", thNightsShort: "Nächte",
    open: "offen", guestsWord: "Gäste", nightsWord: "Nächte",
    noGuests: "Keine Gäste im Zeitraum.", noMonth: "Keine Monatsdaten im Zeitraum.",
    errWord: "Fehler", footer: "lokal auf dem iPad berechnet",
    between: "bis",
    months: ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
  },
  hr: {
    title: "eVisitor – Noćenja",
    subtitle: "Samo čitanje · lozinke se ne spremaju",
    user: "Korisničko ime", pass: "Lozinka",
    runYear: "Dohvati: tekuća godina", runOther: "Dohvati: druga godina",
    runRange: "Dohvati: Od–Do", langBtn: "Sprache: Deutsch",
    cancel: "Odustani", ok: "U redu",
    yearTitle: "Odaberite godinu", yearField: "Godina (npr. 2024)",
    rangeTitle: "Odaberite razdoblje", fromField: "Od (GGGG-MM-DD)", toField: "Do (GGGG-MM-DD)",
    vAccount: "Unesite korisničko ime I lozinku za barem jedan račun.",
    vPeriod: "Neispravno razdoblje. Koristite format GGGG-MM-DD.",
    vOrder: "Datum Do je prije datuma Od.",
    errLogin: "Prijava nije uspjela (pogrešno korisničko ime/lozinka).",
    errHttp: "Greška pri dohvaćanju podataka (HTTP {s}).",
    errConn: "Nema veze s poslužiteljem.",
    running: "Dohvaćam … račun {i} od {n}",
    result: "Rezultat", period: "Razdoblje",
    kpiNights: "Ukupno noćenja", kpiOpen: "od toga otvoreno (bez odjave)",
    kpiGuests: "Ukupno gostiju",
    perAccount: "Po računu", thAccount: "Račun", thNights: "Noćenja",
    thOpen: "otvoreno", thGuests: "Gosti", total: "UKUPNO",
    perMonth: "Noćenja po mjesecu", guestsInPeriod: "Prijavljeni gosti u razdoblju",
    thGuest: "Gost", thArrival: "Dolazak", thDeparture: "Odlazak", thNightsShort: "Noći",
    open: "otvoreno", guestsWord: "gostiju", nightsWord: "noći",
    noGuests: "Nema gostiju u razdoblju.", noMonth: "Nema mjesečnih podataka.",
    errWord: "Greška", footer: "izračunato lokalno na iPadu",
    between: "do",
    months: ["Sij", "Velj", "Ožu", "Tra", "Svi", "Lip", "Srp", "Kol", "Ruj", "Lis", "Stu", "Pro"],
  },
};

// --------------------------- Sicherheitssperre ----------------------------
function assertReadOnly(method, url) {
  const m = String(method).toUpperCase();
  const path = url.split("?")[0].toLowerCase();
  if (m === "GET") {
    for (const bad of ["save", "import", "create", "update", "delete", "insert"]) {
      if (path.includes(bad)) throw new Error("SICHERHEIT: Pfad blockiert (" + path + ")");
    }
    return;
  }
  if (m === "POST" && (path.endsWith("/authentication/login") ||
                       path.endsWith("/authentication/logout"))) return;
  throw new Error("SICHERHEIT: nicht-lesender Zugriff blockiert (" + m + ")");
}

// ------------------------------ Datums-Helfer -----------------------------
// Alle Daten intern als Tagesnummer (Tage seit 1.1.1970, lokalisiert).
const DAY = 86400000;

function dayOf(y, m, d) { return Math.floor(Date.UTC(y, m - 1, d) / DAY); }

function parseNetDate(v) {
  // "/Date(1725208200000+0200)/" -> Tagesnummer im LOKALEN Kalender des Servers
  if (v === null || v === undefined || v === "") return null;
  const s = String(v).trim();
  let m = s.match(/^\/Date\((-?\d+)([+-])(\d{2})(\d{2})\)\/$/);
  if (m) {
    const ms = parseInt(m[1], 10);
    const sign = m[2] === "+" ? 1 : -1;
    const offMin = sign * (parseInt(m[3], 10) * 60 + parseInt(m[4], 10));
    return Math.floor((ms + offMin * 60000) / DAY);
  }
  m = s.match(/^\/Date\((-?\d+)\)\/$/);
  if (m) return Math.floor(parseInt(m[1], 10) / DAY);
  m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);   // ISO
  if (m) return dayOf(+m[1], +m[2], +m[3]);
  return null;
}

function isoOfDay(day) {
  const d = new Date(day * DAY);
  const p = (n) => (n < 10 ? "0" : "") + n;
  return d.getUTCFullYear() + "-" + p(d.getUTCMonth() + 1) + "-" + p(d.getUTCDate());
}

function ymOfDay(day) {
  const d = new Date(day * DAY);
  return [d.getUTCFullYear(), d.getUTCMonth() + 1];
}

// Nächte je Monat für [startDay, endExclDay)
function monthsBetween(startDay, endExclDay) {
  const res = {};
  let cur = startDay;
  while (cur < endExclDay) {
    const [y, m] = ymOfDay(cur);
    const nextMonth = dayOf(m === 12 ? y + 1 : y, m === 12 ? 1 : m + 1, 1);
    const segEnd = Math.min(nextMonth, endExclDay);
    const key = y + "-" + (m < 10 ? "0" : "") + m;
    res[key] = (res[key] || 0) + (segEnd - cur);
    cur = segEnd;
  }
  return res;
}

// --------------------- Übernachtungs-Berechnung (noćenja) -----------------
function computeAccount(records, fromDay, toDay, todayDay) {
  const rangeEndExcl = toDay + 1;
  let totalNights = 0, openNights = 0, guests = 0, openGuests = 0;
  const monthly = {}, guestList = [];
  for (const rec of records) {
    if (!rec || typeof rec !== "object") continue;
    const ci = parseNetDate(rec[ARRIVAL_FIELD] || rec.StayFrom);
    const co = parseNetDate(rec[CHECKOUT_FIELD] || rec.CheckOutDate);
    if (ci === null) continue;
    const isOpen = (co === null);
    const checkoutExcl = isOpen ? Math.min(todayDay, rangeEndExcl)
                                : Math.min(co, rangeEndExcl);
    const start = Math.max(ci, fromDay);
    const nights = checkoutExcl - start;
    if (nights <= 0) continue;
    totalNights += nights;
    guests += 1;
    const mb = monthsBetween(start, checkoutExcl);
    for (const k in mb) monthly[k] = (monthly[k] || 0) + mb[k];
    if (isOpen) { openNights += nights; openGuests += 1; }
    const name = [rec.TouristName, rec.TouristSurname].filter(Boolean).join(" ") || "—";
    guestList.push({ name, checkin: isoOfDay(ci),
                     checkout: isOpen ? null : isoOfDay(co), nights, open: isOpen });
  }
  guestList.sort((a, b) => a.checkin < b.checkin ? -1 : 1);
  return { total_nights: totalNights, open_nights: openNights,
           guests, open_guests: openGuests, monthly, guest_list: guestList };
}

// ------------------------------- Netzwerk ---------------------------------
async function http(method, url, bodyObj) {
  assertReadOnly(method, url);
  const r = new Request(url);
  r.method = method;
  r.headers = { "Accept": "application/json", "Content-Type": "application/json" };
  if (bodyObj !== undefined) r.body = JSON.stringify(bodyObj);
  r.timeoutInterval = 30;
  const text = await r.loadString();
  const status = (r.response && r.response.statusCode) || 0;
  return { status, text };
}

async function apiLogin(user, pw, T) {
  let res;
  try {
    res = await http("POST", BASE + LOGIN_PATH,
                     { userName: user, password: pw, PersistCookie: false });
  } catch (e) { throw new Error(T.errConn); }
  if (res.status !== 200 || res.text.trim().toLowerCase() !== "true")
    throw new Error(T.errLogin);
}

async function apiLogoutQuiet() {
  try { await http("POST", BASE + LOGOUT_PATH, {}); } catch (e) { /* egal */ }
}

async function fetchRecords(fromDay, toDay, T) {
  const filters = [
    { Property: ARRIVAL_FIELD, Operation: "greaterequal",
      Value: isoOfDay(fromDay - LOOKBACK_DAYS) + "T00:00:00" },
    { Property: ARRIVAL_FIELD, Operation: "lessequal",
      Value: isoOfDay(toDay) + "T23:59:59" },
  ];
  const fenc = encodeURIComponent(JSON.stringify(filters));
  const all = [];
  for (let page = 1; page <= 100; page++) {
    const url = BASE + REPORT_PATH + "?sort=ID&page=" + page +
                "&psize=" + PSIZE + "&filters=" + fenc;
    let res;
    try { res = await http("GET", url); }
    catch (e) { throw new Error(T.errConn); }
    if (res.status !== 200) throw new Error(T.errHttp.replace("{s}", res.status));
    let data;
    try { data = JSON.parse(res.text); } catch (e) { throw new Error(T.errHttp.replace("{s}", "JSON")); }
    const recs = (data && (data.Records || data.records)) || [];
    all.push(...recs);
    if (recs.length < PSIZE) break;
  }
  return all;
}

// ------------------------------ Report (HTML) -----------------------------
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildReport(results, fromDay, toDay, lang) {
  const T = I18N[lang];
  let totN = 0, totO = 0, totG = 0;
  const months = {};
  let rows = "";
  for (const r of results) {
    if (!r.ok) {
      rows += '<tr class="err"><td>' + esc(r.username) + '</td><td colspan="3">' +
              esc(T.errWord + ": " + r.error) + "</td></tr>";
      continue;
    }
    const s = r.stats;
    totN += s.total_nights; totO += s.open_nights; totG += s.guests;
    for (const k in s.monthly) months[k] = (months[k] || 0) + s.monthly[k];
    rows += "<tr><td>" + esc(r.username) + '</td><td class="num big">' + s.total_nights +
            '</td><td class="num">' + s.open_nights + '</td><td class="num">' + s.guests + "</td></tr>";
  }
  rows += '<tr class="total"><td>' + T.total + '</td><td class="num big">' + totN +
          '</td><td class="num">' + totO + '</td><td class="num">' + totG + "</td></tr>";

  const keys = Object.keys(months).sort();
  let max = 0; keys.forEach((k) => { if (months[k] > max) max = months[k]; });
  if (!max) max = 1;
  let bars = "";
  for (const k of keys) {
    const [y, m] = k.split("-");
    const lab = T.months[parseInt(m, 10) - 1] + " " + y;
    const pct = Math.round(months[k] / max * 100);
    bars += '<div class="barrow"><div class="barlabel">' + lab + '</div>' +
            '<div class="bartrack"><div class="bar" style="width:' + pct + '%"><span>' +
            months[k] + "</span></div></div></div>";
  }
  if (!bars) bars = '<p class="note">' + T.noMonth + "</p>";

  let gh = "";
  for (const r of results) {
    if (!r.ok) continue;
    const list = r.stats.guest_list || [];
    let g = "";
    for (const x of list) {
      const co = x.checkout ? x.checkout : '<span class="tagopen">' + T.open + "</span>";
      g += "<tr><td>" + esc(x.name) + "</td><td>" + x.checkin + "</td><td>" + co +
           '</td><td class="num">' + x.nights + "</td></tr>";
    }
    if (!g) g = '<tr><td colspan="4" class="note">' + T.noGuests + "</td></tr>";
    gh += "<details><summary><span>" + esc(r.username) + '</span><span class="badge">' +
          list.length + " " + T.guestsWord + " · " + r.stats.total_nights + " " + T.nightsWord +
          '</span></summary><table class="gtable"><thead><tr><th>' + T.thGuest + "</th><th>" +
          T.thArrival + "</th><th>" + T.thDeparture + '</th><th style="text-align:right">' +
          T.thNightsShort + "</th></tr></thead><tbody>" + g + "</tbody></table></details>";
  }

  const now = new Date();
  const p = (n) => (n < 10 ? "0" : "") + n;
  const gen = p(now.getDate()) + "." + p(now.getMonth() + 1) + "." + now.getFullYear() +
              " " + p(now.getHours()) + ":" + p(now.getMinutes());

  return `<!doctype html><html lang="${lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(T.title)}</title><style>
:root{--bg:#f4f6f9;--card:#fff;--ink:#1c2430;--muted:#6b7685;--accent:#2563eb;
--accent2:#1d4ed8;--line:#e3e8ef;--total:#eef4ff;--err:#c0392b}
@media (prefers-color-scheme:dark){:root{--bg:#0f141b;--card:#1a212b;--ink:#e8edf3;
--muted:#95a1b0;--accent:#3b82f6;--accent2:#60a5fa;--line:#2a333f;--total:#1e2836;--err:#f87171}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 -apple-system,sans-serif;
background:var(--bg);color:var(--ink);padding:16px}
.wrap{max-width:820px;margin:0 auto}h1{font-size:21px;margin:.2em 0}
.sub{color:var(--muted);margin-bottom:16px;font-size:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:16px;margin-bottom:14px}h2{font-size:16px;margin:.1em 0 .6em}
.kpis{display:flex;gap:10px;flex-wrap:wrap}
.kpi{flex:1;min-width:140px;background:var(--total);border-radius:12px;padding:12px 14px}
.kpi .n{font-size:28px;font-weight:700;color:var(--accent2)}
.kpi .l{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:10px 8px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase}
td.num{text-align:right}td.big{font-weight:700;color:var(--accent2)}
tr.total td{background:var(--total);font-weight:700;border-top:2px solid var(--accent)}
tr.err td{color:var(--err)}
.barrow{display:flex;align-items:center;gap:9px;margin:6px 0}
.barlabel{width:72px;font-size:12px;color:var(--muted);flex:none;text-align:right}
.bartrack{flex:1;background:var(--total);border-radius:7px;overflow:hidden;height:24px}
.bar{background:linear-gradient(90deg,var(--accent),var(--accent2));height:100%;
min-width:2px;border-radius:7px;display:flex;align-items:center;justify-content:flex-end}
.bar span{color:#fff;font-size:11px;font-weight:600;padding:0 7px}
details{border:1px solid var(--line);border-radius:10px;margin-bottom:9px;overflow:hidden}
summary{padding:11px 13px;cursor:pointer;font-weight:600;background:var(--total);
list-style:none;display:flex;justify-content:space-between;gap:8px}
summary::-webkit-details-marker{display:none}
summary .badge{font-weight:400;color:var(--muted);font-size:12px}
.tagopen{background:rgba(37,99,235,.15);color:var(--accent2);border-radius:6px;
padding:1px 6px;font-size:11px;font-weight:600}
.note{font-size:12px;color:var(--muted)}
footer{color:var(--muted);font-size:11px;text-align:center;margin-top:6px}
</style></head><body><div class="wrap">
<h1>${esc(T.title)}</h1>
<div class="sub">${T.period}: ${isoOfDay(fromDay)} ${T.between} ${isoOfDay(toDay)}</div>
<div class="card"><div class="kpis">
<div class="kpi"><div class="n">${totN}</div><div class="l">${T.kpiNights}</div></div>
<div class="kpi"><div class="n">${totO}</div><div class="l">${T.kpiOpen}</div></div>
<div class="kpi"><div class="n">${totG}</div><div class="l">${T.kpiGuests}</div></div>
</div></div>
<div class="card"><h2>${T.perAccount}</h2><table><thead><tr>
<th>${T.thAccount}</th><th style="text-align:right">${T.thNights}</th>
<th style="text-align:right">${T.thOpen}</th><th style="text-align:right">${T.thGuests}</th>
</tr></thead><tbody>${rows}</tbody></table></div>
<div class="card"><h2>${T.perMonth}</h2>${bars}</div>
<div class="card"><h2>${T.guestsInPeriod}</h2>${gh || '<p class="note">' + T.noGuests + "</p>"}</div>
<footer>${gen} · ${T.footer}</footer>
</div></body></html>`;
}

// ------------------------------- App (UI) ---------------------------------
async function msgAlert(text, T) {
  const a = new Alert();
  a.message = text;
  a.addCancelAction(T.ok);
  await a.present();
}

async function askYear(T) {
  const a = new Alert();
  a.title = T.yearTitle;
  a.addTextField(T.yearField, String(new Date().getFullYear() - 1));
  a.addAction(T.ok);
  a.addCancelAction(T.cancel);
  const idx = await a.present();
  if (idx === -1) return null;
  const y = parseInt(a.textFieldValue(0), 10);
  return (y >= 2000 && y <= 2100) ? y : null;
}

async function askRange(T) {
  const y = new Date().getFullYear();
  const a = new Alert();
  a.title = T.rangeTitle;
  a.addTextField(T.fromField, y + "-01-01");
  a.addTextField(T.toField, isoOfDay(Math.floor(Date.now() / DAY)));
  a.addAction(T.ok);
  a.addCancelAction(T.cancel);
  const idx = await a.present();
  if (idx === -1) return null;
  const f = parseNetDate(a.textFieldValue(0));
  const t = parseNetDate(a.textFieldValue(1));
  if (f === null || t === null) return { bad: "period" };
  if (t < f) return { bad: "order" };
  return { fromDay: f, toDay: t };
}

async function main() {
  let lang = "de";
  try { if (Keychain.contains(KC_LANG)) lang = Keychain.get(KC_LANG); } catch (e) {}
  if (lang !== "hr") lang = "de";
  let savedUsers = ["", "", ""];
  try {
    if (Keychain.contains(KC_USERS)) savedUsers = JSON.parse(Keychain.get(KC_USERS));
  } catch (e) {}

  while (true) {
    const T = I18N[lang];
    const year = new Date().getFullYear();

    const a = new Alert();
    a.title = T.title;
    a.message = T.subtitle;
    for (let i = 0; i < 3; i++) {
      a.addTextField(T.user + " " + (i + 1), savedUsers[i] || "");
      a.addSecureTextField(T.pass + " " + (i + 1), "");
    }
    a.addAction(T.runYear + " (" + year + ")");
    a.addAction(T.runOther);
    a.addAction(T.runRange);
    a.addAction(T.langBtn);
    a.addCancelAction(T.cancel);
    const idx = await a.present();
    if (idx === -1) return;

    const accounts = [];
    const users = [];
    for (let i = 0; i < 3; i++) {
      const u = (a.textFieldValue(i * 2) || "").trim();
      const p = a.textFieldValue(i * 2 + 1) || "";
      users.push(u);
      if (u && p) accounts.push({ u, p });
    }
    savedUsers = users;
    try { Keychain.set(KC_USERS, JSON.stringify(users)); } catch (e) {}

    if (idx === 3) {                       // Sprache umschalten
      lang = (lang === "de") ? "hr" : "de";
      try { Keychain.set(KC_LANG, lang); } catch (e) {}
      continue;
    }

    let fromDay, toDay;
    if (idx === 0) { fromDay = dayOf(year, 1, 1); toDay = dayOf(year, 12, 31); }
    else if (idx === 1) {
      const y = await askYear(T);
      if (y === null) continue;
      fromDay = dayOf(y, 1, 1); toDay = dayOf(y, 12, 31);
    } else {
      const r = await askRange(T);
      if (r === null) continue;
      if (r.bad === "period") { await msgAlert(T.vPeriod, T); continue; }
      if (r.bad === "order") { await msgAlert(T.vOrder, T); continue; }
      fromDay = r.fromDay; toDay = r.toDay;
    }

    if (!accounts.length) { await msgAlert(T.vAccount, T); continue; }

    const todayDay = Math.floor(Date.now() / DAY);
    const results = [];
    for (let i = 0; i < accounts.length; i++) {
      const acc = accounts[i];
      console.log(T.running.replace("{i}", i + 1).replace("{n}", accounts.length));
      try {
        await apiLogin(acc.u, acc.p, T);
        const recs = await fetchRecords(fromDay, toDay, T);
        results.push({ username: acc.u, ok: true,
                       stats: computeAccount(recs, fromDay, toDay, todayDay) });
      } catch (e) {
        results.push({ username: acc.u, ok: false, error: String(e.message || e) });
      } finally {
        await apiLogoutQuiet();
        acc.p = null;                      // Passwort sofort verwerfen
      }
    }

    const html = buildReport(results, fromDay, toDay, lang);
    const wv = new WebView();
    await wv.loadHTML(html);
    await wv.present(true);
    return;                                // nach Schließen des Reports beenden
  }
}

// --------------------------- Start / Test-Export ---------------------------
if (typeof Alert !== "undefined") {
  // Läuft in Scriptable
  main()
    .catch(async (e) => {
      const a = new Alert();
      a.message = String(e && (e.message || e));
      a.addCancelAction("OK");
      await a.present();
    })
    .then(() => { if (typeof Script !== "undefined") Script.complete(); });
} else if (typeof module !== "undefined") {
  // Node (nur für Tests der reinen Logik)
  module.exports = { assertReadOnly, parseNetDate, isoOfDay, dayOf,
                     monthsBetween, computeAccount, buildReport, I18N };
}
