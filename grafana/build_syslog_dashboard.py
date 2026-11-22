#!/usr/bin/env python3
"""build_syslog_dashboard.py - generate grafana/dashboards/fleet-syslog.json.

Fleet syslog from Graylog's OpenSearch (indices graylog_*), via the "Graylog OpenSearch"
data source. Fields come from Graylog's "Cisco IOS" pipeline (../graylog/setup_graylog.py):
  severity       0-7 (Cisco %FAC-SEV-MNEMONIC code, else syslog level)
  severity_name  "0-emergency" .. "7-debug"
  source         device hostname, stored lowercase (yau-mco-flt-ue034, yau-mco-oc4-tag01, ...)
  cisco_event    FAC-SEV-MNEMONIC, e.g. LINK-3-UPDOWN
  message

    python3 grafana/build_syslog_dashboard.py     # writes dashboards/fleet-syslog.json
Test every panel through Grafana: grafana/test_dashboards.py (on fleet-mon).
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dashboards", "fleet-syslog.json")
DS = {"type": "grafana-opensearch-datasource", "uid": "graylog-os"}
GRAYLOG = "https://10.231.137.100:9443"

UES = "source:yau-mco-flt-ue*"
LEVEL = "severity:<=$level"
SEV_COLOURS = {"0-emergency": "dark-red", "1-alert": "red", "2-critical": "orange", "3-error": "yellow"}
# yau-mco-flt-ue034 -> UE034 (display only; the value stays the hostname for links).
UE_MAPPING = {"type": "regex", "options": {"pattern": "^yau-mco-flt-ue(\\d+)$", "result": {"text": "UE$1"}}}
GRAYLOG_LINK = {"title": "Open in Graylog", "targetBlank": True,
                "url": (GRAYLOG + "/search?q=source%3A${__value.raw}%20AND%20severity%3A%3C%3D${level}"
                        "&rangetype=absolute&from=${__from:date:iso}&to=${__to:date:iso}")}

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


def os_target(query, metrics, buckets, kind="Metric", ref="A"):
    return {"refId": ref, "datasource": DS, "queryType": "lucene", "luceneQueryType": kind,
            "query": query, "timeField": "timestamp", "metrics": metrics, "bucketAggs": buckets}


COUNT = [{"id": "1", "type": "count"}]
SEV_NAMES = {"0-emergency": "Emergency", "1-alert": "Alert", "2-critical": "Critical", "3-error": "Error"}
SEV_BY_NUM = {"0": ("Emergency", "dark-red"), "1": ("Alert", "red"), "2": ("Critical", "orange"), "3": ("Error", "yellow")}


def terms(field, size=500, id_="2"):
    return {"id": id_, "type": "terms", "field": field,
            "settings": {"size": str(size), "order": "desc", "orderBy": "_count", "min_doc_count": "1"}}


HIST = {"id": "9", "type": "date_histogram", "field": "timestamp",
        "settings": {"interval": "auto", "min_doc_count": "0", "trimEdges": "0"}}


def steps(*pairs):
    """steps(("green", None), ("orange", 1), ("red", 10)) -> Grafana absolute thresholds."""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in pairs]}


def sev_series_overrides():
    """Series named 1-alert etc.: severity colour and a friendly name."""
    return [{"matcher": {"id": "byName", "options": n},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": SEV_COLOURS[n]}},
                            {"id": "displayName", "value": SEV_NAMES[n]}]} for n in SEV_COLOURS]


def stat(title, target, calc, desc, thresholds, sparkline=False):
    return {"type": "stat", "title": title, "description": desc, "datasource": DS, "targets": [target],
            "fieldConfig": {"defaults": {"unit": "short", "decimals": 0, "color": {"mode": "thresholds"},
                                         "thresholds": thresholds}, "overrides": []},
            "options": {"reduceOptions": {"calcs": [calc], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "area" if sparkline else "none",
                        "textMode": "value", "justifyMode": "center", "wideLayout": True}}


def severity_matrix(title, query, desc, name_col):
    """Device x severity counts: terms(source) > terms(severity_name), pivoted with groupingToMatrix."""
    sev_cols = [{"matcher": {"id": "byName", "options": n}, "properties": [
        {"id": "displayName", "value": SEV_NAMES[n]},
        {"id": "color", "value": {"mode": "thresholds"}},
        {"id": "thresholds", "value": steps(("transparent", None), (c, 1))},
        {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}},
        {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "·"}}}]},
        {"id": "custom.width", "value": 95}, {"id": "custom.align", "value": "center"}]}
        for n, c in SEV_COLOURS.items()]
    return {"type": "table", "title": title, "description": desc, "datasource": DS,
            "targets": [os_target(query, COUNT, [terms("source"), terms("severity_name", 10, "3")], "Metric")],
            "transformations": [
                {"id": "groupingToMatrix", "options": {"columnField": "severity_name", "rowField": "source",
                                                       "valueField": "Count", "emptyValue": "zero"}},
                {"id": "calculateField", "options": {"mode": "reduceRow", "reduce": {"reducer": "sum"},
                                                     "alias": "Total", "replaceFields": False}},
                {"id": "sortBy", "options": {"sort": [{"field": "Total", "desc": True}]}},
            ],
            "fieldConfig": {
                "defaults": {"mappings": [UE_MAPPING], "custom": {"filterable": True}},
                "overrides": [
                    {"matcher": {"id": "byType", "options": "string"},
                     "properties": [{"id": "displayName", "value": name_col}, {"id": "links", "value": [GRAYLOG_LINK]},
                                    {"id": "custom.width", "value": 150}]},
                    *sev_cols,
                    {"matcher": {"id": "byName", "options": "Total"}, "properties": [
                        {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic",
                                                               "valueDisplayMode": "text"}},
                        {"id": "color", "value": {"mode": "continuous-YlRd"}}, {"id": "min", "value": 0}]},
                ]},
            "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}}}


# --- overview
heading("Overview")
row_of([
    (stat("Critical or worse", os_target("severity:<=2", COUNT, [HIST]), "sum",
          "Messages at severity 0-2 (emergency, alert, critical) from any device, with the trend.",
          steps(("green", None), ("orange", 1), ("red", 100)), sparkline=True), 5),
    (stat("Errors", os_target("severity:3", COUNT, [HIST]), "sum",
          "Messages at severity 3 (error) from any device, with the trend.",
          steps(("green", None), ("yellow", 1), ("orange", 1000)), sparkline=True), 5),
    (stat("UEs at critical or worse", os_target(f"severity:<=2 AND {UES}", COUNT, [terms("source")]), "count",
          "Number of UEs that logged severity 0-2.",
          steps(("green", None), ("orange", 1), ("red", 10))), 5),
    (stat("UEs at the selected level", os_target(f"{LEVEL} AND {UES}", COUNT, [terms("source")]), "count",
          "Number of UEs that logged at the level chosen at the top (default: error or worse).",
          steps(("green", None), ("yellow", 1), ("orange", 25), ("red", 50))), 5),
    ({"type": "piechart", "title": "Severity mix", "datasource": DS,
      "description": "Share of messages per severity at the selected level.",
      "targets": [os_target(LEVEL, COUNT, [terms("severity_name", 10, "3"), HIST])],
      "fieldConfig": {"defaults": {"unit": "short"}, "overrides": sev_series_overrides()},
      "options": {"pieType": "donut", "displayLabels": ["percent"],
                  "reduceOptions": {"calcs": ["sum"], "fields": "", "values": False},
                  "legend": {"displayMode": "table", "placement": "right", "values": ["value"]},
                  "tooltip": {"mode": "single"}}}, 4),
], 5)

# --- timeline
heading("Timeline")
timeline = {
    "type": "timeseries", "title": "Messages by severity", "datasource": DS, "interval": "5m",
    "description": "Count per 5-minute (or wider) interval at the selected level, stacked by severity.",
    "targets": [os_target(LEVEL, COUNT, [terms("severity_name", 10, "3"), HIST])],
    "fieldConfig": {"defaults": {"unit": "short", "custom": {
        "drawStyle": "bars", "fillOpacity": 85, "gradientMode": "hue", "lineWidth": 0, "barAlignment": 0,
        "stacking": {"mode": "normal", "group": "A"}, "showPoints": "never",
        "axisSoftMin": 0, "axisLabel": "messages"}},
        "overrides": sev_series_overrides()},
    "options": {"legend": {"displayMode": "list", "placement": "bottom", "calcs": ["sum"]},
                "tooltip": {"mode": "multi", "sort": "desc"}}}
ues_reporting = {
    "type": "timeseries", "title": "UEs reporting at the selected level", "datasource": DS, "interval": "5m",
    "description": "Distinct UEs logging at the selected level per interval. Amber above 10, red above 25: "
                   "many UEs at once points at a shared cause (LTE core, TAG, core switch), not one UE.",
    "targets": [os_target(f"{LEVEL} AND {UES}", [{"id": "1", "type": "cardinality", "field": "source"}], [HIST])],
    "fieldConfig": {"defaults": {
        "unit": "short", "decimals": 0, "displayName": "UEs reporting", "min": 0,
        "color": {"mode": "thresholds"}, "thresholds": steps(("green", None), ("orange", 10), ("red", 25)),
        "custom": {"drawStyle": "line", "lineInterpolation": "smooth", "lineWidth": 2, "fillOpacity": 25,
                   "gradientMode": "scheme", "showPoints": "never", "thresholdsStyle": {"mode": "dashed"},
                   "axisLabel": "UEs"}}, "overrides": []},
    "options": {"legend": {"displayMode": "list", "placement": "bottom", "calcs": ["max", "mean"]},
                "tooltip": {"mode": "single"}}}
row_of([(timeline, 14), (ues_reporting, 10)], 9)

status = {
    "type": "status-history", "title": "Status by UE (worst severity per interval)", "datasource": DS,
    "interval": "15m", "maxDataPoints": 200,
    "description": "The 30 UEs with the most messages at the selected level; each cell is the worst severity "
                   "logged in that interval. A vertical band across many UEs = shared event (outage); "
                   "a horizontal streak = one UE with an ongoing problem.",
    "targets": [os_target(f"{LEVEL} AND {UES}", [{"id": "1", "type": "min", "field": "severity"}],
                          [terms("source", 30), HIST])],
    "transformations": [{"id": "renameByRegex", "options": {"regex": "^yau-mco-flt-ue(\\d+)$", "renamePattern": "UE$1"}}],
    "fieldConfig": {"defaults": {
        "color": {"mode": "thresholds"}, "thresholds": steps(("transparent", None)),
        "mappings": [{"type": "value", "options": {
            k: {"text": t, "color": c, "index": i} for i, (k, (t, c)) in enumerate(SEV_BY_NUM.items())}}],
        "custom": {"fillOpacity": 85, "lineWidth": 0}}, "overrides": []},
    "options": {"showValue": "never", "rowHeight": 0.85, "colWidth": 0.95,
                "legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                "tooltip": {"mode": "single"}}}
row_of([(status, 24)], 14)

# --- per device
heading("By device")
row_of([
    (severity_matrix("UEs by severity", f"{LEVEL} AND {UES}",
                     "Each UE's message count per severity at the selected level. Click a UE to open its "
                     "messages in Graylog.", "UE"), 12),
    (severity_matrix("Infrastructure by severity", f"{LEVEL} AND NOT {UES}",
                     "TAGs, core switches and other non-UE devices. Click a device to open it in Graylog.",
                     "Device"), 12),
], 12)

# --- events and messages
heading("Events and messages")
events = {
    "type": "table", "title": "Top events", "datasource": DS,
    "description": "Cisco event codes (FAC-SEV-MNEMONIC) at the selected level, with how many devices logged each.",
    "targets": [os_target(LEVEL, [{"id": "1", "type": "count"},
                                  {"id": "4", "type": "cardinality", "field": "source"}],
                          [terms("cisco_event", 50)])],
    "transformations": [{"id": "organize", "options": {"renameByName": {
        "cisco_event": "Event", "Count": "Messages", "Unique Count": "Devices"}}}],
    "fieldConfig": {"defaults": {"custom": {"filterable": True}}, "overrides": [
        {"matcher": {"id": "byName", "options": "Messages"}, "properties": [
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
            {"id": "color", "value": {"mode": "continuous-YlRd"}}, {"id": "min", "value": 0}]},
        {"matcher": {"id": "byName", "options": "Devices"}, "properties": [
            {"id": "custom.width", "value": 80}, {"id": "custom.align", "value": "center"}]}]},
    "options": {"showHeader": True, "cellHeight": "sm"}}
latest = {
    "type": "table", "title": "Latest critical-or-worse messages", "datasource": DS,
    "description": "Most recent 500 messages at severity 0-2, newest first.",
    "targets": [os_target("severity:<=2", [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}],
                          [], "RawData")],
    "transformations": [
        {"id": "filterFieldsByName", "options": {"include": {"names": [
            "timestamp", "source", "severity_name", "cisco_event", "message"]}}},
        {"id": "organize", "options": {
            "indexByName": {"timestamp": 0, "source": 1, "severity_name": 2, "cisco_event": 3, "message": 4},
            "renameByName": {"timestamp": "Time", "source": "Device", "severity_name": "Severity",
                             "cisco_event": "Event", "message": "Message"}}},
        {"id": "sortBy", "options": {"sort": [{"field": "Time", "desc": True}]}},
    ],
    "fieldConfig": {"defaults": {"mappings": [UE_MAPPING], "custom": {"filterable": True}}, "overrides": [
        {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "custom.width", "value": 170}]},
        {"matcher": {"id": "byName", "options": "Device"}, "properties": [{"id": "custom.width", "value": 150}]},
        {"matcher": {"id": "byName", "options": "Event"}, "properties": [{"id": "custom.width", "value": 260}]},
        {"matcher": {"id": "byName", "options": "Severity"}, "properties": [
            {"id": "custom.width", "value": 95},
            {"id": "mappings", "value": [{"type": "value", "options": {
                n: {"text": SEV_NAMES[n], "color": c, "index": i} for i, (n, c) in enumerate(SEV_COLOURS.items())}}]},
            {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}]}]},
    "options": {"showHeader": True, "cellHeight": "sm"}}
row_of([(events, 8), (latest, 16)], 13)

dashboard = {
    "uid": "fleet-syslog",
    "title": "Fleet syslog",
    "tags": ["graylog", "fleet"],
    "timezone": "browser",
    "editable": True,
    "graphTooltip": 1,  # shared crosshair across the time panels
    "refresh": "1m",
    "time": {"from": "now-24h", "to": "now"},
    "schemaVersion": 39,
    "description": "Syslog from UEs and infrastructure, from Graylog's OpenSearch. Search and read messages in Graylog.",
    "links": [
        {"type": "link", "title": "Fleet UE traffic", "url": "/d/fleet-traffic", "icon": "dashboard",
         "keepTime": True},
        {"type": "link", "title": "Fleet events", "url": "/d/fleet-events", "icon": "dashboard", "keepTime": True},
        {"type": "link", "title": "Graylog", "url": GRAYLOG + "/", "icon": "external link", "targetBlank": True},
    ],
    "templating": {"list": [{
        "name": "level", "label": "Severity", "type": "custom",
        "query": "Error or worse : 3,Critical or worse : 2,Alert or worse : 1,Emergency : 0",
        "options": [], "multi": False, "includeAll": False,
        "current": {"selected": True, "text": "Error or worse", "value": "3"}}]},
    "panels": panels,
}

if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(dashboard, f, indent=2)
        f.write("\n")
    print(f"wrote {OUT}: {len(panels)} panels")
