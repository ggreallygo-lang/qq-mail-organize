# qq-mail-organize · QQ 邮箱安全整理工具包

让 AI 助手（或你自己）批量整理 QQ 邮箱（mail.qq.com），**且不误删**。
来自一次真实的大规模邮箱整理实战——包括一次误删事故后的彻底重写。所有个人数据已脱敏。

## 项目简介

包含两套互补的工具：

| 组件 | 适用场景 | 原理 |
|---|---|---|
| **Agent Skill**（`SKILL.md` + `references/` + `scripts/`） | 分类、打标签、归档、清未读 | 浏览器自动化（agent-browser），五阶段门禁工作流 |
| **`qqmail.py`** | 按规则批量清理、归位、可恢复删除 | IMAP 直连，配置驱动，scan→确认→move→restore |

一句话原则：**先用 `scan` 看清，再用 `move` 动。绝不用计数差判断结果，绝不碰 QQ 回收站。**

## 安全五铁律（违反任何一条都可能重演误删事故）

1. **绝不用「文件夹计数差」判断移动结果。** `SELECT` 返回值在反复调用时会与 `MOVE` 的残留响应错位，得出荒谬数字。正确做法：移动后用 `UID SEARCH UID <本批uid集>` 精确核对这批 UID 是否还在源文件夹。
2. **全程只 SELECT 一次。** 同一连接上反复 `SELECT` 同一邮箱是响应错位的高发场景。`safe_move()` 只在开头 SELECT 一次，之后不再重选。
3. **批量固定 20。** 不是性能问题，是可定位性问题：20 个一批出异常，一眼能看出是哪批。超长 UID 列表还会被 QQ 静默截断。
4. **目标只写自建「待删除」，绝不写 QQ 回收站。** QQ 的 `Deleted Messages` 有容量淘汰机制：实测达到一定容量后，较早的一段 UID 会被静默清除，推进去的东西不保证还在。自建文件夹不受此限。
5. **`scan` 永不移动，且必须落盘完整 UID 清单。** 只打印计数、不存 UID 清单的 dry-run 等于没做——事后连"到底动了哪些"都无法追溯。

## 标准流程（顺序不可跳）

```bash
python qqmail.py rules                        # 看有哪些可用规则
python qqmail.py state                        # 看所有文件夹计数
python qqmail.py scan  <文件夹> --rule <规则>  # 只读扫描，落盘 UID 清单
python qqmail.py move  <文件夹> --rule <规则>  # 移到「待删除」（可恢复）
python qqmail.py restore <文件夹>              # 从「待删除」搬回去
python qqmail.py relocate <源> <目标> --rule <归位规则> [--apply]  # 只搬家不删
python qqmail.py purge [--apply]               # 永久清空「待删除」（默认仅预览）
```

| 步骤 | 命令 | 作用 | 是否改动邮箱 |
|---|---|---|---|
| 1 | `rules` | 确认规则名 | 否 |
| 2 | `state` | 记录操作前计数 | 否 |
| 3 | `scan 文件夹 --rule X` | 落盘待删 UID 清单 + 抽样预览 | **否（只读）** |
| 4 | 人工确认清单合理 | 看 `last_plan.json` | 否 |
| 5 | `move 文件夹 --rule X` | 移入「待删除」 | 是（可恢复） |
| 6 | 去网页端抽查 | 确认无误 | 否 |
| 7 | 确认后才 `purge --apply` | 永久删除 | 是（不可恢复） |

**第 5 步有内置闸门**：如果没跑过 `scan`，或 `scan` 与 `move` 之间邮件集合变了（新增/减少），脚本直接拒绝执行。单次移动还有 `max_moves_per_run` 上限。

## 安装三步

1. **获取代码**：把本目录复制到本地任意工作目录（发布后可 `git clone`）。
2. **配置账号**：
   ```bash
   cp config.example.json config.json
   ```
   把 `account.user` 改成你的 QQ 邮箱；再按[《如何获取IMAP授权码》](如何获取IMAP授权码.md)生成授权码，存为同目录下的 `imap_auth.txt`（一行 16 位）。
3. **自检**：`python qqmail.py state` 能列出文件夹计数即安装成功。

仅使用 Python 标准库，无第三方依赖（Python 3.8+）。

**安装 Agent Skill**（可选）：把 `SKILL.md`、`references/`、`scripts/` 复制到你的 AI 助手 skills 目录，例如 `~/.workbuddy/skills/qq-mail-organize/`。浏览器自动化脚本需要 [agent-browser](https://github.com/vercel-labs/agent-browser) CLI 与已登录的 Chromium 会话；工作目录用环境变量 `QQMAIL_WORKDIR` 配置（默认当前目录）。

## 保护机制

- **受保护邮件永不删除**：`config.json` → `protected.regex`（密码/重置/收据/登录/安全提醒/验证/续期等），命中即排除，优先级高于一切可删规则。
- **广告标记优先判营销**：命中 `ad_marker.regex`（`<广告>`、`(AD)`、特卖、促销、限时…）的，即使含"账户"字样也照判营销。
- **可删规则示例**：`daily`（银行每日播报）、`promo`（营销促销）、`verify_code`（过期验证码）、`recruit_ad`（招聘平台 EDM）、`nag`（重复催办通知）。按自己的邮件构成增删。

## 推荐文件夹结构（示例）

| 文件夹 | 说明 |
|---|---|
| `真人邮件` | 真人往来，**默认不动** |
| `工资单` | 工资单类邮件 |
| `账单财务` | 银行/信用卡正式账单、电子发票、收据与订单 |
| `银行日报` | 银行每日额度播报类 |
| `招聘求职` | 招聘相关（平台广告可清，真人往来归入真人邮件） |
| `订阅资讯` | 订阅内容、资讯通知 |
| `通知与安全` | 账号安全通知（重置、设备通知等） |
| `待删除` | 暂存区；**新删除操作先落这里** |
| 收件箱 `INBOX` | 新邮件仍会进这里 |

### 读数两条铁律（比移动本身更容易出错）

1. **不要混用 `SELECT` 和 `STATUS`**。两者响应会串，读出的数字可能是别的文件夹的。`qqmail.py state` 只用 `SELECT` + `UID SEARCH UNSEEN`。
2. **QQ 服务端异步落定，操作后立刻读数是中间态**。大批量删除后立刻读可能显示还剩很多，约 30 秒后才归零。**任何数量核对，与操作间隔 ≥30 秒，或用新连接复核。不要用一次读数下结论。**

## 踩坑表

| 坑 | 现象 | 解法 |
|---|---|---|
| imaplib 响应错位 | 移动数与请求数不符 | 只 SELECT 一次 + UID SEARCH 核对 |
| 中文文件夹名 | 直接传中文报错 | RFC3501 modified UTF-7 编码 |
| 带空格系统文件夹 | `SELECT "Sent Messages"` 报 CLIENTBUG | 必须加引号 |
| MOVE 前未选对邮箱 | 报 `[NONEXISTENT] Mails not exist` | 移动前确保 SELECT 的是源文件夹 |
| 邮件头折行 | 长主题被截断 | 用 `email.message_from_bytes` 标准解析，别 `split("\n")` |
| 超长 UID 列表 | QQ 静默截断 | 批量 20 |
| QQ 回收站淘汰 | 推进去的邮件消失 | 用自建「待删除」 |
| UID EXPUNGE 不生效 | QQ 声明支持 UIDPLUS 但实测无效 | 专用暂存夹整夹 `EXPUNGE`（见 `cmd_purge`） |

网页自动化侧的坑（点击模型、选择器、门禁断言）见 `references/qq-mail-web-automation.md`。

## 发生意外时的止血顺序

1. **立刻停止所有脚本**，不要重试、不要重跑。
2. `python qqmail.py state` 记录当前计数。
3. 对比 `run_log.jsonl` 与 `last_plan.json`，确认动了哪些 UID。
4. 若邮件在「待删除」：`python qqmail.py restore <原文件夹>` 搬回。
5. 若已进 QQ 回收站：去网页端 `mail.qq.com` → 已删除 查看；再用 **设置 → 账号 → 邮件恢复**（30 天内，有次数限制）。
6. 写事故复盘记录，再决定下一步。

## 环境与凭据

- 授权码文件：`imap_auth.txt`（一行 16 位，已在 `.gitignore`）
- 服务器：`imap.qq.com:993`（SSL）；能力：`MOVE` / `UIDPLUS` / `XLIST` / `IDLE`
- **凭据不进版本库、不写进任何报告或日志。** `config.json`、`*.log`、`*.jsonl`、UID 清单等运行时产物也全部被 `.gitignore` 排除。

## 目录结构

```
SKILL.md                        Agent Skill：五阶段门禁工作流 + 红线
references/qq-mail-web-automation.md   网页版自动化技术事实（选择器/点击模型/门禁）
scripts/batch_process_mail.py   浏览器批处理主引擎（SENDER 字典为示例，运行前重写）
scripts/finalize_mail.py        收尾引擎（重试中止项/统一打标/标已读）
qqmail.py                       IMAP 安全清理 CLI（唯一推荐入口）
config.example.json             配置模板（复制为 config.json 使用）
如何获取IMAP授权码.md            授权码获取教程
```

## License

[MIT](LICENSE)