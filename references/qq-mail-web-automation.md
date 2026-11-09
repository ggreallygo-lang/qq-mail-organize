# QQ 邮箱网页版自动化参考（实战验证）

本文件记录 mail.qq.com 网页版批量整理的关键技术事实。所有结论均经过实弹验证，
执行整理操作前必读，避免重复踩坑。

## 0. 前置条件

- **QQ 邮箱 Connector**（qq-mail skill）：仅有 `mail:read` / `mail:send` 权限，
  **没有** 标已读 / 打标签 / 移动邮件的工具 → 所有写操作必须走浏览器。
- **agent-browser CLI**（vercel-labs/agent-browser）：持久 Chromium daemon，
  负责登录与批量操作。若 CLI 不在 PATH 中，按你的实际安装位置补充，例如：
  ```
  export PATH="$HOME/.workbuddy/binaries/node/versions/<node版本>/bin:$HOME/.workbuddy/binaries/node/workspace/node_modules/.bin:$PATH"
  ```
  （批处理脚本也支持用环境变量 `QQMAIL_NODE_BIN` / `QQMAIL_WS_BIN` 指定这两个目录。）
- **登录**：`mail.qq.com` 会话超时后跳 `from=session_timeout` 登录页。
  切到「QQ邮箱登录」二维码 tab → 若 Chromium 窗口不可见（无头），从 DOM 提取
  `img[src^="data:image/png"]` 存成 PNG 交给用户扫码（约 1-2 分钟过期，过期需 reload 重取）。
  扫码成功后回 `mail.qq.com` 即可。

## 1. 点击模型（最重要，决定了成败）

QQ 邮箱是 React 应用，控件分两类：

| 控件 | 必须的点击方式 |
|---|---|
| 发件人过滤建议项 `.include-item` | **真实鼠标点击**（`agent-browser click`），合成 `el.click()` 无效 |
| 全选框 `.mail-search-select-all`（搜索视图）/ `.xmail-ui-checkbox.select-all`（文件夹视图） | **真实鼠标点击** |
| 工具栏按钮（标记为/移动到/更多） | **真实点击**，选择器不稳定时用 `snapshot -i` 提取 `ref=eN` 再 `click ref=eN`（⚠️ ref 每次 snapshot 重新编号，不可缓存） |
| 下拉菜单项 `.ui-panel-item-content` | **合成点击有效**（`el.click()` via eval） |
| 侧栏文件夹 `.sidebar-menu-text` | 树展开时合成点击可导航；**树收起时子文件夹不渲染**，先真实点击展开 |

## 2. 关键选择器速查

```
顶部搜索框入口      .mail-lazy-search-wrap        （点击聚焦）
高级过滤输入框      input.filter-input
过滤建议项          .include-item                  （文本恰为「发件人包含」等，不含查询词）
过滤面板标签        .item-name                     （收件箱/按日期/已读/未读，虚拟滚动，可能不在 DOM）
搜索结果行          .mail-list-search-item         （行内 .mail-unread = 未读标记）
搜索视图全选        .mail-search-select-all
文件夹视图全选      .xmail-ui-checkbox.select-all
行内复选框          .xmail-ui-checkbox.mail-checkbox
勾选状态            外层 div 无变化，看内部 .ui-checkbox-icon-checked 是否存在
工具栏按钮文本      .xmail-ui-btn > .ui-btn-text
菜单项              .ui-panel-item-content         （标记为菜单 / 移动到菜单通用）
移动到文件夹列表容器 .folder-list
侧栏条目            .sidebar-menu-text
```

## 3. 搜索过滤流程（按发件人批量定位）

1. `click .mail-lazy-search-wrap` → 等 0.6s
2. 清空 `input.filter-input`：用 native value setter + `input` 事件（直接改 `.value` 无效）：
   ```js
   const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
   s.call(i,''); i.dispatchEvent(new Event('input',{bubbles:true}));
   ```
3. `agent-browser type "input.filter-input" "<sender>"` → 等 1.2s（建议面板出现）
4. **真实点击** `.include-item`（多个时先按文本过滤出「发件人包含」那个）→ 等 2s
5. 验证 chip：页面出现 `发件人:包含"<sender>"`；结果头部 `共 N 封`（数字可能带千分位逗号，正则用 `[\d,]+`）

⚠️ 若只 eval 合成点击 `.include-item`，过滤器**不会生效**——全选将选中全部邮件而非该发件人的 N 封。这是历史上最大的事故来源，务必断言。

## 4. 安全门禁（批量写操作前必须全部通过）

1. **chip 断言**：`发件人:包含"<sender>"` 必须出现在页面上。
2. **未读收窄**：搜索视图覆盖全部历史邮件（含已读）。优先点过滤面板「未读」分面
   （仅在结果同时含已读+未读时出现）；无论如何都要做第 3 条。
3. **行级校验**：`.mail-list-search-item` 行内必须全部带 `.mail-unread`。
   任一渲染行不带未读标记 → 中止。
4. **计数断言**：点全选后「已选中全部 N 封」的 N 必须等于视图数；不等 → 立即再点一次全选撤销并中止。
5. **动作后验证**：move 后视图数应归 0（注意有缓存，等 1.5s）。

## 5. 批量动作

- **标记为**菜单：已读邮件 / 未读邮件 / 星标邮件 / 取消星标 / 广告邮件 / 已有标签... / 清空标签 / 新建标签
- **移动到**菜单：`.folder-list` 内的 `.ui-panel-item-content`，文本即文件夹名（合成点击有效）
- **新建标签**：标记为 → 新建标签 → 输入框 `input[placeholder="标签名称"]`（用
  `agent-browser type` 输入）→ Enter；创建即应用到当前选中邮件。首个标签创建后
  会出现在菜单里，后续直接点击即可。
- **文件夹视图**：工具栏有直接「全部已读」按钮（无需先选中）。**单次有处理上限
  （约 1800 封）**，须循环点击直到侧栏徽标归零。
- 动作完成后按 Escape 关闭菜单，避免状态残留。

## 6. 计数与状态读取

- 侧栏徽标 = `.sidebar-menu-content` 内 `名称(\d+)`，**有缓存延迟**：批量移动后
  可能不刷新，`agent-browser reload` 硬刷新后才准。
- 文件夹真实总数：进入文件夹后读 `共 [\d,]+ 封`。
- 「我的文件夹」树收起时子文件夹不渲染（snapshot/eval 都看不到），先真实点击展开。
- ⚠️ 邮件可能因历史规则散落在子文件夹且为未读——搜索范围是全文件夹的，
  处理「广告/订阅类」发件人时会把这些一并正确处理；收件箱徽标不降不代表失败。

## 7. 运维细节

- eval 返回的 JSON 字符串在 CLI 输出里带引号转义（`"[{\"a\":1}]"`），
  解析前先 `strip('"')` 再处理 `&quot;`。
- 长脚本跑批用 `run_in_background`；Python 打印会缓冲，加 `-u` 或用 tee 落盘后 tail。
- 一次完整跑批（数十发件人 / 数千封规模）约 40-60 分钟；单发件人约 20-40s。
- 会话超时会发生在任何时刻——若操作后页面无响应，先检查 `location.href` 是否跳登录页。
- 结束后执行 `agent-browser close` 清理 daemon。