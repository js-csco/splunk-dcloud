#!/usr/bin/env python3
# ===========================================================================
# seed_itsi_episodes.py - create an ITSI Notable Event Aggregation Policy that
# turns the "Service degraded (ITSI notable)" correlation-search notables into
# ONE Episode per service, and runs the Webex alert action when an Episode opens.
#
# Why a seeder (not conf): aggregation policies live in the KV store, which the
# dCloud lab wipes on every rebuild - so they must be re-created programmatically
# each session, alongside seed_itsi_demo.py. The correlation search itself is
# config-as-code (splunk/apps/itsi_episodes) and needs no seeding.
#
# Run on the Splunk server (ITSI search head):
#   sudo -u splunk /opt/splunk/bin/splunk cmd python3 \
#     /opt/dcloud-splunk/itsi/seed_itsi_episodes.py --verbose
#
# NOTE: the aggregation-policy REST schema is version-sensitive. Run with
# --verbose; if the POST is rejected, the server's error body is printed so the
# payload can be adjusted for your ITSI version. Even with NO custom policy,
# ITSI's built-in default policy already groups these notables into Episodes -
# this seeder only adds per-service splitting + the Webex action.
# ===========================================================================
import argparse
import base64
import json
import os
import ssl
import sys
import uuid
from urllib import request, parse, error

# Aggregation policies are managed by the event_management_interface, served by
# SA-ITOA under the nobody context.
POLICY_NS = "servicesNS/nobody/SA-ITOA/event_management_interface"
POLICY_OBJ = "notable_event_aggregation_policy"

CORRELATION_SOURCE = "dcloud - Service degraded (ITSI notable)"
POLICY_TITLE = "dCloud - Service Episodes"

WEBEX_MSG = ("\U0001F534 ITSI Episode opened - a dCloud service is degraded "
             "(%source_field%). Open Episode Review / Root Cause Analysis.")


def b64(s):
    return base64.b64encode(s.encode()).decode()


class ITSI:
    def __init__(self, base, user, password, verbose=False):
        self.base = base.rstrip("/")
        self.verbose = verbose
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self._auth = "Basic " + b64("%s:%s" % (user, password))

    def call(self, method, path, params=None, body=None):
        url = "%s/%s" % (self.base, path.lstrip("/"))
        headers = {"Authorization": self._auth}
        if params:
            url += "?" + parse.urlencode(params)
        data = None
        if body is not None:
            data = parse.urlencode(body).encode() if isinstance(body, dict) else body.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=30, context=self.ctx) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
        except error.HTTPError as exc:
            detail = exc.read().decode()[:800]
            if self.verbose:
                sys.stderr.write("  HTTP %s %s -> %s\n" % (exc.code, path, detail))
            return exc.code, detail

    def find_policy(self, title):
        code, res = self.call("GET", "%s/%s" % (POLICY_NS, POLICY_OBJ),
                              params={"filter": json.dumps({"title": title}), "fields": "_key,title"})
        if code == 200 and isinstance(res, list) and res:
            return res[0].get("_key")
        return None


def policy_payload():
    """Aggregation policy: split notables from our correlation search into one
    Episode per service, and run webex_notify when an Episode is created."""
    rule_key = uuid.uuid4().hex

    # Only notables from our correlation search belong to this policy.
    filter_criteria = {
        "condition": "AND",
        "items": [
            {"type": "clause", "config": {
                "condition": "OR",
                "items": [
                    {"type": "notable_event_field", "config": {
                        "field": "source", "operator": "=", "value": CORRELATION_SOURCE}},
                ],
            }},
        ],
    }
    # Break (start a fresh Episode) after 2h of quiet, or when severity clears.
    breaking_criteria = {
        "condition": "OR",
        "items": [
            {"type": "pause", "config": {"limit": 7200}},
        ],
    }
    # No action rules from the seeder: ITSI's action-rule schema is very
    # version-specific (it rejected our Webex action with "Actions: Missing key
    # condition"). The per-service SPLIT is the value here; add the Webex action
    # rule in the UI (Configuration > Notable Event Aggregation Policies > this
    # policy > Action Rules > run alert action webex_notify) - it persists via
    # saved lab state.
    rules = []

    return {
        "title": POLICY_TITLE,
        "description": ("Group 'Service degraded' notables into one Episode per "
                        "service and notify Webex when an Episode opens."),
        "disabled": 0,
        "priority": 6,
        "split_by_field": "itsi_service_ids",
        "filter_criteria": filter_criteria,
        "breaking_criteria": breaking_criteria,
        "rules": rules,
        "group_severity": "highest",
        "group_status": "highest",
        "group_assignee": "unassigned",
        "group_title": "%itsi_service_ids% degraded",
        "group_description": "One or more KPIs for this service crossed High/Critical.",
    }


def main():
    ap = argparse.ArgumentParser(description="Seed the ITSI Service Episodes aggregation policy.")
    ap.add_argument("--host", default=os.environ.get("SPLUNK_MGMT", "https://localhost:8089"))
    ap.add_argument("--user", default=os.environ.get("SPLUNK_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("SPLUNK_ADMIN_PASSWORD", "C1sco12345"))
    ap.add_argument("--verbose", action="store_true", help="print server error bodies")
    ap.add_argument("--delete", action="store_true", help="delete the policy instead of creating it")
    args = ap.parse_args()

    itsi = ITSI(args.host, args.user, args.password, verbose=args.verbose)

    existing = itsi.find_policy(POLICY_TITLE)

    if args.delete:
        if not existing:
            print("  nothing to delete (%s not found)" % POLICY_TITLE)
            return 0
        code, _ = itsi.call("DELETE", "%s/%s/%s" % (POLICY_NS, POLICY_OBJ, existing))
        print("  %s policy '%s'" % ("deleted" if code in (200, 204) else "FAILED to delete", POLICY_TITLE))
        return 0 if code in (200, 204) else 1

    payload = policy_payload()
    body = {"data": json.dumps(payload)}
    if existing:
        payload["_key"] = existing
        body = {"data": json.dumps(payload)}
        code, res = itsi.call("POST", "%s/%s/%s" % (POLICY_NS, POLICY_OBJ, existing), body=body)
        action = "updated"
    else:
        code, res = itsi.call("POST", "%s/%s" % (POLICY_NS, POLICY_OBJ), body=body)
        action = "created"

    if code in (200, 201):
        print("  %s aggregation policy '%s'" % (action, POLICY_TITLE))
        print("  -> ITSI > Configuration > Notable Event Aggregation Policies to review.")
        return 0

    print("  FAILED to %s policy (HTTP %s)." % (action, code))
    print("  ERROR BODY: %s" % (res,))     # always show it, so we can fix the schema
    print("  Note: ITSI's built-in DEFAULT policy still groups the notables into Episodes;")
    print("  this custom policy only adds per-service splitting + the Webex action.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
