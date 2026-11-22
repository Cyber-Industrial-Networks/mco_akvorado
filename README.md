# Akvorado – NetFlow trial on fleet-mon

NetFlow collector + web console for the MCO TAG IPsec headends (UE traffic). Repo `ts-ci-net/mco_akvorado`
(`git@github-mco-akvorado:ts-ci-net/mco_akvorado.git`; Mac key `~/.ssh/github_mco_akvorado_mac`, server key
`~/.ssh/github_mco_akvorado_deploy`). Runs in Docker Compose on **fleet-mon**
(`ssh fleet-mon`), next to fleet-monitor (`../fleet_monitoring`) and Graylog (`../graylog`). Started 2026-11-22.

- Console: **https://10.231.137.100:9444/** (fleet-monitor sign-in, TACACS+/ISE). Allowed from
  10.228.224.0/24, 10.228.20.28, 10.228.8.0/23 (ufw, same as Graylog :9443).
- NetFlow in: **udp/2055 on 10.231.137.100**, NetFlow v9 or IPFIX, unsampled. Allowed exporters: `ALLOWED` in
  `akvorado-docker-firewall.sh` (OC4-TAG01 10.228.224.17 primary, OC3-TAG01 10.228.224.18 standby).
- Install / update: `~/akvorado` on the server is a clone of this repo. Push from the Mac, then on the server
  `cd ~/akvorado && git pull` and `sudo bash ~/akvorado/install.sh` (idempotent), or install just the changed file
  (see the table) and restart that service. Akvorado version pinned in `install.sh` (`AKVORADO_VERSION`, currently 2026.10.1).

| File | Goes to |
|---|---|
| `install.sh` | run from `~/akvorado` |
| `env` | `/srv/akvorado/.env` (compose files used) |
| `docker-compose-local.yml` | `/srv/akvorado/docker/` – overrides on top of upstream `docker-compose.yml` |
| `config/*.yaml` | `/srv/akvorado/config/` (+ generated root-only `snmp-credentials.yaml` from cellwatch.env `CORE_SNMP_*`) |
| `akvorado-docker-firewall.sh` | `/usr/local/sbin/`, `akvorado-docker-firewall.service` |
| `nginx-akvorado.conf` | `/etc/nginx/sites-available/akvorado` |

- Data: 100G LV `ubuntu-vg/akvorado` at `/srv/akvorado`; ClickHouse in `/srv/akvorado/data/clickhouse`
  (mem_limit 6g). Retention: raw 7 d, 1 min 7 d, 5 min 90 d, 1 h 1 y (`config/akvorado.yaml`).
- **Firewall:** Docker-published ports bypass ufw, so udp/2055 is filtered by iptables chain `AKVORADO-FLOW` in
  `DOCKER-USER` (same approach as Graylog's syslog). A ufw rule for 2055 would have no effect.
- Localhost only: traefik public 127.0.0.1:8081 (console, behind nginx), private 127.0.0.1:8082 (ClickHouse,
  Kafka UI, orchestrator config – unauthenticated). nginx owns *:8080, hence 8082.
- Ops (needs sudo or docker group): `cd /srv/akvorado && sudo docker compose ps|logs -f akvorado-inlet`.
  Inlet stats: `curl -s 127.0.0.1:8082/api/v0/inlet/metrics | grep flows`.
- Break-glass if sign-in is down: `ssh -L 8081:127.0.0.1:8081 fleet-mon` → http://localhost:8081.

## Adding an exporter (e.g. OC4-CSW01, 10.228.224.1)

1. Add its address to `ALLOWED` in `akvorado-docker-firewall.sh`, copy to server, re-run `install.sh`
   (or just `sudo systemctl restart akvorado-docker-firewall` after installing the script).
2. SNMPv3 credentials already cover 10.228.224.0/24.
3. Apply the Flexible NetFlow config on the switch (exporter → 10.231.137.100 udp 2055, VRF mgmt).

## OC3-CSW01 baseline before NetFlow (2026-11-22 15:40 AEDT)

C9500-16X, IOS XE 17.12.05, Network + DNA Advantage. Part A (record/exporter/monitor) applied, not yet attached.

| Measure | Before | Watch after enabling |
|---|---|---|
| CPU (IOS 5 s / 1 min / 5 min) | 2% / 2% / 1%; 72 h peaks ~30–50% | 5 min avg up by no more than a few % |
| `fed main event` / `pubd` | 10% / 0% | pubd low single digits |
| DRAM (`show platform resources`) | 3777 MB / 15616 MB (24%), Healthy | stays Healthy |
| Netflow ACL TCAM in / out | 6/1024 / 6/2048 | small increase per attached interface |
| CPU queue drops (all incl. NFL SAMPLED DATA) | 0 | stay 0 |
| Tunnel0 / Tunnel1 5 min rate | 0 / 0 bps (Tu0 tracks Lo8, Tu1 tracks Lo88) | – |

`show platform software fed switch active fnf flow-record usage` doesn't exist on 17.12.

## OC3-CSW01 trial result (2026-11-22) – removed

Flexible NetFlow on Vl2034 (ingress `AKV-MON`, egress `AKV-MON-OUT`; Cat9k rejects `match interface input`
in an egress record, hence two records) worked end to end: SNMPv3, site `oc3`, interface names. But UE traffic
at the core switch is IPsec only (ESP + IKE/500) between UE cellular addresses 10.231.64.<n> and the VPN
headends 10.231.128.11 / .13, so no inner traffic is visible. All NetFlow config removed from OC3-CSW01
(never saved); `show run | section flow` back to the pre-trial state. Next: export from the headend
OC3-TAG01 (C8300), where traffic is decrypted.

## TAG headends (2026-11-22)

UE IPsec tunnels (IKEv2, DVTI Virtual-Template1, fvrf flt) terminate on two C8300 TAGs. **OC4-TAG01 is primary**
(BGP weighted toward it); OC3-TAG01 is standby: its ~95 tunnels stay up but carry only DPD keepalives.
UE data is decrypted on 10.231.128.11 (OC4-TAG01). OC3-TAG01: Lo1 10.228.224.18, Lo8 .12, Lo88 .13,
Po1.2032 = 10.231.128.109/31 to OC3-CSW01 Vl2032.

NetFlow on OC3-TAG01 `Port-channel1.2032` in+out (single record `AKV-REC` works both directions on IOS XE
routers) – works, SNMP `prtg` OK. Cost: QFP DRAM 36→59 MB, no CPU change. On Po1.2032 both the outer ESP and
the decrypted traffic appear; split them with SrcNetRole/DstNetRole `vpn-headend`.

## UE names (2026-11-22)

`config/outlet.yaml` network-source `fleet-ues` fetches every 10 min
`http://247.16.14.1:8090/ue-networks.json` → nginx :8090 (this repo's `nginx-akvorado.conf`; Docker subnet
247.16.14.0/24 only, ufw rule from install.sh) → fleet-monitor `GET /api/ues/akvorado-networks.json`
(commit 8df2d5c). Per UE: loopback /32, LAN /28, cellular /32, vendor cellular /32; `name` = UE034,
`tenant` = machine (register asset, else cellwatch OpGuard name). In the console: group by
`SrcNetName`/`DstNetName` (UE) or `SrcNetTenant`/`DstNetTenant` (machine).
Check: `curl -s 127.0.0.1:8082/api/v0/outlet/metrics | grep remotedatasource`.

## Fleet traffic dashboard (Grafana)

**https://10.231.137.100:9445/** – same fleet-monitor sign-in; everyone is a Grafana Viewer (can explore, can't
save). Service `grafana-flows` (grafana-oss 13.0.2 + grafana-clickhouse-datasource 4.23.4) on 127.0.0.1:3001,
reading ClickHouse as the read-only user `grafana` (`grafana/clickhouse-user.xml`, Docker network only).

- Dashboard "Fleet UE traffic": overview, top UEs/machines, destinations and services, internet/public DNS,
  tunnel health per UE and per TAG. Machine filter at the top. Raw flows only: 7-day history.
- **Edit the SQL in `grafana/build_dashboard.py`**, then `python3 grafana/build_dashboard.py` (writes
  `grafana/dashboards/fleet-traffic.json`) and commit both. Test every panel query on the server first:
  `python3 -I grafana/build_dashboard.py --test http://127.0.0.1:8082/clickhouse/`. Deploy: `git pull` +
  `sudo bash install.sh` (Grafana re-reads the dashboard within 30 s).
- Break-glass admin: `ssh -L 3001:127.0.0.1:3001 fleet-mon` → http://localhost:3001, user `admin`, password in
  `/srv/akvorado/grafana-admin-password.txt`.

## Fleet syslog dashboard (Grafana)

Same Grafana (https://10.231.137.100:9445/ → *Fleet syslog*, linked from *Fleet UE traffic*). Reads Graylog's
OpenSearch (`graylog_*`, no auth – security plugin is off in the Graylog stack) through the `graylog_graylog`
Docker network, plugin grafana-opensearch-datasource 2.34.6, data source "Graylog OpenSearch".

- Severity picker (default *error or worse* = severity ≤ 3). Overview counts, messages by severity over time,
  **UEs reporting per interval** (many at once = likely outage), UE × severity and infrastructure × severity
  tables (click a device → its messages in Graylog), top Cisco events, latest critical-or-worse messages.
- Generated by `grafana/build_syslog_dashboard.py` (fields from Graylog's Cisco IOS pipeline: `severity`,
  `severity_name`, `source` (lowercase hostname), `cisco_event`, `message`).
- Test both dashboards through Grafana after a deploy: `python3 -I ~/akvorado/grafana/test_dashboards.py`.

## Fleet events dashboard (Grafana, lower-priority syslog)

*Fleet events* (linked from the other two): severity 4-7 from Graylog's OpenSearch, by theme – **stability**
(interface / BGP / PIM / EIGRP changes, "Flaps by UE" grid), **machine power** (ignition under-voltage per machine;
machine names joined from ClickHouse), **changes and access** (config changes manual vs automated, logins, change
log), **hardware** (OIR), **hygiene** (native VLAN mismatch, leftover PnP / Catalyst Center sync, top events by
volume). SSH session chatter is hidden by default ("Session chatter" picker). Theme = list of Cisco event codes in
`THEMES` in `grafana/build_events_dashboard.py`.
