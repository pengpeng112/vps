# 跨 AI 协作入口

适用于 Codex、Claude Code、Gemini、Cursor、Windsurf、Cline、Copilot、ZCode（GLM/Kimi/Grok）等所有工具与模型。**模型名称不决定事实或授权**；唯一权威协作规则在根目录 [AGENTS.md](../AGENTS.md)。修订：2026-11-21。

## 1. 启动与防冲突（所有工具统一）

1. 在 `F:\vps` 工作，先完整阅读根目录 `AGENTS.md`。
2. 开工第一步执行：
   ```bash
   python scripts/session_guard.py status
   git status --short
   ```
3. 按 `AGENTS.md` §1 任务等级（**T0 纯本地只读 / T1 本地修改 / T2 现网诊断或变更**）读取对应上下文：
   - 稳态架构、设备表、未决项与 14 条禁令 → [docs/current/现势总览.md](current/现势总览.md)
   - 近 30 天运行流水 → [docs/current/当前状态.md](current/当前状态.md) 顶部 3 条
   - 全仓资料地图 → [docs/INDEX.md](INDEX.md)
4. 写操作前按标准 `scope` 申请锁（`gz-prod`、`device:<id>`、`server:<name>`、`code:<module>`、`docs`），严禁抢占或代解他人锁，收尾务必解锁。

## 2. 可复制的通用启动提示词（用于不支持自动加载的工具）

> 你在 `F:\vps` 工作。先阅读 `AGENTS.md`，再运行 `python scripts/session_guard.py status` 和 `git status --short`。按 `AGENTS.md` §1 任务等级按需读取 `docs/current/现势总览.md` 与 `docs/current/当前状态.md` 顶部 3 条，不要整库遍历 `node_modules`/固件/备份/凭证。用户本轮要求是：**〔在此填写具体任务与授权范围〕**。请严格遵守 `AGENTS.md` 中的三色授权边界、生产红线、3 次失败熔断与脱敏规则；保留其他会话的未提交改动；新建文件必须按 `AGENTS.md` §5 落位命名，若发现用户新建未归位文书请按规范提示或协助归档；交付时按 `AGENTS.md` §6 输出可核验四要素，有变更则完成收尾闭环并释放本人锁。

## 3. 各工具自动入口映射

| 工具 | 项目自动入口 | 维护方式 |
|---|---|---|
| Codex / 支持 AGENTS 规范工具 | 根目录 `AGENTS.md` | **唯一权威真源** |
| Claude Code / Gemini / Cursor / Windsurf / Cline / GitHub Copilot | `CLAUDE.md`、`GEMINI.md`、`.cursorrules`、`.windsurfrules`、`.clinerules`、`.github/copilot-instructions.md` | **6 份完全一致的极简指针**，只重定向 `AGENTS.md` 与 `session_guard.py`，不另行夹带差异化规则 |
| ZCode / 网页模型 | 提供 `F:\vps` 目录并附上方启动提示词 | 网页模型严禁粘贴未脱敏材料 |

## 4. 交接与多方评审

- 交接格式细则见 [docs/plans/AI协作交接规范.md](plans/AI协作交接规范.md)。
- 多模型交叉评审/变更核查请仅在用户明确要求时调用 `multi-review` / `multi-verify` 技能（脏工作区必须按本轮白名单文件生成 diff，避免混入他人未提交改动）。
