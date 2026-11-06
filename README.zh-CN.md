# loop-pulse

[English](./README.md)

**周期性 Agent 循环的唤醒闸门。** 一次性冻结脉冲章程；每次定时唤醒时对观察文本取指纹，判定 `DELTA / ECHO / STALL / DONE / BUDGET-CUT / SCOPE-BREACH / HOLD`，只在确有增量且预算未耗尽时允许行动。

## 功能

- 脉冲章程：任务目标、允许来源、允许动词、停止条件、唤醒/时长/停滞预算
- 稳定的观察指纹（空白归一化）
- 清晰的授权 / 只汇报 / 停止语义
- JSONL 脉冲日志作为可审计收据
- 仅标准库辅助脚本：`scripts/looppulse.py`
- 17 个单元测试

## 安装

把本目录拷进 Agent 的 skills 目录，或：

```bash
git clone https://github.com/fangdididi/loop-pulse.git
```

适用于会加载 `SKILL.md` 的 Claude Code / Cursor / Codex 类 Agent。

## 用法

```bash
python3 scripts/looppulse.py lint examples/pr-ci-charter.json
python3 scripts/looppulse.py check examples/pr-ci-charter.json \
  --file examples/sample-obs-running.txt \
  --log pulses.jsonl \
  --write-charter /tmp/charter-out.json
python3 scripts/looppulse.py demo
python3 -m unittest discover -s tests -v
```

工作流：填章程 → 每次唤醒跑观察并 `check` → 服从 `verdict`；只有 `DELTA` 才授权 `allowed_verbs`。

## 示例

- `examples/pr-ci-charter.json` — 照看 CI 直到合并
- `examples/sample-obs-*.txt` — 示例观察文本

## 文件结构

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

## 许可

MIT © fangdididi
