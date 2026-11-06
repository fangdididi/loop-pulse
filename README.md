# loop-pulse

[中文版](./README.zh-CN.md)

**Wake gate for recurring agent loops.** Freeze a pulse charter once; on every scheduled wake, fingerprint the observation and classify `DELTA / ECHO / STALL / DONE / BUDGET-CUT / SCOPE-BREACH / HOLD` so the agent only acts on real change inside budget.

## Features

- Pulse charter: mission, allow-listed sources, allowed verbs, stop predicates, wake/hour/stall budget
- Stable observation fingerprints (whitespace-normalized)
- Verdict gate with clear authorize / report-only / stop semantics
- JSONL pulse log as an auditable receipt
- Stdlib-only helper: `scripts/looppulse.py`
- 17 unit tests

## Install

Copy this folder into your agent's skills directory, or clone:

```bash
git clone https://github.com/fangdididi/loop-pulse.git
```

Works with Claude Code / Cursor / Codex-style agents that load `SKILL.md`.

## Usage

```bash
# validate charter
python3 scripts/looppulse.py lint examples/pr-ci-charter.json

# classify a wake
python3 scripts/looppulse.py check examples/pr-ci-charter.json \
  --file examples/sample-obs-running.txt \
  --log pulses.jsonl \
  --write-charter /tmp/charter-out.json

# demo
python3 scripts/looppulse.py demo

# tests
python3 -m unittest discover -s tests -v
```

Agent workflow:

1. Fill `templates/charter.json`.
2. On each wake, run the observation recipe → `check`.
3. Obey `verdict`. Only `DELTA` authorizes `allowed_verbs`.

## Examples

- `examples/pr-ci-charter.json` — CI babysit until merged
- `examples/sample-obs-running.txt` / `sample-obs-merged.txt` — sample observations

## File structure

```
loop-pulse/
  SKILL.md / SKILL.zh-CN.md
  README.md / README.zh-CN.md
  scripts/looppulse.py
  tests/test_looppulse.py
  templates/charter.json
  examples/
  LICENSE
```

## License

MIT © fangdididi
