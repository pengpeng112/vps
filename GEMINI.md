# F:\vps AI 启动入口（唯一权威规则在 AGENTS.md）
1. 开工先完整阅读根目录 `AGENTS.md`（本文件仅作重定向指针，不覆盖主规则）。
2. 开工先运行 `python scripts/session_guard.py status` 与 `git status --short`。
3. 按 `AGENTS.md` §1 任务等级（T0/T1/T2）读取 `docs/current/现势总览.md` 与 `docs/current/当前状态.md` 顶部 3 条，写操作前按标准 scope 申请锁，收尾释放锁。
4. 生产红线：管理主台=广州（8445）；DMIT 美西为分叉温备；禁手改设备正式 mihomo 配置；禁自动 `git commit/push/reset/stash/clean`。
5. 安全红线：严禁打印或入库密码、完整 DSN、token、密钥、订阅 URL 或 WS 路径。
6. 当前治理主线：`docs/plans/governance-20261119/00-执行总纲与事实基线.md`；目录导航见 `README.md` 与 `docs/INDEX.md`。
7. 新建与文书规范：AI 自建文件必须按 `AGENTS.md` §5 落位命名；若 `status` 提示 `[doc-hygiene]` 或发现用户新建未归位文书，主动提示并协助脱敏归档（不删原件）。
