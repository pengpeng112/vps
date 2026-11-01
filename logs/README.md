# 生成日志

`node-probe/` 由 `scripts/node-site-probe.py` 写入：`latest.md` 是最近一轮，`history.csv` 是累积表，`runs/` 是每轮 JSON。目录在根 `.gitignore` 里。

这些文件是测量输出，不是配置，也不是设备事实的唯一来源。巡检脚本不切换节点。
