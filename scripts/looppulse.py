#!/usr/bin/env python3
"""loop-pulse: wake gate for recurring agent loops.

Standard library only. Commands:
  lint    CHARTER.json                 validate charter shape
  fingerprint TEXT|--file PATH         stable fingerprint of an observation
  check   CHARTER.json --obs TEXT|--file PATH [--log pulses.jsonl] [--now ISO]
                                       classify this wake; print verdict JSON
  record  CHARTER.json --obs ... [--log pulses.jsonl] [--verdict V]
                                       append a pulse line after check
  demo                                 tiny worked example

Verdicts:
  DELTA         fingerprint changed → work authorized (CONTINUE)
  ECHO          same fingerprint → report-only; bump idle_streak
  STALL         idle_streak reached stall_limit → stop the loop
  DONE          a stop_when predicate matched → stop, success
  BUDGET-CUT    wake or hour budget exhausted → stop
  SCOPE-BREACH  observation names a source outside charter.sources
  HOLD          charter incomplete / missing observation / bad clock
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

REQUIRED = ("mission", "sources", "allowed_verbs", "stop_when", "wake_budget", "observation")
BUDGET_KEYS = ("max_wakes", "max_hours", "stall_limit")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def _utc_now(iso: Optional[str] = None) -> datetime:
    if iso:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def fingerprint(text: str) -> str:
    """Stable short fingerprint: normalize whitespace, sha256 truncated."""
    norm = "\n".join(re.sub(r"[ \t]+", " ", line.strip()) for line in text.strip().splitlines())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def lint_charter(card: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    for k in REQUIRED:
        if k not in card:
            errs.append(f"missing:{k}")
    if not isinstance(card.get("mission"), str) or not card.get("mission", "").strip():
        errs.append("mission:empty")
    sources = card.get("sources")
    if not isinstance(sources, list) or not sources:
        errs.append("sources:need-nonempty-list")
    elif any(not isinstance(s, str) or not s.strip() for s in sources):
        errs.append("sources:entries-must-be-strings")
    verbs = card.get("allowed_verbs")
    if not isinstance(verbs, list) or not verbs:
        errs.append("allowed_verbs:need-nonempty-list")
    elif any(not isinstance(v, str) or not re.match(r"^[a-z][a-z0-9_-]*$", v) for v in verbs):
        errs.append("allowed_verbs:lowercase-tokens-only")
    stop = card.get("stop_when")
    if not isinstance(stop, list) or not stop:
        errs.append("stop_when:need-nonempty-list")
    else:
        for i, pred in enumerate(stop):
            if not isinstance(pred, dict) or "kind" not in pred:
                errs.append(f"stop_when[{i}]:need-kind")
                continue
            kind = pred["kind"]
            if kind == "fingerprint_equals" and "value" not in pred:
                errs.append(f"stop_when[{i}]:fingerprint_equals-needs-value")
            elif kind == "obs_contains" and "needle" not in pred:
                errs.append(f"stop_when[{i}]:obs_contains-needs-needle")
            elif kind == "metric_at_least":
                if "key" not in pred or "threshold" not in pred:
                    errs.append(f"stop_when[{i}]:metric_at_least-needs-key+threshold")
            elif kind not in ("fingerprint_equals", "obs_contains", "metric_at_least", "status_done"):
                errs.append(f"stop_when[{i}]:unknown-kind:{kind}")
    budget = card.get("wake_budget")
    if not isinstance(budget, dict):
        errs.append("wake_budget:need-object")
    else:
        for k in BUDGET_KEYS:
            if k not in budget:
                errs.append(f"wake_budget:missing:{k}")
            elif not isinstance(budget[k], int) or budget[k] < 1:
                errs.append(f"wake_budget:{k}:positive-int")
    obs = card.get("observation")
    if not isinstance(obs, dict) or "recipe" not in obs:
        errs.append("observation:need-recipe")
    started = card.get("started_at")
    if started is not None and (not isinstance(started, str) or not ISO_RE.match(started)):
        errs.append("started_at:need-ISO-8601")
    wakes_used = card.get("wakes_used", 0)
    if not isinstance(wakes_used, int) or wakes_used < 0:
        errs.append("wakes_used:nonneg-int")
    idle = card.get("idle_streak", 0)
    if not isinstance(idle, int) or idle < 0:
        errs.append("idle_streak:nonneg-int")
    last_fp = card.get("last_fingerprint")
    if last_fp is not None and (not isinstance(last_fp, str) or len(last_fp) < 8):
        errs.append("last_fingerprint:bad")
    return errs


def parse_metrics(obs: str) -> Dict[str, float]:
    """Pull KEY=NUMBER pairs (and JSON-ish \"key\": number) from observation text."""
    out: Dict[str, float] = {}
    for m in re.finditer(r"(?i)\b([a-z_][a-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\b", obs):
        out[m.group(1).lower()] = float(m.group(2))
    for m in re.finditer(r'"([a-z_][a-z0-9_]*)"\s*:\s*(-?\d+(?:\.\d+)?)', obs, re.I):
        out[m.group(1).lower()] = float(m.group(2))
    return out


def scope_breach(card: Dict[str, Any], obs: str) -> Optional[str]:
    """If observation names path-like tokens outside charter.sources, breach."""
    sources = [s.strip() for s in card["sources"]]
    # Allow mentions of sources and generic words; flag path-like tokens with a slash
    # that are not under any declared source prefix.
    tokens = set(re.findall(r"[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)+", obs))
    if not tokens:
        return None
    for tok in sorted(tokens):
        if any(tok == s or tok.startswith(s.rstrip("/") + "/") or s.startswith(tok) for s in sources):
            continue
        # also allow if any source basename appears
        if any(tok.endswith("/" + os.path.basename(s.rstrip("/"))) for s in sources):
            continue
        return tok
    return None


def stop_matched(card: Dict[str, Any], obs: str, fp: str) -> Optional[str]:
    metrics = parse_metrics(obs)
    for pred in card["stop_when"]:
        kind = pred["kind"]
        if kind == "fingerprint_equals" and fp == pred["value"]:
            return f"fingerprint_equals:{pred['value']}"
        if kind == "obs_contains" and pred["needle"] in obs:
            return f"obs_contains:{pred['needle']}"
        if kind == "status_done" and re.search(r"(?i)\b(status|state)\s*[:=]\s*(done|merged|closed|complete|success)\b", obs):
            return "status_done"
        if kind == "metric_at_least":
            key = pred["key"].lower()
            if key in metrics and metrics[key] >= float(pred["threshold"]):
                return f"metric_at_least:{key}>={pred['threshold']}"
    return None


def check_wake(
    card: Dict[str, Any],
    obs: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or _utc_now()
    errs = lint_charter(card)
    if errs:
        return {
            "verdict": "HOLD",
            "reason": "charter-invalid",
            "errors": errs,
            "authorized": False,
            "allowed_verbs": [],
        }
    if not obs or not obs.strip():
        return {
            "verdict": "HOLD",
            "reason": "missing-observation",
            "authorized": False,
            "allowed_verbs": [],
        }

    fp = fingerprint(obs)
    budget = card["wake_budget"]
    wakes_used = int(card.get("wakes_used", 0))
    idle = int(card.get("idle_streak", 0))
    started_raw = card.get("started_at")
    started = _parse_dt(started_raw) if started_raw else now

    # Budget first (this wake counts as +1 against max_wakes)
    if wakes_used + 1 > budget["max_wakes"]:
        return _result("BUDGET-CUT", "max_wakes", fp, card, authorized=False, idle=idle, wakes_used=wakes_used)
    elapsed_h = (now - started).total_seconds() / 3600.0
    if elapsed_h > budget["max_hours"]:
        return _result("BUDGET-CUT", f"max_hours:{elapsed_h:.2f}", fp, card, authorized=False, idle=idle, wakes_used=wakes_used)

    breach = scope_breach(card, obs)
    if breach:
        return _result("SCOPE-BREACH", f"outside-source:{breach}", fp, card, authorized=False, idle=idle, wakes_used=wakes_used)

    matched = stop_matched(card, obs, fp)
    if matched:
        return _result("DONE", matched, fp, card, authorized=False, idle=0, wakes_used=wakes_used + 1)

    last = card.get("last_fingerprint")
    if last is None or last != fp:
        return _result("DELTA", "fingerprint-changed" if last else "first-pulse", fp, card, authorized=True, idle=0, wakes_used=wakes_used + 1)

    # ECHO
    new_idle = idle + 1
    if new_idle >= budget["stall_limit"]:
        return _result("STALL", f"idle_streak:{new_idle}", fp, card, authorized=False, idle=new_idle, wakes_used=wakes_used + 1)
    return _result("ECHO", f"idle_streak:{new_idle}", fp, card, authorized=False, idle=new_idle, wakes_used=wakes_used + 1)


def _result(
    verdict: str,
    reason: str,
    fp: str,
    card: Dict[str, Any],
    authorized: bool,
    idle: int,
    wakes_used: int,
) -> Dict[str, Any]:
    return {
        "verdict": verdict,
        "reason": reason,
        "fingerprint": fp,
        "authorized": authorized,
        "allowed_verbs": list(card.get("allowed_verbs", [])) if authorized else [],
        "idle_streak": idle,
        "wakes_used": wakes_used,
        "mission": card.get("mission"),
        "next_charter_patch": {
            "last_fingerprint": fp,
            "idle_streak": idle,
            "wakes_used": wakes_used,
        },
    }


def apply_patch(card: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(card)
    patch = result.get("next_charter_patch") or {}
    out.update(patch)
    if "started_at" not in out or not out["started_at"]:
        out["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def append_log(path: str, result: Dict[str, Any], now: datetime) -> None:
    line = {
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": result["verdict"],
        "reason": result.get("reason"),
        "fingerprint": result.get("fingerprint"),
        "idle_streak": result.get("idle_streak"),
        "wakes_used": result.get("wakes_used"),
        "authorized": result.get("authorized"),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def demo() -> None:
    card = {
        "mission": "Babysit CI for pr-42 until green or budget ends",
        "sources": ["repo/ci/pr-42.log", "repo/status.json"],
        "allowed_verbs": ["read", "rerun-failed", "reply-bot"],
        "stop_when": [
            {"kind": "obs_contains", "needle": "status=merged"},
            {"kind": "metric_at_least", "key": "pass_rate", "threshold": 1.0},
        ],
        "wake_budget": {"max_wakes": 12, "max_hours": 6, "stall_limit": 3},
        "observation": {"recipe": "tail ci log + cat status.json"},
        "started_at": "2026-11-06T00:00:00Z",
        "wakes_used": 0,
        "idle_streak": 0,
        "last_fingerprint": None,
    }
    print("== lint ==")
    print(lint_charter(card) or "ok")
    obs1 = "pass_rate=0.5\nstatus=running\n# from repo/ci/pr-42.log"
    r1 = check_wake(card, obs1, now=_utc_now("2026-11-06T00:20:00Z"))
    print("== wake1 ==")
    print(json.dumps(r1, indent=2))
    card = apply_patch(card, r1)
    obs2 = obs1  # no change
    r2 = check_wake(card, obs2, now=_utc_now("2026-11-06T00:40:00Z"))
    print("== wake2 echo ==")
    print(json.dumps(r2, indent=2))
    card = apply_patch(card, r2)
    obs3 = "pass_rate=1.0\nstatus=merged\n# from repo/ci/pr-42.log"
    r3 = check_wake(card, obs3, now=_utc_now("2026-11-06T01:00:00Z"))
    print("== wake3 done ==")
    print(json.dumps(r3, indent=2))


def _read_obs(args: argparse.Namespace) -> str:
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    if args.obs is not None:
        return args.obs
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="looppulse")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_lint = sub.add_parser("lint")
    p_lint.add_argument("charter")

    p_fp = sub.add_parser("fingerprint")
    p_fp.add_argument("--file")
    p_fp.add_argument("text", nargs="?")

    p_check = sub.add_parser("check")
    p_check.add_argument("charter")
    p_check.add_argument("--obs")
    p_check.add_argument("--file")
    p_check.add_argument("--log")
    p_check.add_argument("--now")
    p_check.add_argument("--write-charter", help="write patched charter back to this path")

    p_rec = sub.add_parser("record")
    p_rec.add_argument("charter")
    p_rec.add_argument("--obs")
    p_rec.add_argument("--file")
    p_rec.add_argument("--log", required=True)
    p_rec.add_argument("--now")
    p_rec.add_argument("--write-charter")

    sub.add_parser("demo")

    args = p.parse_args(argv)

    if args.cmd == "demo":
        demo()
        return 0

    if args.cmd == "fingerprint":
        text = open(args.file, encoding="utf-8").read() if args.file else (args.text or "")
        print(fingerprint(text))
        return 0

    if args.cmd == "lint":
        card = load_json(args.charter)
        errs = lint_charter(card)
        if errs:
            print(json.dumps({"ok": False, "errors": errs}, indent=2))
            return 1
        print(json.dumps({"ok": True}, indent=2))
        return 0

    if args.cmd in ("check", "record"):
        card = load_json(args.charter)
        obs = _read_obs(args)
        now = _utc_now(args.now) if args.now else _utc_now()
        result = check_wake(card, obs, now=now)
        if args.log:
            append_log(args.log, result, now)
        if args.write_charter:
            patched = apply_patch(card, result)
            with open(args.write_charter, "w", encoding="utf-8") as f:
                json.dump(patched, f, indent=2, ensure_ascii=False)
                f.write("\n")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        # non-zero exit for stop/hold/breach/budget so shells can branch
        if result["verdict"] in ("HOLD", "STALL", "BUDGET-CUT", "SCOPE-BREACH"):
            return 2
        if result["verdict"] == "DONE":
            return 0
        if result["verdict"] == "ECHO":
            return 3
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
