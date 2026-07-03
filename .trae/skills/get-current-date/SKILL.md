---
name: "get-current-date"
description: "获取本机系统的真实当前日期时间，作为一切时间判断的权威基准。凡涉及'今天/现在/最新/是否过期/日期先后/事件是否已发生'等时间判断时，必须先调用此技能校准，禁止使用环境注入的 Today's date。Invoke whenever the task involves the current date/time, recency checks, deadline/expiry judgment, or comparing whether an event has happened yet."
---

# 获取系统真实当前日期时间

## 目的

环境注入的 `Today's date` 字段可能滞后或错误，直接依赖它会导致时间判断出错
（例如把"已发生的事件"误判为"未来的预期"）。本技能从**本机系统时钟**读取真实
日期时间，作为当前时间的唯一权威基准。

## 强制使用规则

**凡是涉及以下场景，必须先调用本技能获取真实日期，再进行判断，禁止直接使用环境里的 `Today's date`：**

1. 判断"今天/现在"是几号、星期几
2. 判断某新闻/公告/事件是否"已经发生"还是"未来的预期"
3. 判断某数据/文件/信息是否"过期""最新"
4. 比较两个日期的先后、计算时间间隔
5. 需要生成含当前日期的文件名、时间窗口、报告落款等

## 使用方法

### 方法一：Python（推荐，最稳定）

```bash
python .trae/skills/get-current-date/get_current_date.py
```

输出示例：

```
========================================
本机系统当前日期时间（真实基准）
========================================
日期        : 2026-07-01 (星期三 / Wednesday)
日期时间    : 2026-07-01 21:40:43
紧凑日期    : 20260701
时区        : +0800
ISO8601     : 2026-07-01T21:40:43+08:00
UTC         : 2026-07-01 13:40:43 UTC
========================================
```

### 方法二：一行 Python 命令（快速）

```bash
python -c "from datetime import datetime; print(datetime.now().strftime('%Y-%m-%d %H:%M:%S %A'))"
```

### 方法三：PowerShell（Windows 备用）

```powershell
Get-Date -Format "yyyy-MM-dd HH:mm:ss dddd"
```

## 注意事项

1. **优先级**：本技能读取的系统时间 > 环境注入的 `Today's date`。两者冲突时，
   **一律以本技能结果为准**，并可提示用户环境日期可能有误。
2. **时区**：脚本输出本地时区时间（含时区偏移）与 UTC，跨时区判断时使用带时区的 ISO8601。
3. **调用时机**：在一次任务中若已获取过真实日期，可复用，无需重复调用；但跨任务或
   长对话中涉及新的时间判断时应重新校准。
