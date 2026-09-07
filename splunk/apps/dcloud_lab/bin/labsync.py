#!/usr/bin/env python
# ===========================================================================
# labsync.py - custom search command wrapper for labsync.sh.
#
# Usage in a dashboard:  | makeresults | labsync
# Runs labsync.sh (which does the git snapshot + push) and returns one result
# row with fields: status, message, commit, branch.
# ===========================================================================
import os
import json
import subprocess

import splunk.Intersplunk as si


def _run():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "labsync.sh")
    try:
        out = subprocess.check_output(
            ["/bin/bash", script],
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=180,
        )
        lines = [l for l in out.strip().splitlines() if l.strip()]
        last = lines[-1] if lines else ""
        try:
            return json.loads(last)
        except Exception:
            return {"status": "error", "message": (out or "no output")[-800:],
                    "commit": "", "branch": "lab-snapshot"}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": (e.output or "script failed")[-800:],
                "commit": "", "branch": "lab-snapshot"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "message": str(e), "commit": "", "branch": "lab-snapshot"}


try:
    results, dummyresults, settings = si.getOrganizedResults()
    si.outputResults([_run()])
except Exception as e:  # noqa: BLE001
    si.generateErrorResults(str(e))
