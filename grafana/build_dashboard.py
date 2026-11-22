#!/usr/bin/env python3
"""build_dashboard.py - generate grafana/dashboards/fleet-traffic.json.

The SQL for every panel lives here, readable; the JSON is generated. Edit this file,
run it, commit both:

    python3 grafana/build_dashboard.py            # writes dashboards/fleet-traffic.json
    python3 grafana/build_dashboard.py --test URL  # run every panel query against ClickHouse
                                                   # (e.g. http://127.0.0.1:8082/clickhouse/ on fleet-mon)

Data: Akvorado's raw `flows` table (7-day retention), exported by the TAG headends.
The TAGs export both directions on their core link, so each kind of traffic is picked
by direction + network role (roles come from config/outlet.yaml and the fleet-monitor
UE register):
  decrypted, UE -> elsewhere : FlowDirection = egress  AND SrcNetRole IN (ue-lan, ue-loopback)
  decrypted, elsewhere -> UE : FlowDirection = ingress AND DstNetRole IN (ue-lan, ue-loopback)
  encrypted tunnel (ESP/IKE) : FlowDirection = ingress AND SrcNetRole = ue-cellular AND DstNetRole = vpn-headend
"""
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dashboards", "fleet-traffic.json")
DS = {"type": "grafana-clickhouse-datasource", "uid": "akvorado-ch"}

# ------------------------------------------------------------------ SQL building blocks
UE_OUT = "(FlowDirection = 'egress' AND SrcNetRole IN ('ue-lan', 'ue-loopback'))"
UE_IN = "(FlowDirection = 'ingress' AND DstNetRole IN ('ue-lan', 'ue-loopback'))"
DECRYPTED = f"({UE_OUT} OR {UE_IN})"
ESP_IN = ("(FlowDirection = 'ingress' AND SrcNetRole = 'ue-cellular' "
          "AND DstNetRole = 'vpn-headend')")
TAGS = "ExporterName LIKE '%-TAG%'"
# UE name from the register, else the address (UE missing from the register, or old flows).
UE = ("if(FlowDirection = 'egress', " + "{src}, {dst})")  # filled in below, after NAME_OR_IP
MACHINE = "if(FlowDirection = 'egress', SrcNetTenant, DstNetTenant)"
# Machine name, or the UE when the register has no machine for it.
WHO = f"if({MACHINE} != '', {MACHINE}, {UE})"
MACHINE_VAR = f"('__all' IN (${{machine:singlequote}}) OR {MACHINE} IN (${{machine:singlequote}}))"
WHERE = f"$__timeFilter(TimeReceived) AND {TAGS}"


def ip(col):
    return f"replaceOne(IPv6NumToString({col}), '::ffff:', '')"


# Flows stored before the register feed went live carry the old static names (ue-lan, ...):
# treat those as unnamed too. They age out with the 7-day raw retention.
NO_NAME = "('', 'ue-lan', 'ue-loopback', 'ue-cellular')"


def NAME_OR_IP(side):
    return f"if({side}NetName NOT IN {NO_NAME}, {side}NetName, {ip(side + 'Addr')})"


UE = UE.format(src=NAME_OR_IP("Src"), dst=NAME_OR_IP("Dst"))
WHO = f"if({MACHINE} != '', {MACHINE}, {UE})"


PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
           "127.0.0.0/8", "169.254.0.0/16", "224.0.0.0/4"]
PUBLIC_DST = "(" + " AND ".join(f"NOT isIPAddressInRange({ip('DstAddr')}, '{p}')" for p in PRIVATE) + ")"
PROTO = "multiIf(Proto = 6, 'TCP', Proto = 17, 'UDP', Proto = 1, 'ICMP', Proto = 50, 'ESP', toString(Proto))"
BPS = "sum(Bytes) * 8 / $__interval_s"

# ------------------------------------------------------------------ panels
panels = []
_y = [0]


def add(panel, w, h, x=None):
    """Lay panels out left to right, wrapping at 24 columns."""
    if x is None:
        x = add.x
    if x + w > 24:
        x, _y[0] = 0, _y[0] + add.h
    panel["gridPos"] = {"x": x, "y": _y[0], "w": w, "h": h}
    add.x, add.h = x + w, max(add.h if x else 0, h)
    panel["id"] = len(panels) + 1
    panels.append(panel)


add.x, add.h = 0, 0


def row(title):
    _y[0] += add.h
    add.x, add.h = 0, 0
    add({"type": "row", "title": title, "collapsed": False, "panels": []}, 24, 1)
    _y[0] += 1
    add.x, add.h = 0, 0


def target(sql, fmt):
    return {"refId": "A", "datasource": DS, "editorType": "sql", "rawSql": sql.strip(),
            "format": 0 if fmt == "timeseries" else 1, "queryType": fmt}


def stat(title, sql, unit, desc=""):
    return {"type": "stat", "title": title, "description": desc, "datasource": DS,
            "targets": [target(sql, "table")],
            "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "thresholds"},
                                         "thresholds": {"mode": "absolute",
                                                        "steps": [{"color": "blue", "value": None}]}},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "value", "graphMode": "none", "textMode": "value"}}


def table(title, sql, units=None, desc="", sort=None):
    overrides = [{"matcher": {"id": "byName", "options": name},
                  "properties": [{"id": "unit", "value": unit}]} for name, unit in (units or {}).items()]
    return {"type": "table", "title": title, "description": desc, "datasource": DS,
            "targets": [target(sql, "table")],
            "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": overrides},
            "options": {"showHeader": True, "cellHeight": "sm",
                        "sortBy": [{"displayName": sort, "desc": True}] if sort else []}}


def series(title, sql, unit="bps", desc="", stack=True):
    return {"type": "timeseries", "title": title, "description": desc, "datasource": DS,
            "targets": [target(sql, "timeseries")],
            "fieldConfig": {"defaults": {"unit": unit, "custom": {
                "drawStyle": "line", "fillOpacity": 30 if stack else 0, "lineWidth": 1,
                "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                "showPoints": "never"}}, "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "right",
                                   "calcs": ["mean", "max"], "sortBy": "Mean", "sortDesc": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def bars(title, sql, unit="decbytes", desc=""):
    return {"type": "bargauge", "title": title, "description": desc, "datasource": DS,
            "targets": [target(sql, "table")],
            "fieldConfig": {"defaults": {"unit": unit, "min": 0, "color": {"mode": "continuous-BlPu"}},
                            "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True}}}


# --- Overview
row("Overview")
add(stat("UE traffic (decrypted)", f"""
SELECT sum(Bytes) AS bytes FROM flows
WHERE {WHERE} AND {DECRYPTED} AND {MACHINE_VAR}""", "decbytes",
         "Decrypted traffic to and from UE LANs and loopbacks, both directions."), 6, 4)
add(stat("Active UEs", f"""
SELECT uniqExact({UE}) AS ues FROM flows
WHERE {WHERE} AND {DECRYPTED} AND {MACHINE_VAR} AND {UE} != ''""", "none",
         "UEs with decrypted traffic in the time range."), 6, 4)
add(stat("Machines using the internet", f"""
SELECT uniqExact(SrcNetName) AS ues FROM flows
WHERE {WHERE} AND {UE_OUT} AND {PUBLIC_DST} AND {MACHINE_VAR}""", "none",
         "UEs whose LAN sent traffic to a public (non-private) address."), 6, 4)
add(stat("UEs carrying data via standby TAG", f"""
SELECT uniqExact({UE}) AS ues FROM flows
WHERE {WHERE} AND {DECRYPTED} AND ExporterSite = 'oc3' AND {MACHINE_VAR}""", "none",
         "OC4-TAG01 is primary; decrypted traffic through OC3-TAG01 means the UE has failed over."), 6, 4)

# --- Top UEs / machines
row("Top UEs and machines")
add(table("Top UEs", f"""
SELECT {UE} AS UE, any({MACHINE}) AS Machine,
       sumIf(Bytes, FlowDirection = 'egress') AS `From UE`,
       sumIf(Bytes, FlowDirection = 'ingress') AS `To UE`,
       sum(Bytes) AS Total, count() AS Flows,
       uniqExactIf({ip('DstAddr')}, FlowDirection = 'egress') AS Destinations
FROM flows
WHERE {WHERE} AND {DECRYPTED} AND {MACHINE_VAR} AND {UE} != ''
GROUP BY UE ORDER BY Total DESC LIMIT 100""",
          {"From UE": "decbytes", "To UE": "decbytes", "Total": "decbytes"},
          "Decrypted traffic per UE. Machine = register asset, or the name cellwatch found on the UE LAN.",
          "Total"), 10, 12)
add(series("Traffic by machine (top 10)", f"""
WITH top AS (
  SELECT {WHO} AS who FROM flows
  WHERE {WHERE} AND {DECRYPTED} AND {MACHINE_VAR}
  GROUP BY who ORDER BY sum(Bytes) DESC LIMIT 10)
SELECT $__timeInterval(TimeReceived) AS time, {WHO} AS machine, {BPS} AS bps
FROM flows
WHERE {WHERE} AND {DECRYPTED} AND {MACHINE_VAR} AND {WHO} IN (SELECT who FROM top)
GROUP BY time, machine ORDER BY time""",
           desc="Decrypted traffic, both directions, for the 10 busiest machines (UE name when the machine is unknown)."), 14, 12)

# --- Destinations & apps
row("Destinations and applications")
add(table("Where UEs send traffic", f"""
SELECT {ip('DstAddr')} AS Destination, any(DstNetName) AS Label, {PROTO} AS Protocol,
       DstPort AS Port, uniqExact(SrcNetName) AS UEs, sum(Bytes) AS Bytes, count() AS Flows
FROM flows
WHERE {WHERE} AND {UE_OUT} AND {MACHINE_VAR}
GROUP BY Destination, Protocol, Port ORDER BY Bytes DESC LIMIT 100""",
          {"Bytes": "decbytes"},
          "UE LAN / loopback -> destination, decrypted. UEs = how many UEs sent to it.", "Bytes"), 12, 11)
add(bars("Traffic by service", f"""
SELECT concat({PROTO}, if(DstPort > 0, concat('/', toString(DstPort)), '')) AS service,
       sum(Bytes) AS bytes
FROM flows
WHERE {WHERE} AND {UE_OUT} AND {MACHINE_VAR}
GROUP BY service ORDER BY bytes DESC LIMIT 12""",
         desc="UE -> destination traffic by protocol/destination port."), 6, 11)
add(table("Who sends traffic to UEs", f"""
SELECT {ip('SrcAddr')} AS Source, any(SrcNetName) AS Label, {PROTO} AS Protocol,
       SrcPort AS Port, uniqExact(DstNetName) AS UEs, sum(Bytes) AS Bytes
FROM flows
WHERE {WHERE} AND {UE_IN} AND {MACHINE_VAR}
GROUP BY Source, Protocol, Port ORDER BY Bytes DESC LIMIT 100""",
          {"Bytes": "decbytes"}, "Source -> UE LAN / loopback, decrypted (pollers, servers, ...).", "Bytes"), 6, 11)

# --- Internet & DNS
row("Internet and public DNS")
add(table("Machines reaching public addresses", f"""
SELECT {NAME_OR_IP('Src')} AS UE, any(SrcNetTenant) AS Machine, {ip('DstAddr')} AS Destination,
       {PROTO} AS Protocol, DstPort AS Port, sum(Bytes) AS Bytes, count() AS Flows,
       max(TimeReceived) AS `Last seen`
FROM flows
WHERE {WHERE} AND {UE_OUT} AND {PUBLIC_DST} AND {MACHINE_VAR}
GROUP BY UE, Destination, Protocol, Port ORDER BY Bytes DESC LIMIT 200""",
          {"Bytes": "decbytes", "Last seen": "dateTimeAsLocal"},
          "UE LAN -> any address outside 10/8, 172.16/12, 192.168/16, 100.64/10 and multicast.", "Bytes"), 14, 11)
add(series("Internet traffic by machine", f"""
SELECT $__timeInterval(TimeReceived) AS time,
       if(SrcNetTenant != '', SrcNetTenant, {NAME_OR_IP('Src')}) AS machine, {BPS} AS bps
FROM flows
WHERE {WHERE} AND {UE_OUT} AND {PUBLIC_DST} AND {MACHINE_VAR}
GROUP BY time, machine ORDER BY time""", desc="UE -> public address traffic per machine."), 10, 11)

# --- Tunnel health
row("Tunnel health (TAG headends)")
tag_cols = []
for site in ("oc4", "oc3"):
    tag_cols += [f"sumIf(Bytes, {ESP_IN} AND ExporterSite = '{site}') AS `Tunnel {site.upper()}`",
                 f"sumIf(Bytes, {DECRYPTED} AND ExporterSite = '{site}') AS `Data {site.upper()}`"]
add(table("Tunnels per UE", f"""
SELECT if({ESP_IN} OR FlowDirection = 'egress', {NAME_OR_IP('Src')}, {NAME_OR_IP('Dst')}) AS UE,
       any(if({ESP_IN} OR FlowDirection = 'egress', SrcNetTenant, DstNetTenant)) AS Machine,
       {', '.join(tag_cols)},
       multiIf(`Data OC3` > `Data OC4`, 'OC3 (standby)', `Data OC4` > 0, 'OC4',
               `Tunnel OC4` > `Tunnel OC3`, 'OC4 (no data)', 'OC3 (no data)') AS `Carried by`,
       maxIf(TimeReceived, {ESP_IN}) AS `Last tunnel packet`
FROM flows
WHERE {WHERE} AND ({ESP_IN} OR {DECRYPTED}) AND {MACHINE_VAR}
GROUP BY UE HAVING UE != ''
ORDER BY (`Carried by` != 'OC4') DESC, `Data OC4` + `Data OC3` ASC LIMIT 200""",
          {"Tunnel OC4": "decbytes", "Data OC4": "decbytes", "Tunnel OC3": "decbytes",
           "Data OC3": "decbytes", "Last tunnel packet": "dateTimeAsLocal"},
          "Tunnel = encrypted ESP/IKE from the UE's cellular address to each TAG (standby still sees "
          "keepalives). Data = decrypted traffic through that TAG. UEs not on OC4, or with no data, "
          "are listed first."), 14, 12)
add(series("Encrypted tunnel traffic by TAG", f"""
SELECT $__timeInterval(TimeReceived) AS time, upper(ExporterSite) AS tag, {BPS} AS bps
FROM flows
WHERE {WHERE} AND {ESP_IN} AND {MACHINE_VAR}
GROUP BY time, tag ORDER BY time""",
           desc="ESP/IKE from UEs to each TAG. A shift from OC4 to OC3 = UEs failing over.", stack=False), 10, 12)

dashboard = {
    "uid": "fleet-traffic",
    "title": "Fleet UE traffic",
    "tags": ["akvorado", "fleet"],
    "timezone": "browser",
    "editable": True,
    "refresh": "1m",
    "time": {"from": "now-6h", "to": "now"},
    "schemaVersion": 39,
    "description": "NetFlow from the TAG IPsec headends via Akvorado (raw flows, 7-day retention).",
    "templating": {"list": [{
        "name": "machine", "label": "Machine", "type": "query", "datasource": DS,
        "query": ("SELECT DISTINCT SrcNetTenant FROM flows WHERE TimeReceived > now() - INTERVAL 1 DAY "
                  "AND SrcNetRole IN ('ue-lan', 'ue-loopback') AND SrcNetTenant != '' ORDER BY 1"),
        "definition": "machines seen in the last day",
        "refresh": 2, "multi": True, "includeAll": True, "allValue": "__all",
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]}, "sort": 1}]},
    "panels": panels,
}


# ------------------------------------------------------------------ test mode
def expand(sql, start="now() - INTERVAL 1 HOUR", step=60):
    return (sql.replace("$__timeFilter(TimeReceived)", f"TimeReceived >= {start}")
               .replace("$__timeInterval(TimeReceived)", f"toStartOfInterval(TimeReceived, INTERVAL {step} second)")
               .replace("$__interval_s", str(step))
               .replace("${machine:singlequote}", "'__all'"))


def test(url):
    bad = 0
    for p in panels:
        for t in p.get("targets", []):
            q = expand(t["rawSql"]) + "\nFORMAT TSVWithNames"
            try:
                with urllib.request.urlopen(url + "?" + urllib.parse.urlencode({"query": q}), timeout=60) as r:
                    lines = r.read().decode().splitlines()
                print(f"OK   {p['title']:<38} {max(len(lines) - 1, 0):>4} rows  {lines[1][:90] if len(lines) > 1 else ''}")
            except urllib.error.HTTPError as e:
                bad += 1
                print(f"FAIL {p['title']}: {e.read().decode()[:400]}")
    var = dashboard["templating"]["list"][0]["query"] + " FORMAT TSV"
    with urllib.request.urlopen(url + "?" + urllib.parse.urlencode({"query": var}), timeout=60) as r:
        print(f"OK   variable machine: {len(r.read().decode().splitlines())} values")
    return bad


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        sys.exit(1 if test(sys.argv[2]) else 0)
    with open(OUT, "w") as f:
        json.dump(dashboard, f, indent=2)
        f.write("\n")
    print(f"wrote {OUT}: {len(panels)} panels")
