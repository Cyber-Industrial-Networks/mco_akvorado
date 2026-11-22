#!/usr/bin/env python3
"""test_dashboards.py - run every panel query of the deployed dashboards through Grafana.

Run on fleet-mon after deploying (needs the break-glass admin password file):
    python3 -I ~/akvorado/grafana/test_dashboards.py [uid | file.json ...]   # default: fleet-traffic fleet-syslog

A .json argument tests that dashboard file (e.g. a newly generated one) before it is deployed.

Template variables are replaced by their default (the dashboard's "current" value, or the
All value). Only the queries are tested: panel transformations run in the browser.
"""
import base64
import json
import sys
import urllib.error
import urllib.request

GRAFANA = "http://127.0.0.1:3001"
PASSWORD_FILE = "/srv/akvorado/grafana-admin-password.txt"
RANGE = ("now-6h", "now")


def main(uids):
    pw = open(PASSWORD_FILE).read().strip()
    headers = {"Authorization": "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode(),
               "Content-Type": "application/json"}

    def call(path, body=None):
        req = urllib.request.Request(GRAFANA + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        return json.load(urllib.request.urlopen(req, timeout=120))

    failures = 0
    for uid in uids:
        dash = json.load(open(uid)) if uid.endswith(".json") else call(f"/api/dashboards/uid/{uid}")["dashboard"]
        print(f"== {dash['title']} ({uid}, version {dash.get('version')})")
        subs = {"$__interval_s": "60"}
        for v in dash.get("templating", {}).get("list", []):
            if v.get("includeAll") and v.get("allValue"):
                val = v["allValue"]
            else:
                val = v.get("current", {}).get("value", "")
                val = val[0] if isinstance(val, list) else val
            for form in (f"${{{v['name']}:singlequote}}", f"${{{v['name']}}}", f"${v['name']}"):
                subs[form] = val
        for p in dash["panels"]:
            for t in p.get("targets", []):
                q = json.loads(json.dumps(t))
                for k in ("rawSql", "query"):
                    if isinstance(q.get(k), str):
                        for a, b in subs.items():
                            q[k] = q[k].replace(a, b)
                q.update(intervalMs=60000, maxDataPoints=1000)
                try:
                    res = call("/api/ds/query", {"from": RANGE[0], "to": RANGE[1], "queries": [q]})["results"][t["refId"]]
                    if res.get("error"):
                        raise RuntimeError(res["error"])
                    frames = res.get("frames", [])
                    rows = sum(len(f["data"]["values"][0]) for f in frames if f["data"].get("values"))
                    fields = [x["name"] for x in frames[0]["schema"]["fields"]] if frames else []
                    print(f"  OK   {p['title']:<40} frames={len(frames):<3} rows={rows:<5} {fields[:5]}")
                except (RuntimeError, urllib.error.HTTPError) as e:
                    failures += 1
                    msg = e.read().decode() if isinstance(e, urllib.error.HTTPError) else str(e)
                    print(f"  FAIL {p['title']}: {msg[:300]}")
    print("failures:", failures)
    return failures


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1:] or ["fleet-traffic", "fleet-syslog"]) else 0)
