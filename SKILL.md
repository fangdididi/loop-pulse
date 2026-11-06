---
name: loop-pulse
description: Before each wake of a recurring agent loop, freeze or load a pulse charter and classify the wake (DELTA / ECHO / STALL / DONE / BUDGET-CUT / SCOPE-BREACH / HOLD) from an observation fingerprint so the agent only acts when there is a real delta and budget left. Use when babysitting CI, watching an experiment, polling a research source list, or any scheduled /loop that must stop on stall, success, or spend.
---

# loop-pulse

Recurring agent loops waste tokens when they wake up, see nothing new, and still "helpfully" poke the world. **loop-pulse** turns every wake into a gated pulse: observe → fingerprint → verdict → then (and only then) act.

## When to use

- A `/loop`-style or cron-woken agent that re-checks CI, metrics, or a fixed source list
- You want **delta-only** work: no side effects when the observation is unchanged
- You need a hard stop on **stall**, **success**, or **wake/hour budget**
- You must keep the loop inside an allow-listed set of sources

Do **not** use this for one-shot tasks, for user-interruption routing, or for rehearsing trigger rules before deploy — those are different jobs.

## Core idea

1. **Pulse charter** (frozen once): mission, allowed sources, allowed verbs, stop predicates, wake budget, observation recipe.
2. **Each wake**: run the recipe, feed the text to `looppulse.py check`, obey the verdict.
3. **Persist**: write `next_charter_patch` back onto the charter and append a line to `pulses.jsonl`.

### Verdicts

| Verdict | Meaning | Agent may act? |
| --- | --- | --- |
| `DELTA` | Fingerprint changed (or first pulse) | Yes — only `allowed_verbs` |
| `ECHO` | Same fingerprint; under stall limit | No — report-only |
| `STALL` | Idle streak hit `stall_limit` | No — stop the loop |
| `DONE` | A `stop_when` predicate matched | No — stop, success |
| `BUDGET-CUT` | `max_wakes` or `max_hours` exhausted | No — stop |
| `SCOPE-BREACH` | Observation names a path outside `sources` | No — stop / escalate |
| `HOLD` | Charter invalid or observation missing | No — fix charter first |

## Steps

1. Copy `templates/charter.json` → `pulse-charter.json`.
2. Fill `mission`, `sources`, `allowed_verbs`, `stop_when`, `wake_budget`, `observation.recipe`.
3. Lint: `python3 scripts/looppulse.py lint pulse-charter.json`
4. On each scheduled wake:
   - Run the observation recipe; save stdout to `obs.txt` (must only touch `sources`).
   - `python3 scripts/looppulse.py check pulse-charter.json --file obs.txt --log pulses.jsonl --write-charter pulse-charter.json`
   - Branch on `verdict` (exit code: `0` DELTA/DONE, `2` stop-class, `3` ECHO).
5. If `DELTA`: perform the smallest action from `allowed_verbs`, then re-observe only if the action itself changes the watched surface.
6. If `ECHO`: write a one-line status; do not edit code, re-run jobs, or ping humans.
7. If `STALL` / `DONE` / `BUDGET-CUT` / `SCOPE-BREACH`: end the loop and leave the pulse log as the receipt.

## Charter shape

```json
{
  "mission": "Babysit CI for pr-42 until merged or budget ends",
  "sources": ["repo/ci/pr-42.log", "repo/status.json"],
  "allowed_verbs": ["read", "rerun-failed", "reply-bot"],
  "stop_when": [
    {"kind": "obs_contains", "needle": "status=merged"},
    {"kind": "metric_at_least", "key": "pass_rate", "threshold": 1.0}
  ],
  "wake_budget": {"max_wakes": 12, "max_hours": 6, "stall_limit": 3},
  "observation": {"recipe": "tail ci log + cat status.json"},
  "started_at": "2026-11-06T00:00:00Z",
  "wakes_used": 0,
  "idle_streak": 0,
  "last_fingerprint": null
}
```

`stop_when` kinds: `fingerprint_equals`, `obs_contains`, `metric_at_least`, `status_done`.

## Examples

**CI babysit** — see `examples/pr-ci-charter.json`. First wake with a changing log → `DELTA`. Identical log next wake → `ECHO`. `status=merged` → `DONE`.

**Experiment watchdog** — sources = metric file + status file; stop on `accuracy>=0.9` or three identical pulses → `STALL`.

**Research watch** — sources = a frozen URL/file list; observation = "new titles since last pulse"; identical list → `ECHO` until `stall_limit`, never expand the source list mid-loop (`SCOPE-BREACH` if you try).

## Tips

- Put numbers in `key=value` form so `metric_at_least` can see them.
- Keep `allowed_verbs` tiny. A loop that may "refactor" will.
- Set `stall_limit` to 2–4 for noisy CI; higher for slow experiments.
- Never widen `sources` after `started_at` is set — start a new charter instead.
- Pair with your scheduler (`/loop`, cron, queue wake). This skill gates the wake; it does not schedule it.

## Helper

```bash
python3 scripts/looppulse.py demo
python3 scripts/looppulse.py lint examples/pr-ci-charter.json
python3 scripts/looppulse.py check examples/pr-ci-charter.json --file examples/sample-obs-running.txt
python3 -m unittest discover -s tests -v
```
