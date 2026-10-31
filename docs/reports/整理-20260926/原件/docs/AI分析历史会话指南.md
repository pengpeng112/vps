# 如何让其他 AI 工具分析 ZCode 历史会话 — 操作说明

> 目的：把 ZCode（本机 AI 编程工具）在 `F:\vps` 项目里的全部历史对话导出成普通 Markdown，交给任何其他 AI（网页版 ChatGPT/Kimi/DeepSeek/Gemini 等）分析。
> 生成日期：2026-09-19

---

## 一、记录都存在哪（了解即可）

| 位置 | 内容 | 说明 |
|------|------|------|
| `C:\Users\Administrator\.zcode\cli\db\db.sqlite` | **主库（1.4GB）**：全部项目的会话/消息/工具调用 | 表 `session`(项目目录+标题)、`message`(角色+时间)、`part`(正文/思考/工具调用) |
| `C:\Users\Administrator\.zcode\cli\rollout\model-io-sess_*.jsonl` | 近期会话的模型原始输入输出 | JSONL，每行一个请求/响应，含系统提示词，体积小 |
| `C:\Users\Administrator\.zcode\cli\exec\sess_*\*.log` | 每条命令的输出日志 | 排障用 |
| `F:\vps\` 下的 *.md 交付文档 | 会话产出的正式文档（交接/SOP/台账等） | **建议一并给 AI**，比原始会话更有结构 |

注意：db.sqlite 是整机所有项目的库（含医院等敏感项目），**不要整个文件外发**，按下面方法只导出本项目。

## 二、快速导出（推荐方式）

用现成脚本 `F:\vps\scripts\export_zcode_sessions.py`：

```bat
cd /d F:\vps\scripts

:: 1) 标准导出：F:\vps 全部会话 → 只含用户/AI 对话文本，自动脱敏
python export_zcode_sessions.py --redact

:: 2) 想让 AI 看到做过哪些操作（读文件/执行命令的摘要行）
python export_zcode_sessions.py --redact --tools

:: 3) 只要「路由器管理系统」主会话，并含 AI 思考过程
python export_zcode_sessions.py --redact --tools --reasoning --title 路由器管理系统

:: 4) 合并成单文件（有的 AI 一次只能传一个文件）
python export_zcode_sessions.py --redact --tools --out F:\vps\docs\会话导出\合并.md
```

产物默认在 `F:\vps\docs\会话导出\`，一个会话一个 `.md`，开头有会话标题/时间，正文按 `[user]` / `[assistant]` 分段。

当前 `F:\vps` 共有 5 个会话，主会话「开发路由器管理系统执行计划」最大（414KB / 667 条消息，2026-09-13 起）。文件名可能因终端显示乱码看着怪，内容本身是 UTF-8 正常的。

## 三、交给其他 AI 的建议姿势

1. **体积控制**：网页 AI 一般限 100KB~1MB。主会话文本版 300KB 可直接传；要更小就加 `--last 1` 或让 AI 只看部分。
2. **喂法**：
   - 支持传文件的（Kimi/ChatGPT/Gemini）：直接把导出的 .md 作为附件，提示词写「这是某 AI 编程助手的项目开发会话记录，请分析……」
   - 只能粘贴的：分段粘贴，每段贴完说"继续"。
   - 有 API/长上下文的（GLM/Kimi 长文本）：合并单文件一次喂。
3. **配合正式文档效果更好**：把 `F:\vps\路由器代理配置交接-20260917.md`、`登录信息与配置总表`、`刷机发货流程SOP`、`运维相关 md` 一起给，AI 能把「会话过程」和「最终结论」对上。

## 四、安全须知（重要）

- 原始会话里包含**全部真实密码/密钥**（VPS、管理台、TOTP、节点凭证、服务器口令）。**外发必须加 `--redact`**，它会把已知敏感值替换成 `***XXX***` 占位符。
- `--redact` 只覆盖已知值清单（脚本头部 `REDACT_PAIRS`），新出现的密钥请自行补进清单再导。
- 脱敏清单当前覆盖：管理台 admin 密码、TOTP 密钥、路由器/服务器口令、VLESS uuid/pbk/sid、agent HMAC 密钥、`password=/secret=` 行。
- db.sqlite 永远不要整体外发或上传网盘。

## 五、常见问题

- **导出为空**：检查 `--project` 路径写法（如 `F:\vps`，不要带引号尾空格）。
- **想导其他项目**：`python export_zcode_sessions.py --project "F:\python\数据资产" --redact`，或 `--all` 导全部（文件很多）。
- **工具输出全文**：默认工具只留一行摘要；加 `--full-tools` 含输出正文（文件会大很多）。
