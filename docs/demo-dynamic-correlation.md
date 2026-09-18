# Demo runbook — Dynamic Correlation (2 → 3 sources, live)

**Audience:** a customer who wants to see how to correlate **two** data sources and,
especially, how to **add a third source live**. The story is one two-tier service — the
**Directory App** (web app container + PostgreSQL container) running on the **Proxmox
hypervisor** — and how Splunk lines the tiers up on time and surfaces each tier's error
messages.

> Maps the customer's asks: *"correliere web-app container & hypervisor"* → Sources A + B;
> *"zeig Fehlermeldungen der unterliegenden Bausteine"* → the per-tier error panels;
> *"zeig mir den Service live wie ich ihn in die Correlation [bringe]"* → adding Source C
> live (Postgres is just one example — the 3rd source is a dropdown, so you can add
> Network or SNMP the same way).

The lab web tier is a small Python HTTP app; present `webapp:access` simply as **the web
server access log** (it captures every request: status, path, `src_ip`, errors). The
technique is identical for Apache/nginx access logs.

---

## The data sources

| Role | Source | Search | "Error" looks like |
|---|---|---|---|
| A — Web app | `berlin_web` / `webapp:access` | `index=berlin_web sourcetype=webapp:access` | `status>=500` (incl. `/healthz` 503 when the DB is down) |
| B — Hypervisor | `berlin_proxmox` / `proxmox:api` | `index=berlin_proxmox sourcetype=proxmox:api` | `status="error"`, container not `running` |
| C — Database *(add live)* | `berlin_db` / `postgres:log` | `index=berlin_db sourcetype=postgres:log` | `ERROR` / `FATAL` |
| C — Network *(alt)* | `berlin_network` | `index=berlin_network` | `timed out` / `refused` / `%LINK`… |
| C — SNMP *(alt)* | `berlin_snmp` | `index=berlin_snmp` | `linkDown` / trap |

---

## Pre-flight (do this before the customer joins)

1. **Both containers up and the app reachable** (on the Proxmox host, or ubuntu-berlin with sudo):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/demo-chaos.sh | bash -s -- recover
   curl http://198.18.3.50:8080/        # generate a couple of web requests
   ```
2. **Confirm each source has recent data** (Search, last 60 min):
   ```spl
   index=berlin_web sourcetype=webapp:access | stats count
   index=berlin_proxmox sourcetype=proxmox:api | stats count
   index=berlin_db sourcetype=postgres:log | stats count
   ```
   All three should be > 0. If `berlin_web` is empty, hit the app a few times; if
   `berlin_db` is empty, confirm the DB container + its UF are up (Startup step 5b).
3. Log in as **gary** (`role_global`) — the correlation spans Berlin tiers, and only the
   global analyst sees the whole chain.

---

## Run it — three ways (pick per audience)

### Way 1 — From scratch in Search (shows the *technique*)

Best for a technical audience: prove there's no magic — a source is one `OR (index=…)`.

**Two sources:**
```spl
(index=berlin_web sourcetype=webapp:access) OR (index=berlin_proxmox sourcetype=proxmox:api)
| eval tier=case(index=="berlin_web","Web app", index=="berlin_proxmox","Hypervisor", 1==1,"other")
| timechart span=1m count by tier
```

**Now add the 3rd source live** — literally type the extra clause and the `case()` line,
then re-run:
```spl
(index=berlin_web sourcetype=webapp:access) OR (index=berlin_proxmox sourcetype=proxmox:api)
  OR (index=berlin_db sourcetype=postgres:log)
| eval tier=case(index=="berlin_web","Web app", index=="berlin_proxmox","Hypervisor",
                 index=="berlin_db","Database", 1==1,"other")
| timechart span=1m count by tier
```
> Talking point: *"Adding a data source to a correlation is adding one line. That's the
> whole idea — Splunk doesn't care that these are three different systems."*

### Way 2 — The generic Correlation dashboards

**Correlation → Correlation 2 Sources**, then **Correlation 3 Sources**: pick any indexes
in the Source A/B/C dropdowns and a correlation key (service / user / host). Good for
showing the reusable *2-of-3 vs 3-of-3 footprint* pattern across arbitrary sources.

### Way 3 — The Dynamic Correlation dashboard (the main event)

**Correlation → Dynamic Correlation.** It opens on **2 sources** (Web app + Hypervisor):
an activity timeline, an error timeline, a footprint table, and each tier's error messages.

1. **"Add a 3rd source (live)"** dropdown → choose **Database — PostgreSQL**. The DB series
   appears in both timelines, a **Database error-messages** panel appears, and the footprint
   table gains a Database row — *without touching the search bar.*
   > *"So bringe ich einen Service live in die Correlation — ein Klick, kein Rebuild."*
2. **Break the database** (Proxmox host as root, or ubuntu-berlin with sudo):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/demo-chaos.sh | bash -s -- break-db
   ```
   Within ~1–2 min, on the same dashboard:
   - **Error timeline**: Web `5xx` and **DB error** bars rise **in the same minute** — the
     web app can't reach Postgres, so `/healthz` returns 503 and requests 5xx.
   - **Error-message panels**: the Web panel shows `db_unavailable…` messages; the Database
     panel shows the Postgres `FATAL`/connection errors — *the "Fehlermeldungen der
     unterliegenden Bausteine".*
   - **Footprint table**: errors light up red across the correlated tiers.
3. **Root cause:** open **Correlation → Root Cause Analysis** — it names the deepest broken
   layer (*"DB container (db-berlin) stopped"*). Correlation shows *what* moves together;
   RCA says *which layer* is to blame.
4. **Recover** and show it settle green:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/ubuntu/demo-chaos.sh | bash -s -- recover
   ```

> Swap the 3rd source to **Network** or **SNMP** in the same dropdown to show the pattern is
> source-agnostic — exactly the "add any source" message.

---

## German talk-track (optional)

- **Einstieg:** *"Wir haben einen Service aus zwei Bausteinen — Web-App und Datenbank — auf
  einem Hypervisor. Die Logs liegen getrennt. Correlation legt sie auf der Zeitachse
  übereinander."*
- **2 Quellen:** *"Erst zwei Quellen: die Web-App-Zugriffe und der Hypervisor."*
- **3. Quelle live:** *"Jetzt nehme ich die Datenbank dazu — ein Dropdown, sofort ist der
  Postgres-Service in der Correlation, inklusive seiner Fehlermeldungen."*
- **Incident:** *"Ich stoppe die DB. Sehen Sie — Web-5xx und DB-Fehler steigen zur selben
  Minute. Die Correlation zeigt den Zusammenhang, Root Cause Analysis zeigt die Ursache."*
- **Abschluss:** *"Eine weitere Quelle hinzuzufügen ist genau eine Zeile — das Prinzip
  bleibt gleich, egal ob Web, DB, Netzwerk oder SNMP."*

---

## Troubleshooting

- **A timeline/panel is empty:** that source has no data in the window — check the pre-flight
  counts; widen the time range; hit the web app a few times for `webapp:access`.
- **DB errors don't appear on `break-db`:** stopping the container makes the DB *unreachable*
  (Web 5xx + the DB series simply stops). To show Postgres `ERROR`/`FATAL` *log lines*
  instead, keep the container up and trigger a bad query/auth — or narrate the gap ("the DB
  stopped emitting, and the web tier went 5xx at the same moment").
- **Only some tiers show:** the 3rd source only appears after you pick it in the dropdown;
  the DB error panel is hidden until then (by design).
- **Nothing correlates in time:** confirm clocks/live timestamps — all lab feeds use live
  time, so a stale range hides the incident.
