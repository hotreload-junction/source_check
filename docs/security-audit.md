# Code-Audit: Bugs und Sicherheitslücken (16.11.2026)

Branch: `claude/code-audit-security-review-tgkp9b`. Geprüft wurden das gesamte Backend (`backend/app`, ~9 000 Zeilen), das
Frontend (`frontend/src`), Deploy-Konfiguration (`deploy/`), GitHub-Workflows (`.github/workflows`), das Agent-Tooling
(`tools/`, `.claude/hooks`) und die Git-Historie auf eingecheckte Secrets.

**Testlage:** vorher 487 Backend- + 48 Frontend-Tests grün. Nachher 536 Backend- (+49 neue in
`backend/tests/test_security_fixes.py`) und 50 Frontend-Tests grün, `tools/check` grün. Jeder Fix unten hat einen
Regressionstest, der vor dem Fix fehlgeschlagen wäre.

## Übersicht

| # | Schwere | Befund | Status |
|---|---|---|---|
| 1 | **Hoch** | SSRF: Backend lädt beliebige interne Adressen (Cloud-Metadaten, `db:3306`, `localhost`) und zeigt den Inhalt an | behoben |
| 2 | **Hoch** | Prompt-Injection ins Agent-Team: jeder GitHub-Nutzer kann über das Board headless Agents steuern | behoben |
| 3 | **Hoch** (Integrität) | „Quote first“ umgehbar: erfundenes Zitat, das eine kurze Quelle umschließt, bekommt Fuzzy-Score 100 | behoben |
| 4 | Mittel | Speicher-DoS: direkte Downloads ohne Größenlimit | behoben |
| 5 | Mittel | Cache-Key > 512 Zeichen: Fehler „Datenbank nicht erreichbar“ und Cache-Kollision zwischen langen URLs | behoben |
| 6 | Mittel | Verwaiste „running“-Zeilen blockieren das Concurrency-Limit dauerhaft (alle bekommen 429) | behoben |
| 7 | Mittel | Fehlende Browser-Security-Header (CSP, Clickjacking) und kein Body-Limit an der API | behoben |
| 8 | Niedrig | `href` aus fremden Daten ohne Scheme-Prüfung (`javascript:`-Links) | behoben |
| 9 | Niedrig | Rohe Fehlertexte des LLM-Providers werden öffentlich angezeigt | behoben |
| 10 | Niedrig | Diverse Robustheits-Bugs (Absturz einer Claim-Prüfung beendet die ganze Analyse, `Retry-After`-Datum, `null`-Tokens, IPv6, Socket-Leak …) | behoben |
| 11–18 | Info | Empfehlungen ohne Code-Änderung (Rate-Limit, Einmal-Workflows, DB-Default-Passwort …) | offen, siehe unten |

Keine eingecheckten Secrets gefunden (gesamte Historie aller Branches durchsucht, `.env` nie committet).

---

## 1. SSRF über die Analyse-URL und über Links im Artikel (Hoch)

**Problem.** `POST /api/analyses` akzeptierte jede `http(s)`-URL. Der Fetcher lädt sie (Context.dev, bei Fehlschlag direkter
GET mit `follow_redirects=True`), und der Text landet in `result.article.text_markdown`, das jeder Besucher abrufen kann.
Damit ließen sich z. B. `http://169.254.169.254/latest/meta-data/` (Cloud-Metadaten des VPS), `http://backend:8000/…`,
`http://db:3306` oder Dienste im Docker-/Host-Netz auslesen. Zusätzlich: Jede Quelle, auf die ein (vom Angreifer
geschriebener) Artikel verlinkt, wird ebenfalls direkt geladen, ebenso Exa- und Agent-Treffer. Ein Redirect von einer
öffentlichen Seite auf eine interne Adresse wurde ebenfalls verfolgt.

Reproduziert: `normalize_url("http://169.254.169.254/latest/meta-data/")` wurde angenommen, der Job gestartet.

**Fix.**
- Neues Modul `backend/app/fetch/netguard.py`:
  - `is_public_hostname()` (ohne DNS): lehnt nicht-globale IP-Literale (privat, Loopback, Link-Local, IPv4-mapped IPv6,
    Kurzformen wie `0x7f000001`/`127.1`), `localhost`, Ein-Wort-Hosts (`db`, `backend`) und interne Suffixe (`.local`,
    `.internal` …) ab.
  - `guard_request` als httpx-Request-Hook am Default-Client des Fetchers: läuft vor **jedem** Request, auch bei jedem
    Redirect-Hop, löst den Namen auf und blockiert, wenn irgendeine Adresse nicht öffentlich ist; Ports nur 80/443/8080/8443.
  - Die Ausnahme `BlockedAddress` erbt von `httpx.HTTPError`, wird also überall zum neutralen Status `unreachable`
    (zählt nie gegen einen Artikel, AGENTS.md §7).
- `api.py`: interne Hosts → `422 invalid_url` („Only public web addresses can be analysed.“), bevor ein Job startet.
- `contracts/api.md`: die neuen 422-Fälle dokumentiert.

**Restrisiko.** DNS-Rebinding zwischen unserem Lookup und dem Connect von httpx. Die Port-Whitelist hält interne Dienste auf
anderen Ports trotzdem fern. Eine vollständige Lösung wäre ein Transport, der auf die geprüfte IP pinnt, oder ein
Egress-Proxy/Firewall-Regeln auf dem VPS (Container darf `169.254.169.254` und RFC1918 nicht erreichen) – empfohlen als
zweite Ebene.

## 2. Prompt-Injection ins Agent-Team über das Board (Hoch)

**Problem.** `tools/board` liest die Kommentare des Board-Issues. Der Header `[leo] BREAKING -> all:` ist reiner Text und
nicht an den GitHub-Login gebunden; Kommentare ohne Header werden als „NOTE an alle“ behandelt. AGENTS.md sagt selbst:
„The repo and the board may be public“. Damit konnte **jeder GitHub-Nutzer** einen Kommentar schreiben, der via
`tools/autopilot` als Prompt an einen headless Agent geht – laut Doku gestartet mit
`claude -p --dangerously-skip-permissions` bzw. `codex exec --full-auto`. Das ist faktisch Remote-Code-Ausführung auf den
Rechnern des Teams (und Zugriff auf deren `.env`/Keys, Push nach `dev`).

**Fix.** `tools/board` filtert jetzt per `jq` auf `author_association` ∈ {OWNER, MEMBER, COLLABORATOR} plus explizit
erlaubte Logins (`BOARD_ALLOW_LOGINS`, Default `github-actions[bot]` für die CI-Posts). `BOARD_TRUST_ALL=1` schaltet den
Filter ab (nur für private Repos). Getestet mit Beispiel-JSON: ein gefälschter `[leo] BREAKING`-Kommentar eines Fremden wird
verworfen, Owner/Collaborator/CI kommen durch. Kompatibel mit bash 3.2 (`set -u`, keine Glob-Expansion von `[bot]`).

**Empfehlung zusätzlich:** Autopilot-Agents nicht mit `--dangerously-skip-permissions` betreiben, oder nur in einer
Sandbox ohne Secrets.

## 3. „Quote first“-Invariante umgehbar (Hoch, Integrität der Detection)

**Problem.** `verify.fuzzy_score` nutzte `rapidfuzz.fuzz.partial_ratio(quote, source)`. `partial_ratio` sucht den
**kürzeren** String im längeren. Ist die Quelle kurz (z. B. ein Telegram-Post, für den es kein Mindestlängen-Check gibt)
und das vom LLM gelieferte „Zitat“ länger, wird die Quelle im Zitat gesucht – ein Zitat, das die ganze Quelle enthält plus
erfundene Sätze, bekommt **100 Punkte** und damit `supported`/`contradicted`. Reproduziert:

```
fuzzy_score("<Quelle> The attack was ordered by the president personally …", "<Quelle>")  ->  100.0
```

Das widerspricht direkt der zentralen Regel „Never trust an LLM quote unchecked“, nach der Hostinger die Logik bewertet.

**Fix.** Ist das Zitat länger als der Quelltext, wird `fuzz.ratio` (Gesamtvergleich) statt `partial_ratio` verwendet. Für
normale Fälle (Zitat kürzer als Quelle) ändert sich nichts. Betrifft auch `origin.py`, das dieselbe Funktion nutzt.

**Hinweis fürs Team:** Das ist eine Änderung an der Detection-Logik (nicht an Modell/Prompt). Laut AGENTS.md §7 sollte der
Eval-Lauf wiederholt und das auf dem Board erwähnt werden; erwartet wird kein messbarer Unterschied, da nur der seltene Fall
„Zitat länger als Quelle“ betroffen ist.

## 4. Speicher-DoS durch unbegrenzte Downloads (Mittel)

**Problem.** `_trafilatura` und `_raw_html` riefen `client.get(url)` und `r.text` auf: der ganze Body wird in den Speicher
geladen, der Content-Type wurde erst danach (und in `_raw_html` gar nicht) geprüft. Ein Link auf eine mehrere GB große
Datei oder einen endlosen Stream konnte den Backend-Container sprengen.

**Fix.** Neuer `Fetcher._get_limited()`: streamt, prüft Status, Content-Type und `Content-Length` vorab und bricht nach
`MAX_DOWNLOAD_BYTES` (5 MB) ab → neutraler Status `blocked` („page larger than …“).

## 5. Cache-Key-Länge und Kollisionen (Mittel)

**Problem.** `analyses.normalized_url` ist `VARCHAR(512)`, die URL-Länge war nicht begrenzt. Auf MariaDB (strict mode)
schlägt der Insert fehl → `SQLAlchemyError` → der Nutzer sieht fälschlich „database_unavailable“ (503). Außerdem wurde mit
Frage auf `norm[:490]` gekürzt: zwei verschiedene lange URLs mit gleichem Präfix teilten sich das gecachte Ergebnis.

**Fix.** URL > 2048 Zeichen → 422. Zu lange normalisierte URLs werden zu Präfix + SHA-256 der ganzen URL (≤ 512 Zeichen,
kollisionsfrei, deterministisch).

## 6. Verwaiste „running“-Jobs blockieren den Dienst (Mittel)

**Problem.** Scheitert am Ende eines Jobs sowohl das Speichern des Ergebnisses als auch das Speichern des Fehlers (kurzer
DB-Ausfall), bleibt die Zeile `running`. `running_count()` zählt sie für immer gegen `MAX_CONCURRENT_ANALYSES` (Default 2) –
nach zwei solchen Fällen bekommt **jede** neue Anfrage 429, bis zum Neustart. `find_active()` ließ neue Anfragen außerdem
einem toten Job „beitreten“ (Client wartet ewig).

**Fix.** `jobs.py`: aktive Zeilen zählen nur, wenn sie jünger als 30 Minuten sind (`ACTIVE_MAX_AGE`; jede Analyse wird durch
ihre eigenen Budgets nach wenigen Minuten beendet).

## 7. Browser-Hardening und Body-Limit (Mittel)

**Problem.** Caddy lieferte keine Security-Header. Die UI zeigt Text aus beliebigen Webseiten an; ohne CSP gibt es keine
zweite Verteidigungslinie hinter React-Escaping, kein Schutz gegen Clickjacking. Request-Bodies an `/api/*` waren unbegrenzt.

**Fix.** `deploy/Caddyfile`: `Content-Security-Policy` (Skripte nur `'self'`, `frame-ancestors 'none'`, `object-src 'none'`,
Google Fonts und Favicon-Hosts erlaubt), `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy`, `Server`-Header entfernt; `request_body max_size 64KB` für `/api/*`.

Getestet: Frontend gebaut und mit genau dieser CSP in Chromium geladen (Landing, Demo-Analyse, Methodology) – keine
CSP-Verstöße. Der erste Testlauf hat gezeigt, dass die Favicons über `google.com/s2/favicons` → `*.gstatic.com` laufen; die
sind jetzt erlaubt. **Nicht** geprüft: die Caddyfile-Syntax mit `caddy validate` (kein Caddy-Binary in der Sandbox) – bitte
vor dem Merge einmal `docker compose … up web` bzw. `caddy validate` laufen lassen.

## 8. `javascript:`-Links im Frontend (Niedrig)

**Problem.** `Panel.tsx` setzte `href={s.url}` direkt. Die Artikel-Links werden im Backend zwar auf `http(s)` gefiltert,
aber Quell-URLs kommen auch aus Exa und Agent-Antworten. Eine `javascript:`-URL würde beim Klick im Origin der App laufen.

**Fix.** `safeHref()` in `frontend/src/text.ts` (nur `http:`/`https:`), an allen drei Link-Stellen verwendet; Tests in
`text.test.ts`.

## 9. Provider-Fehlertexte öffentlich (Niedrig)

**Problem.** `AnalysisError(f"Claim extraction failed: {exc}")` übernahm den rohen `LLMError`, inkl. HTTP-Body des Providers
(z. B. „Incorrect API key provided: sk-…***…“). Der Fehler steht im öffentlichen `GET /api/analyses/{id}`.

**Fix.** Detail ins Log, nach außen eine generische Meldung.

## 10. Robustheits-Bugs (Niedrig)

| Datei | Bug | Fix |
|---|---|---|
| `pipeline/assess.py` | Eine unerwartete Exception in **einer** Claim-Prüfung (z. B. ungewohnte API-Antwort) beendete die **ganze** Analyse | diese Claim wird `not_checkable` (neutral), der Rest läuft weiter |
| `pipeline/run.py` | `next(...)` ohne Default → `StopIteration`, wenn die Evidenz-Quelle nicht registriert ist | `next(..., None)` |
| `pipeline/exa.py` | `float(Retry-After)` wirft bei HTTP-Datum `ValueError` aus einer „never raises“-Funktion | `_retry_after()` mit Fallback |
| `pipeline/exa.py` | Nicht-Dict-Einträge in `results` → `AttributeError` | gefiltert |
| `pipeline/exa_agent.py` | pro Claim ein `httpx.Client`, nie geschlossen (Socket-Leak); unerwartete JSON-Formen → Absturz trotz „never raises“ | Client wird geschlossen, Antwort defensiv geparst, Fehler → neutral |
| `llm.py` | `"usage": {"prompt_tokens": null}` → `TypeError` im Kostenzähler | `or 0` |
| `fetch/urls.py` | IPv6: `http://[::1]/` wurde zu `http://::1` (keine gültige URL); ungültiger Port warf `ValueError` statt `InvalidURL` (→ 500) | Klammern erhalten, `InvalidURL` |
| `pipeline/textutil.py` | `looks_like_copy` + Konstanten doppelt definiert | Duplikat entfernt |

---

## Empfehlungen ohne Code-Änderung (Info)

11. **Kein Rate-Limit pro Client.** Jeder kann ohne Login Analysen starten, auch mit `force: true` (Cache wird umgangen,
    alle bezahlten APIs laufen neu). Das globale Limit von 2 parallelen Jobs begrenzt die Kosten pro Zeit, erlaubt aber,
    den Dienst für alle anderen dauerhaft auf 429 zu halten. Empfehlung: Rate-Limit pro IP in Traefik/Caddy, `force` nur
    mit Token.
12. **SSE-Streams belegen Threads.** Jede offene `/events`-Verbindung hält einen Thread des AnyIO-Pools (Default 40). Viele
    offene Streams können die synchronen Endpoints blockieren. Empfehlung: Limit pro IP am Proxy oder async-Generator.
13. **Einmal-Workflows im Repo.** `sync-verda-key.yml` und `export-guardian-cache.yml` sind als „one-time“ gedacht, lassen
    sich aber per `workflow_dispatch` oder passender Commit-Message erneut auslösen. `export-guardian-cache` lädt ein
    Ergebnis aus der Produktions-DB als Artifact hoch, das bei einem öffentlichen Repo jeder herunterladen kann.
    Empfehlung: nach Gebrauch entfernen (nicht von mir gelöscht – das ist eine Entscheidung für die Humans).
14. **Actions nicht auf Commit-SHA gepinnt** (`actions/checkout@v4` usw.). Supply-Chain-Härtung: auf SHA pinnen.
15. **DB-Default-Passwort.** `docker-compose.yml` fällt ohne `MARIADB_PASSWORD` auf `citetrace` zurück. Auf dem VPS setzt
    `bootstrap.sh` ein Zufallspasswort und der Port ist nicht veröffentlicht – lokal okay, aber `:?` wäre sicherer.
16. **httpx-Clients pro Analyse** (`Fetcher`, `RdapClient`, `WaybackClient`, `LLMClient`) werden nicht explizit
    geschlossen; der GC räumt auf, bei Dauerlast lieber ein `close()` am Ende von `analyze()`.
17. **Datenschutz.** Favicons (`google.com/s2/favicons`) und Google Fonts verraten Google, welche Domains ein Nutzer
    gerade prüft. Für eine Desinformations-Analyse-App ggf. selbst hosten.
18. **Proxy-Betrieb.** Der SSRF-Guard löst Namen lokal auf. Läuft das Backend künftig hinter einem Egress-Proxy ohne
    lokales DNS, würden alle direkten Fetches als `unreachable` enden (Context.dev ist davon nicht betroffen).

## Geänderte Dateien

- Backend: `app/fetch/netguard.py` (neu), `app/fetch/fetcher.py`, `app/fetch/urls.py`, `app/api.py`, `app/jobs.py`,
  `app/llm.py`, `app/pipeline/{assess,exa,exa_agent,run,textutil,verify}.py`, `tests/test_security_fixes.py` (neu)
- Frontend: `src/text.ts`, `src/text.test.ts`, `src/Panel.tsx`
- Deploy/Tooling/Doku: `deploy/Caddyfile`, `tools/board`, `contracts/api.md`, `docs/security-audit.md` (dieser Bericht)
