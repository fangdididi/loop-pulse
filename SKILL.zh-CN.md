---
name: loop-pulse
description: 在周期性 Agent 循环的每次唤醒前，冻结或加载一份脉冲章程，并根据观察指纹把本次唤醒判定为 DELTA / ECHO / STALL / DONE / BUDGET-CUT / SCOPE-BREACH / HOLD，只在确有增量且预算未耗尽时允许行动。适用于照看 CI、盯实验指标、轮询固定来源列表，或任何必须在停滞/成功/耗尽时停下来的 /loop。
---

# loop-pulse

周期性 Agent 循环最常见的浪费是：按时醒来、什么都没变，却仍「好心」去改世界。**loop-pulse** 把每一次唤醒变成闸门：观察 → 指纹 → 判定 → 再行动。

## 何时使用

- 以 `/loop` 或定时唤醒的方式反复检查 CI、指标或固定来源列表
- 只想在**有增量**时做事：观察不变就禁止副作用
- 需要在**停滞、成功、唤醒/时长预算**上硬停
- 必须把循环关在允许的来源集合内

不要用于一次性任务、打扰用户的路由，或上线前的触发规则彩排——那些是别的技能。

## 核心思路

1. **脉冲章程**（只冻一次）：任务目标、允许来源、允许动词、停止谓词、唤醒预算、观察配方。
2. **每次唤醒**：跑配方，把文本交给 `looppulse.py check`，服从判定。
3. **落盘**：把 `next_charter_patch` 写回章程，并追加一行到 `pulses.jsonl`。

### 判定

| 判定 | 含义 | 能否行动 |
| --- | --- | --- |
| `DELTA` | 指纹变了（或首次脉冲） | 能 — 仅限 `allowed_verbs` |
| `ECHO` | 指纹相同，未达停滞上限 | 否 — 只汇报 |
| `STALL` | 空转次数达到 `stall_limit` | 否 — 停循环 |
| `DONE` | 命中某条 `stop_when` | 否 — 成功结束 |
| `BUDGET-CUT` | `max_wakes` 或 `max_hours` 耗尽 | 否 — 停 |
| `SCOPE-BREACH` | 观察文本点名了 `sources` 之外的路径 | 否 — 停/升级 |
| `HOLD` | 章程不合法或缺少观察 | 否 — 先修章程 |

## 步骤

1. 复制 `templates/charter.json` → `pulse-charter.json`。
2. 填好 `mission`、`sources`、`allowed_verbs`、`stop_when`、`wake_budget`、`observation.recipe`。
3. 校验：`python3 scripts/looppulse.py lint pulse-charter.json`
4. 每次定时唤醒时：
   - 按配方产出观察，写入 `obs.txt`（只能碰 `sources`）。
   - `python3 scripts/looppulse.py check pulse-charter.json --file obs.txt --log pulses.jsonl --write-charter pulse-charter.json`
   - 按 `verdict` 分支（退出码：`0` 为 DELTA/DONE，`2` 为停止类，`3` 为 ECHO）。
5. 若 `DELTA`：只做 `allowed_verbs` 里最小的一步。
6. 若 `ECHO`：写一行状态即可，不改代码、不重跑任务、不打扰人。
7. 若 `STALL` / `DONE` / `BUDGET-CUT` / `SCOPE-BREACH`：结束循环，脉冲日志即收据。

## 章程结构

见英文版示例；`stop_when` 支持：`fingerprint_equals`、`obs_contains`、`metric_at_least`、`status_done`。

## 例子

**CI 照看** — `examples/pr-ci-charter.json`：日志变化 → `DELTA`；完全相同 → `ECHO`；出现 `status=merged` → `DONE`。

**实验看门** — 来源=指标文件+状态文件；`accuracy>=0.9` 或连续三次相同脉冲 → `STALL`。

**调研守望** — 来源=冻结的 URL/文件列表；列表不变 → `ECHO` 直到停滞上限；循环中途扩来源会触发 `SCOPE-BREACH`。

## 提示

- 把数字写成 `key=value`，方便 `metric_at_least` 解析。
- `allowed_verbs` 越短越好；允许「重构」的循环最终都会去重构。
- 嘈杂 CI 用 `stall_limit` 2–4；慢实验可更高。
- `started_at` 设定后不要扩大 `sources`——新开一份章程。
- 本技能只管唤醒闸门，不管调度本身。

## 辅助命令

```bash
python3 scripts/looppulse.py demo
python3 scripts/looppulse.py lint examples/pr-ci-charter.json
python3 scripts/looppulse.py check examples/pr-ci-charter.json --file examples/sample-obs-running.txt
python3 -m unittest discover -s tests -v
```
