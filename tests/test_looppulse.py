#!/usr/bin/env python3
"""Tests for loop-pulse helper (stdlib unittest)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import looppulse as lp  # noqa: E402


def base_card(**overrides):
    card = {
        "mission": "Watch experiment run-7 metrics until goal or stall",
        "sources": ["runs/run-7/metrics.json", "runs/run-7/status.txt"],
        "allowed_verbs": ["read", "plot", "annotate"],
        "stop_when": [
            {"kind": "metric_at_least", "key": "accuracy", "threshold": 0.9},
            {"kind": "obs_contains", "needle": "status=done"},
            {"kind": "status_done"},
        ],
        "wake_budget": {"max_wakes": 5, "max_hours": 4, "stall_limit": 3},
        "observation": {"recipe": "cat metrics.json status.txt"},
        "started_at": "2026-11-06T00:00:00Z",
        "wakes_used": 0,
        "idle_streak": 0,
        "last_fingerprint": None,
    }
    card.update(overrides)
    return card


class FingerprintTests(unittest.TestCase):
    def test_stable_and_whitespace_insensitive(self):
        a = lp.fingerprint("accuracy=0.5\nstatus=running")
        b = lp.fingerprint("accuracy=0.5\nstatus=running\n")
        c = lp.fingerprint("accuracy=0.5\n  status=running")
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertNotEqual(a, lp.fingerprint("accuracy=0.6\nstatus=running"))


class LintTests(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(lp.lint_charter(base_card()), [])

    def test_missing_mission(self):
        c = base_card()
        del c["mission"]
        self.assertTrue(any(e.startswith("missing:mission") for e in lp.lint_charter(c)))

    def test_bad_verbs(self):
        c = base_card(allowed_verbs=["Read Logs"])
        errs = lp.lint_charter(c)
        self.assertTrue(any("allowed_verbs" in e for e in errs))

    def test_budget_positive(self):
        c = base_card(wake_budget={"max_wakes": 0, "max_hours": 4, "stall_limit": 3})
        errs = lp.lint_charter(c)
        self.assertTrue(any("max_wakes" in e for e in errs))


class GateTests(unittest.TestCase):
    def test_first_pulse_delta(self):
        r = lp.check_wake(base_card(), "accuracy=0.4\n# runs/run-7/metrics.json", now=lp._utc_now("2026-11-06T00:10:00Z"))
        self.assertEqual(r["verdict"], "DELTA")
        self.assertTrue(r["authorized"])
        self.assertEqual(r["wakes_used"], 1)
        self.assertIn("read", r["allowed_verbs"])

    def test_echo_then_stall(self):
        card = base_card()
        obs = "accuracy=0.4\n# runs/run-7/metrics.json"
        r1 = lp.check_wake(card, obs, now=lp._utc_now("2026-11-06T00:10:00Z"))
        card = lp.apply_patch(card, r1)
        r2 = lp.check_wake(card, obs, now=lp._utc_now("2026-11-06T00:20:00Z"))
        self.assertEqual(r2["verdict"], "ECHO")
        self.assertFalse(r2["authorized"])
        self.assertEqual(r2["idle_streak"], 1)
        card = lp.apply_patch(card, r2)
        r3 = lp.check_wake(card, obs, now=lp._utc_now("2026-11-06T00:30:00Z"))
        self.assertEqual(r3["verdict"], "ECHO")
        card = lp.apply_patch(card, r3)
        r4 = lp.check_wake(card, obs, now=lp._utc_now("2026-11-06T00:40:00Z"))
        self.assertEqual(r4["verdict"], "STALL")
        self.assertEqual(r4["idle_streak"], 3)

    def test_done_on_metric(self):
        card = base_card(last_fingerprint="deadbeefdeadbeef", wakes_used=1)
        r = lp.check_wake(
            card,
            "accuracy=0.95\n# runs/run-7/metrics.json",
            now=lp._utc_now("2026-11-06T01:00:00Z"),
        )
        self.assertEqual(r["verdict"], "DONE")
        self.assertIn("metric_at_least", r["reason"])

    def test_done_status(self):
        card = base_card(last_fingerprint="aaaaaaaaaaaaaaaa", wakes_used=1)
        r = lp.check_wake(card, "state: complete\n# runs/run-7/status.txt", now=lp._utc_now("2026-11-06T01:00:00Z"))
        self.assertEqual(r["verdict"], "DONE")

    def test_budget_wakes(self):
        card = base_card(wakes_used=5)
        r = lp.check_wake(card, "accuracy=0.1\n# runs/run-7/metrics.json", now=lp._utc_now("2026-11-06T00:10:00Z"))
        self.assertEqual(r["verdict"], "BUDGET-CUT")
        self.assertEqual(r["reason"], "max_wakes")

    def test_budget_hours(self):
        card = base_card(wakes_used=1)
        r = lp.check_wake(card, "accuracy=0.1\n# runs/run-7/metrics.json", now=lp._utc_now("2026-11-06T05:00:00Z"))
        self.assertEqual(r["verdict"], "BUDGET-CUT")
        self.assertTrue(r["reason"].startswith("max_hours"))

    def test_scope_breach(self):
        card = base_card()
        r = lp.check_wake(
            card,
            "accuracy=0.1\npeeked at /etc/passwd and secrets/prod.env",
            now=lp._utc_now("2026-11-06T00:10:00Z"),
        )
        self.assertEqual(r["verdict"], "SCOPE-BREACH")

    def test_hold_missing_obs(self):
        r = lp.check_wake(base_card(), "  ", now=lp._utc_now("2026-11-06T00:10:00Z"))
        self.assertEqual(r["verdict"], "HOLD")

    def test_hold_bad_charter(self):
        c = base_card()
        del c["stop_when"]
        r = lp.check_wake(c, "x", now=lp._utc_now("2026-11-06T00:10:00Z"))
        self.assertEqual(r["verdict"], "HOLD")

    def test_delta_after_change(self):
        card = base_card()
        obs1 = "accuracy=0.4\n# runs/run-7/metrics.json"
        r1 = lp.check_wake(card, obs1, now=lp._utc_now("2026-11-06T00:10:00Z"))
        card = lp.apply_patch(card, r1)
        obs2 = "accuracy=0.55\n# runs/run-7/metrics.json"
        r2 = lp.check_wake(card, obs2, now=lp._utc_now("2026-11-06T00:20:00Z"))
        self.assertEqual(r2["verdict"], "DELTA")
        self.assertEqual(r2["idle_streak"], 0)
        self.assertTrue(r2["authorized"])


class LogTests(unittest.TestCase):
    def test_append_log(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "pulses.jsonl")
            card = base_card()
            r = lp.check_wake(card, "accuracy=0.2\n# runs/run-7/metrics.json", now=lp._utc_now("2026-11-06T00:10:00Z"))
            lp.append_log(path, r, lp._utc_now("2026-11-06T00:10:00Z"))
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["verdict"], "DELTA")


class MetricsParseTests(unittest.TestCase):
    def test_parse_eq_and_json(self):
        m = lp.parse_metrics('accuracy=0.8 loss=1.2 {"pass_rate": 0.5}')
        self.assertEqual(m["accuracy"], 0.8)
        self.assertEqual(m["loss"], 1.2)
        self.assertEqual(m["pass_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
