#!/usr/bin/env python3
"""build_events_dashboard.py - generate grafana/dashboards/fleet-events.json.

Lower-priority syslog (severity 4-7: warning, notice, info, debug) from Graylog's OpenSearch,
organised by theme rather than by severity. A theme is a list of Cisco event codes
(FAC-SEV-MNEMONIC, Graylog field cisco_event) in THEMES below: edit the lists to re-file events.

    python3 grafana/build_events_dashboard.py     # writes dashboards/fleet-events.json
Test: python3 -I grafana/test_dashboards.py grafana/dashboards/fleet-events.json (on fleet-mon).
"""
import json
import os
import urllib.parse

from build_syslog_dashboard import COUNT, DS, GRAYLOG, HIST, UE_MAPPING, UES, os_target, stat, steps, terms

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards", "fleet-events.json")
CH = {"type": "grafana-clickhouse-datasource", "uid": "akvorado-ch"}
MIXED = {"type": "datasource", "uid": "-- Mixed --"}
LOW = "severity:>=4"


def events(*codes):
    """Lucene clause matching any of these Cisco event codes (wildcards allowed, e.g. PNP-6-*)."""
    return "cisco_event:(" + " OR ".join(c if "*" in c else f'"{c}"' for c in codes) + ")"


THEMES = {
    "Stability": ["LINEPROTO-5-UPDOWN", "LINK-5-UPDOWN", "LINK-5-CHANGED", "BGP-5-ADJCHANGE",
                  "BGP_SESSION-5-ADJCHANGE", "PIM-5-NBRCHG", "PIM-5-DRCHG", "DUAL-5-NBRCHANGE"],
    "Machine power": ["IGNITION-4-*"],
    "Changes and access": ["SYS-5-CONFIG_I", "SYS-5-CONFIG_P", "SEC_LOGIN-5-LOGIN_SUCCESS",
                           "SEC_LOGIN-4-LOGIN_FAILED", "DMI-5-AUTH_PASSED", "SYS-6-PRIVCFG_ENCRYPT_SUCCESS"],
    "Hardware": ["SPA_OIR-*", "OIR-*", "IOSXE_OIR-*"],
    "Hygiene": ["CDP-4-NATIVE_VLAN_MISMATCH", "PNP-6-*", "DMI-5-SYNC_*", "NDBMAN-5-*", "PKI-6-*"],
}
# Session chatter from automation (Catalyst Center, PRTG, scripts): hidden by default.
NOISE = ["SSH-5-SSH_CLOSE", "SSH-5-SSH2_SESSION", "SSH-5-SSH2_CLOSE", "SSH-5-SSH2_USERAUTH", "SYS-6-LOGOUT"]
THEME_COLOURS = {"Stability": "orange", "Machine power": "red", "Changes and access": "blue",
                 "Hardware": "purple", "Hygiene": "green", "Other": "text"}

panels = []
_y = [0]


def place(panel, x, w, h):
    panel["gridPos"] = {"x": x, "y": _y[0], "w": w, "h": h}
    panel["id"] = len(panels) + 1
    panels.append(panel)


def row_of(items, h):
    x = 0
    for p, w in items:
        place(p, x, w, h)
        x += w
    _y[0] += h


def heading(title):
    place({"type": "row", "title": title, "collapsed": False, "panels": []}, 0, 24, 1)
    _y[0] += 1


def graylog_link(lucene):
    q = urllib.parse.quote(lucene, safe="")
    return {"title": "Open in Graylog", "targetBlank": True,
            "url": (GRAYLOG + "/search?q=source%3A${__value.raw}%20AND%20" + q +
                    "&rangetype=absolute&from=${__from:date:iso}&to=${__to:date:iso}")}


def bars(title, targets, desc, overrides=(), stack=True, interval="15m"):
    return {"type": "timeseries", "title": title, "description": desc, "datasource": DS, "interval": interval,
            "targets": targets,
            "fieldConfig": {"defaults": {"unit": "short", "custom": {
                "drawStyle": "bars", "fillOpacity": 85, "gradientMode": "hue", "lineWidth": 0,
                "stacking": {"mode": "normal" if stack else "none", "group": "A"}, "showPoints": "never",
                "axisSoftMin": 0}}, "overrides": list(overrides)},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "calcs": ["sum"]},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def counted(alias, query, ref):
    t = os_target(query, COUNT, [HIST], ref=ref)
    t["alias"] = alias
    return t


def device_matrix(title, query, column_field, desc, link_query, name_col="Device"):
    """Device x <column_field> counts, pivoted; device names shown as UE034 for UEs."""
    return {"type": "table", "title": title, "description": desc, "datasource": DS,
            "targets": [os_target(query, COUNT, [terms("source", 200), terms(column_field, 12, "3")])],
            "transformations": [
                {"id": "groupingToMatrix", "options": {"columnField": column_field, "rowField": "source",
                                                       "valueField": "Count", "emptyValue": "zero"}},
                {"id": "calculateField", "options": {"mode": "reduceRow", "reduce": {"reducer": "sum"},
                                                     "alias": "Total", "replaceFields": False}},
                {"id": "sortBy", "options": {"sort": [{"field": "Total", "desc": True}]}},
            ],
            "fieldConfig": {"defaults": {
                "mappings": [UE_MAPPING, {"type": "value", "options": {"0": {"text": "·"}}}],
                "custom": {"filterable": True, "align": "center"},
                "color": {"mode": "thresholds"}, "thresholds": steps(("transparent", None), ("rgba(255,152,48,0.35)", 1))},
                "overrides": [
                    {"matcher": {"id": "byType", "options": "string"}, "properties": [
                        {"id": "displayName", "value": name_col}, {"id": "links", "value": [graylog_link(link_query)]},
                        {"id": "custom.width", "value": 150}, {"id": "custom.align", "value": "left"}]},
                    {"matcher": {"id": "byType", "options": "number"}, "properties": [
                        {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}]},
                    {"matcher": {"id": "byName", "options": "Total"}, "properties": [
                        {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
                        {"id": "color", "value": {"mode": "continuous-YlRd"}}, {"id": "min", "value": 0}]},
                ]},
            "options": {"showHeader": True, "cellHeight": "sm"}}


def raw_table(title, query, desc, size=300, event_names=None):
    """Latest messages: Time, Device, Event, Message."""
    ev_props = [{"id": "custom.width", "value": 250}]
    if event_names:
        ev_props += [{"id": "mappings", "value": [{"type": "value", "options": {
            k: {"text": t, "color": c, "index": i} for i, (k, (t, c)) in enumerate(event_names.items())}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}}]
    return {"type": "table", "title": title, "description": desc, "datasource": DS,
            "targets": [os_target(query, [{"id": "1", "type": "raw_data", "settings": {"size": str(size)}}], [],
                                  "RawData")],
            "transformations": [
                {"id": "filterFieldsByName", "options": {"include": {"names": [
                    "timestamp", "source", "cisco_event", "message"]}}},
                {"id": "organize", "options": {
                    "indexByName": {"timestamp": 0, "source": 1, "cisco_event": 2, "message": 3},
                    "renameByName": {"timestamp": "Time", "source": "Device", "cisco_event": "Event",
                                     "message": "Message"}}},
                {"id": "sortBy", "options": {"sort": [{"field": "Time", "desc": True}]}},
            ],
            "fieldConfig": {"defaults": {"mappings": [UE_MAPPING], "custom": {"filterable": True}}, "overrides": [
                {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "custom.width", "value": 170}]},
                {"matcher": {"id": "byName", "options": "Device"}, "properties": [{"id": "custom.width", "value": 150}]},
                {"matcher": {"id": "byName", "options": "Event"}, "properties": ev_props}]},
            "options": {"showHeader": True, "cellHeight": "sm"}}


def status_grid(title, query, desc, thresholds, n=30):
    return {"type": "status-history", "title": title, "description": desc, "datasource": DS,
            "interval": "15m", "maxDataPoints": 200,
            "targets": [os_target(query, COUNT, [terms("source", n), HIST])],
            "transformations": [{"id": "renameByRegex", "options": {"regex": "^yau-mco-flt-ue(\\d+)$",
                                                                    "renamePattern": "UE$1"}}],
            "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "thresholds": thresholds,
                                         "custom": {"fillOpacity": 85, "lineWidth": 0}}, "overrides": []},
            "options": {"showValue": "never", "rowHeight": 0.85, "colWidth": 0.95,
                        "legend": {"showLegend": False}, "tooltip": {"mode": "single"}}}


# UE -> machine, from the flows Akvorado labels with the fleet-monitor register (last day).
MACHINES_SQL = """
SELECT lower(concat('yau-mco-flt-', n)) AS source, anyIf(t, t != '') AS Machine
FROM (SELECT SrcNetName AS n, SrcNetTenant AS t FROM flows
      WHERE TimeReceived > now() - INTERVAL 1 DAY AND match(SrcNetName, '^UE[0-9]+$'))
GROUP BY source"""


# ------------------------------------------------------------------ overview
heading("Overview")
theme_q = {name: events(*codes) for name, codes in THEMES.items()}
row_of([
    (stat("Link / routing changes", os_target(f"{LOW} AND {theme_q['Stability']}", COUNT, [HIST]), "sum",
          "Interface up/down, BGP / PIM / EIGRP neighbour changes.",
          steps(("green", None), ("yellow", 1), ("orange", 1000)), sparkline=True), 5),
    (stat("UEs with under-voltage", os_target(f"{theme_q['Machine power']} AND {UES}", COUNT, [terms("source")]),
          "count", "UEs reporting ignition / supply under-voltage (machine battery, wiring, cranking).",
          steps(("green", None), ("orange", 1), ("red", 10))), 5),
    (stat("Config changes", os_target('cisco_event:("SYS-5-CONFIG_I" OR "SYS-5-CONFIG_P")', COUNT, [HIST]), "sum",
          "Configuration changes, manual (CONFIG_I) and automated (CONFIG_P).",
          steps(("blue", None)), sparkline=True), 5),
    (stat("Hardware events", os_target(theme_q["Hardware"], COUNT, [HIST]), "sum",
          "Module insertion / removal / offline (OIR).", steps(("green", None), ("purple", 1)), sparkline=True), 5),
    (stat("Devices to tidy up", os_target(theme_q["Hygiene"], COUNT, [terms("source")]), "count",
          "Devices logging hygiene events (native VLAN mismatch, leftover Plug-and-Play, ...).",
          steps(("green", None), ("yellow", 1))), 4),
], 5)
other = f"{LOW} AND NOT ({' OR '.join(theme_q.values())}) AND NOT cisco_event:${{noise}}"
row_of([(bars("Lower-severity events by theme",
              [counted(name, f"{LOW} AND {q}", chr(65 + i)) for i, (name, q) in enumerate(theme_q.items())]
              + [counted("Other", other, "F")],
              "Severity 4-7 messages per 15 minutes, by theme. 'Other' excludes SSH session chatter unless "
              "'Session chatter' is set to Show.",
              [{"matcher": {"id": "byName", "options": n},
                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
               for n, c in THEME_COLOURS.items()]), 24)], 8)

# ------------------------------------------------------------------ stability
heading("Link and routing stability")
row_of([
    (status_grid("Flaps by UE (per 15 min)", f"{theme_q['Stability']} AND {UES}",
                 "The 30 UEs with the most link / neighbour changes. Darker = more changes in that 15 minutes. "
                 "A vertical band = many UEs at once (shared cause); a streak = one unstable UE.",
                 steps(("transparent", None), ("yellow", 1), ("orange", 5), ("red", 20))), 14),
    (device_matrix("Most unstable devices", theme_q["Stability"], "cisco_event",
                   "Link / neighbour change counts per device and event. Click a device to open it in Graylog.",
                   theme_q["Stability"]), 10),
], 14)

# ------------------------------------------------------------------ power
heading("Machine power")
power_table = {
    "type": "table", "title": "Under-voltage by machine", "datasource": MIXED,
    "description": "Under-voltage starts per UE, with its machine (from the fleet-monitor register via Akvorado). "
                   "Repeated events on one machine suggest a battery / alternator / wiring issue.",
    "targets": [
        dict(os_target('cisco_event:"IGNITION-4-IGN_UNDER_VOLTAGE"', [
            {"id": "1", "type": "count"}, {"id": "5", "type": "max", "field": "timestamp"}], [terms("source", 200)]),
             datasource=DS),
        {"refId": "B", "datasource": CH, "editorType": "sql", "format": 1, "queryType": "table",
         "rawSql": MACHINES_SQL.strip()},
    ],
    "transformations": [
        {"id": "joinByField", "options": {"byField": "source", "mode": "outerTabular"}},
        {"id": "filterByValue", "options": {"type": "include", "match": "any", "filters": [
            {"fieldName": "Count", "config": {"id": "greater", "options": {"value": 0}}}]}},
        {"id": "organize", "options": {"indexByName": {"source": 0, "Machine": 1, "Count": 2, "Max": 3},
                                       "renameByName": {"source": "UE", "Count": "Under-voltage events",
                                                        "Max": "Last event"}}},
        {"id": "sortBy", "options": {"sort": [{"field": "Under-voltage events", "desc": True}]}},
    ],
    "fieldConfig": {"defaults": {"mappings": [UE_MAPPING], "custom": {"filterable": True}}, "overrides": [
        {"matcher": {"id": "byName", "options": "Last event"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}]},
        {"matcher": {"id": "byName", "options": "Under-voltage events"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
            {"id": "color", "value": {"mode": "continuous-YlRd"}}, {"id": "min", "value": 0}]},
        {"matcher": {"id": "byName", "options": "UE"}, "properties": [
            {"id": "links", "value": [graylog_link(theme_q["Machine power"])]}]}]},
    "options": {"showHeader": True, "cellHeight": "sm"}}
power_timeline = bars("Under-voltage events over time",
                      [os_target(f'cisco_event:"IGNITION-4-IGN_UNDER_VOLTAGE" AND {UES}', COUNT,
                                 [terms("source", 15), HIST])],
                      "Under-voltage starts per UE per hour (top 15 UEs).", interval="1h")
power_timeline["transformations"] = [{"id": "renameByRegex", "options": {"regex": "^yau-mco-flt-ue(\\d+)$",
                                                                         "renamePattern": "UE$1"}}]
row_of([(power_timeline, 14), (power_table, 10)], 10)

# ------------------------------------------------------------------ changes and access
heading("Changes and access")
change_events = {"SYS-5-CONFIG_I": ("Config change (manual)", "blue"),
                 "SYS-5-CONFIG_P": ("Config change (automated)", "purple"),
                 "SEC_LOGIN-5-LOGIN_SUCCESS": ("Login", "green"),
                 "SEC_LOGIN-4-LOGIN_FAILED": ("Login failed", "red"),
                 "DMI-5-AUTH_PASSED": ("NETCONF/API login", "green"),
                 "SYS-6-PRIVCFG_ENCRYPT_SUCCESS": ("Config saved (encrypted)", "text")}
changes_over_time = bars("Changes and logins over time",
                         [counted("Config (manual)", 'cisco_event:"SYS-5-CONFIG_I"', "A"),
                          counted("Config (automated)", 'cisco_event:"SYS-5-CONFIG_P"', "B"),
                          counted("Logins", 'cisco_event:("SEC_LOGIN-5-LOGIN_SUCCESS" OR "DMI-5-AUTH_PASSED")', "C"),
                          counted("Failed logins", 'cisco_event:"SEC_LOGIN-4-LOGIN_FAILED"', "D")],
                         "Configuration changes and logins per hour.",
                         [{"matcher": {"id": "byName", "options": n},
                           "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
                          for n, c in (("Config (manual)", "blue"), ("Config (automated)", "purple"),
                                       ("Logins", "green"), ("Failed logins", "red"))], interval="1h")
row_of([
    (changes_over_time, 8),
    (raw_table("Change log", theme_q["Changes and access"],
               "Latest 300 configuration changes and logins. The message says who and from where "
               "(e.g. 'Configured from console by <user> on vty0 (<ip>)').", 300, change_events), 16),
], 12)

# ------------------------------------------------------------------ hardware
heading("Hardware")
row_of([
    (device_matrix("Module events by device", theme_q["Hardware"], "cisco_event",
                   "Module insertion / removal / offline events per device.", theme_q["Hardware"]), 10),
    (raw_table("Latest module events", theme_q["Hardware"], "Most recent OIR messages (which module / slot).", 200), 14),
], 9)

# ------------------------------------------------------------------ hygiene
heading("Hygiene and noise")
top_low = {
    "type": "table", "title": "Top lower-severity events by volume", "datasource": DS,
    "description": "All severity 4-7 events (including session chatter), with device counts. High volume from "
                   "few devices = a config issue to fix or noise to filter in Graylog.",
    "targets": [os_target(LOW, [{"id": "1", "type": "count"}, {"id": "4", "type": "cardinality", "field": "source"}],
                          [terms("cisco_event", 40)])],
    "transformations": [{"id": "organize", "options": {"renameByName": {
        "cisco_event": "Event", "Count": "Messages", "Unique Count": "Devices"}}}],
    "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": [
        {"matcher": {"id": "byName", "options": "Messages"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
            {"id": "color", "value": {"mode": "continuous-GrYlRd"}}, {"id": "min", "value": 0}]},
        {"matcher": {"id": "byName", "options": "Devices"}, "properties": [
            {"id": "custom.width", "value": 80}, {"id": "custom.align", "value": "center"}]}]},
    "options": {"showHeader": True, "cellHeight": "sm"}}
row_of([
    (top_low, 10),
    (device_matrix("Devices to tidy up", theme_q["Hygiene"], "cisco_event",
                   "Native VLAN mismatches (fix the trunk config), leftover Plug-and-Play / Catalyst Center sync "
                   "chatter (PnP agent still active after onboarding), certificate messages.",
                   theme_q["Hygiene"]), 14),
], 12)

dashboard = {
    "uid": "fleet-events",
    "title": "Fleet events",
    "tags": ["graylog", "fleet"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,
    "refresh": "5m",
    "time": {"from": "now-24h", "to": "now"},
    "schemaVersion": 39,
    "description": "Lower-priority syslog (severity 4-7) by theme: stability, machine power, changes, hardware, hygiene.",
    "links": [
        {"type": "link", "title": "Fleet syslog", "url": "/d/fleet-syslog", "icon": "dashboard", "keepTime": True},
        {"type": "link", "title": "Fleet UE traffic", "url": "/d/fleet-traffic", "icon": "dashboard", "keepTime": True},
        {"type": "link", "title": "Graylog", "url": GRAYLOG + "/", "icon": "external link", "targetBlank": True},
    ],
    "templating": {"list": [{
        "name": "noise", "label": "Session chatter", "type": "custom",
        # value = the events to hide; "Show" hides a code that never occurs.
        "query": "Hidden : (" + " OR ".join(f'"{c}"' for c in NOISE) + "),Shown : (\"NONE\")",
        "options": [], "multi": False, "includeAll": False,
        "current": {"selected": True, "text": "Hidden",
                    "value": "(" + " OR ".join(f'"{c}"' for c in NOISE) + ")"}}]},
    "panels": panels,
}

if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(dashboard, f, indent=2)
        f.write("\n")
    print(f"wrote {OUT}: {len(panels)} panels")
