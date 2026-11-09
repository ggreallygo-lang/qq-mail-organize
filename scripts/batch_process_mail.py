# -*- coding: utf-8 -*-
"""
批量处理 QQ 邮箱未读邮件（浏览器自动化驱动）
=====================================================================
已验证的关键机制（实战实测）：
  1. eval 合成 click 对 React 面板无效 → 必须用 agent-browser 真实点击
     （include-item、select-all、工具栏按钮）
  2. 过滤生效判据：页面出现 chip 文本 发件人:包含“SENDER” + 结果数 共 N 封
  3. 搜索覆盖全部历史邮件（含已读）→ 必须点「未读」标签收窄
  4. 「共 N 封」数字带千分位逗号，正则须兼容
  5. 标记已读与移动解耦：最后对目标文件夹做一次全选标已读（内容全为本批次）

动作映射：
  score<=3 忽略   -> 移到 广告通知（最后统一标已读）
  4<=score<=6 扫读 -> 移到 订阅周报 + 标 扫读（最后统一标已读）
  score==7  建议读 -> 标 建议读，留收件箱、保持未读
  score>=8  重点读 -> 标 重点读，留收件箱、保持未读

配置（全部走环境变量，无硬编码个人路径）：
  QQMAIL_WORKDIR   工作目录（输入数据与日志落盘处），默认当前目录
  QQMAIL_NODE_BIN  node 可执行文件所在目录（可选，agent-browser 已在 PATH 则留空）
  QQMAIL_WS_BIN    agent-browser 所在 node_modules/.bin 目录（可选）
  QQMAIL_AB        agent-browser 命令名/路径，默认 "agent-browser"

输入：WORKDIR/unread_raw.json（由只读枚举步骤生成），
格式为列表，每项至少含 [发件人, _, 主题, _, 摘要]（索引 0/2/4）。
"""
import subprocess, json, sys, time, os, re

AB = os.environ.get("QQMAIL_AB", "agent-browser")
BASE_ENV = os.environ.copy()
NODE_BIN = os.environ.get("QQMAIL_NODE_BIN", "")
WS_BIN = os.environ.get("QQMAIL_WS_BIN", "")
if NODE_BIN or WS_BIN:
    BASE_ENV["PATH"] = os.pathsep.join(
        p for p in (WS_BIN, NODE_BIN, BASE_ENV.get("PATH", "")) if p)

WORKDIR = os.environ.get("QQMAIL_WORKDIR", os.getcwd())

# ---------- 分级字典（仅为示例条目，每次运行前按当前方案重写） ----------
# 格式：发件人 -> (评分 1-10, 备注)
# 评分口径：<=3 忽略、4-6 扫读、7 建议读、>=8 重点读
# 特殊值 "ECOM"：电商类特判，按主题关键词决定 keep/move（见 action_for）
SENDER = {
    "example-bank@example.com": (5, "示例银行"),
    "newsletter@example.com": (4, "示例订阅"),
    "noreply@ads.example.com": (2, "示例广告"),
    "important-contact@example.com": (9, "示例重点联系人"),
    "orders@example-shop.com": ("ECOM", "示例电商"),
}

def action_for(sender, subject="", summary=""):
    e = sender.lower()
    # 大小写无关查找（字典键混合大小写）
    v = next((val for k, val in SENDER.items() if k.lower() == e), None)
    if v is not None:
        if v[0] == "ECOM":
            blob = (subject + " " + summary).lower()
            if any(k in blob for k in ["订单","物流","配送","已下单","待付款","账单","还款","行程","取票","购票","invoice","order","shipping"]):
                return ("keep", "建议读")
            return ("move", "广告通知")
        s = v[0]
    else:
        return ("skip", None)
    if s <= 3:  return ("move", "广告通知")
    if s <= 6:  return ("move", "订阅周报")
    if s == 7: return ("keep", "建议读")
    return ("keep", "重点读")

# ---------- 浏览器驱动 ----------
def ab(args, timeout=40):
    try:
        r = subprocess.run([AB]+args, capture_output=True, text=True, env=BASE_ENV, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return "TIMEOUT"

def evaljs(js):
    return ab(["eval", js])

def wait(sec):
    time.sleep(sec)

def click_ref_text(text, nth=0):
    """snapshot -i 找含 text 的行 -> 提取 ref -> 真实点击"""
    out = ab(["snapshot", "-i"])
    refs = []
    for line in out.splitlines():
        if text in line:
            m = re.search(r"ref=e\d+", line)
            if m and m.group(0) not in refs:
                refs.append(m.group(0))
    if len(refs) <= nth:
        return f"REF_NOMATCH({len(refs)})"
    return ab(["click", refs[nth]])

def body_text():
    r = evaljs('(() => { const t=document.body?document.body.innerText:""; return t.replace(/\\s+/g," "); })()')
    return r.strip().strip('"') if r else ""

def get_view_count():
    """解析搜索结果头部 共 N 封（兼容千分位逗号）；找不到返回 -1"""
    t = body_text()
    m = re.search(r"共\s*([\d,]+)\s*封", t)
    if not m:
        return -1
    return int(m.group(1).replace(",", ""))

def selected_count():
    """解析 已选中全部 N 封邮件；未选中返回 -1"""
    t = body_text()
    m = re.search(r"已选中全部\s*([\d,]+)\s*封", t)
    if not m:
        return -1
    return int(m.group(1).replace(",", ""))

def chip_ok(sender):
    t = body_text()
    # chip 引号可能是直引号或弯引号
    return (f'发件人:包含“{sender}”' in t) or (f'发件人:包含"{sender}"' in t) or \
           (f'发件人:包含“{sender}' in t and sender in t)

def clear_and_type(sender):
    ab(["click", ".mail-lazy-search-wrap"]); wait(0.8)
    # 确认过滤输入框存在
    r = evaljs('(() => { const i=document.querySelector("input.filter-input"); return i?"OK":"NOINPUT"; })()')
    if "NOINPUT" in r:
        # 再试一次
        ab(["click", ".mail-lazy-search-wrap"]); wait(1.0)
        r = evaljs('(() => { const i=document.querySelector("input.filter-input"); return i?"OK":"NOINPUT"; })()')
        if "NOINPUT" in r:
            return "NOINPUT"
    evaljs("""(() => { const i=document.querySelector('input.filter-input'); if(i){const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set; s.call(i,''); i.dispatchEvent(new Event('input',{bubbles:true}));} })()""")
    wait(0.4)
    ab(["type", "input.filter-input", sender]); wait(1.5)
    return "OK"

def click_unread_tab():
    """点「未读」分面（仅当结果混有已读/未读时才渲染）：优先 .item-name 精确匹配"""
    js = """(() => { const el=[...document.querySelectorAll('.item-name')].find(e=>e.textContent.trim()==='未读'); if(!el) return 'NOMATCH'; el.click(); return 'CLICKED'; })()"""
    r = evaljs(js)
    if "CLICKED" in r:
        return "OK"
    return click_ref_text("未读")

def parse_eval_json(r):
    """eval 返回的 JSON 字符串可能带引号包裹与 \\" 转义，统一清洗"""
    s = (r or "").strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    s = s.replace('\\"', '"').replace("&quot;", '"')
    return json.loads(s)

def rows_unread_check():
    """行级校验：当前渲染的搜索结果行必须全部带 .mail-unread 标记。
    返回 (ok, rows, withUnread)"""
    js = """(() => {
      const rows=[...document.querySelectorAll('.mail-list-search-item')];
      if(!rows.length) return JSON.stringify({rows:0, withUnread:0});
      let wu=0; rows.forEach(r=>{ if(r.querySelector('.mail-unread')) wu++; });
      return JSON.stringify({rows:rows.length, withUnread:wu});
    })()"""
    r = evaljs(js)
    try:
        d = parse_eval_json(r)
    except Exception:
        return False, -1, -1
    return d["rows"] > 0 and d["rows"] == d["withUnread"], d["rows"], d["withUnread"]

def click_tab(name):
    js = f"""(() => {{ const el=[...document.querySelectorAll('.item-name')].find(e=>e.textContent.trim()==='{name}'); if(!el) return 'NOMATCH'; el.click(); return 'CLICKED'; }})()"""
    r = evaljs(js)
    if "CLICKED" in r:
        return "OK"
    return click_ref_text(name)

def open_mark_submenu():
    """hover 别处 -> 真实点击 标记为；验证子菜单出现"""
    ab(["hover", ".mail-search-select-all"]); wait(0.4)
    r = click_ref_text("标记为")
    wait(1.0)
    t = body_text()
    if "已读邮件" in t and "新建标签" in t:
        return "OK"
    # 重试一次
    r = click_ref_text("标记为")
    wait(1.0)
    t = body_text()
    return "OK" if ("已读邮件" in t and "新建标签" in t) else f"SUBMENU_FAIL {r}"

def click_panel_item(name):
    """点面板菜单项（标记为/移动到共用组件）：可见项精确文本 + 合成点击（已验证有效）"""
    js = f"""(() => {{
      const el=[...document.querySelectorAll('.ui-panel-item-content')].filter(e=>e.offsetParent!==null).find(e=>e.textContent.trim()==='{name}');
      if(!el) return 'NOMATCH'; el.click(); return 'CLICKED'; }})()"""
    return evaljs(js)

def close_submenu():
    ab(["press", "Escape"]); wait(0.4)

def apply_label(label, log):
    """打开标记为子菜单；若标签已存在则直接点，否则新建标签"""
    if open_mark_submenu() != "OK":
        log.append("label: SUBMENU_FAIL")
        return False
    # 子菜单里找已有标签项
    js = f"""(() => {{
      const items=[...document.querySelectorAll('.ui-panel-item-content')].map(e=>e.textContent.trim());
      return JSON.stringify(items); }})()"""
    try:
        items = parse_eval_json(evaljs(js))
    except Exception:
        items = []
    log.append("submenu_items: " + json.dumps(items, ensure_ascii=False))
    ok = False
    if label in items:
        rr = click_panel_item(label)
        wait(1.2)
        log.append(f"label_click({label}): {rr}")
        ok = "Done" in rr or "✓" in rr
    else:
        # 新建标签
        rr = click_panel_item("新建标签")
        wait(1.2)
        log.append(f"new_label_dialog: {rr}")
        # 探测对话框可见输入框（排除搜索过滤输入框）
        probe = evaljs("""(() => {
          const inputs=[...document.querySelectorAll('input')].filter(i=>i.offsetParent!==null && !i.className.includes('filter-input'));
          return JSON.stringify(inputs.map((i,k)=>({k, cls:i.className.slice(0,50), ph:i.placeholder||"", type:i.type})));
        })()""")
        log.append("dialog_inputs: " + probe)
        try:
            inputs = parse_eval_json(probe)
        except Exception:
            inputs = []
        target = None
        for inp in inputs:
            if "标签" in inp.get("ph", "") or "名称" in inp.get("ph", ""):
                target = inp; break
        if target is None and inputs:
            target = inputs[-1]
        if target is None:
            ab(["press", "Escape"]); wait(0.4)
            log.append("label: NO_DIALOG_INPUT")
            return False
        # 用类名精确定位该输入框
        ph = target.get("ph") or ""
        sel = f'input[placeholder="{ph}"]' if ph else "input"
        ab(["fill", sel, label]); wait(0.5)
        ab(["press", "Enter"]); wait(1.5)
        # 可能需要点确认按钮
        t = body_text()
        for btn in ("确定", "创建", "保存"):
            if btn in t:
                rr2 = click_ref_text(btn)
                wait(1.0)
                log.append(f"confirm({btn}): {rr2}")
                break
        log.append(f"label_typed: {label} via {sel}")
        ok = True
    close_submenu()
    return ok

def process_one(sender, action, folder, label):
    log = [f"### {sender} -> action={action} folder={folder} label={label}"]
    # 1. 搜索
    ct = clear_and_type(sender)
    if ct != "OK":
        log.append(f"ABORT search: {ct}")
        return "\n".join(log), 0
    # 2. 真实点击 发件人包含
    r = ab(["click", ".include-item"]); wait(2.2)
    log.append(f"click_include: {r}")
    # 3. chip 断言
    if not chip_ok(sender):
        log.append(f"ABORT chip: chip_not_found")
        return "\n".join(log), 0
    n_total = get_view_count()
    log.append(f"n_total: {n_total}")
    # 4. 未读分面（结果混有已读/未读时才渲染；点了就收窄到未读）
    cu = click_unread_tab(); wait(1.8)
    n_unread = get_view_count()
    log.append(f"facet: {cu} n_unread: {n_unread}")
    # 行级硬门禁：所有渲染行必须带 .mail-unread 标记
    ok, nrows, wu = rows_unread_check()
    log.append(f"rows_check: ok={ok} rows={nrows} unread={wu}")
    if not ok:
        log.append("ABORT rows_not_all_unread")
        return "\n".join(log), 0
    n_final = get_view_count()
    if n_final <= 0:
        log.append("no unread -> skip")
        return "\n".join(log), 0
    # 5. 全选 + 数量断言
    ab(["click", ".mail-search-select-all"]); wait(1.5)
    n_sel = selected_count()
    log.append(f"n_selected: {n_sel}")
    if n_sel != n_final:
        # 取消选择，保护现场
        ab(["click", ".mail-search-select-all"]); wait(0.8)
        log.append(f"ABORT count_mismatch: sel={n_sel} view={n_final}")
        return "\n".join(log), 0
    # 6. 动作
    processed = 0
    if action == "move":
        if label:
            ok = apply_label(label, log)
            log.append(f"label_result: {ok}")
        # 移动到
        r1 = click_ref_text("移动到"); wait(1.2)
        r2 = click_panel_item(folder); wait(2.0)
        log.append(f"move: {r1} | {r2}")
        processed = n_final
    else:  # keep
        if label:
            ok = apply_label(label, log)
            log.append(f"label_result: {ok}")
        # 取消选择，邮件保持未读在收件箱
        ab(["click", ".mail-search-select-all"]); wait(0.8)
        processed = n_final
    # 7. 后验证：move 后视图可能有缓存残留，不自动二轮（统一由最后清点处理）
    if action == "move":
        wait(1.5)
        n_after = get_view_count()
        log.append(f"n_after(view cached): {n_after}")
    return "\n".join(log), processed

def main():
    test = len(sys.argv) > 1 and sys.argv[1] == "test"
    only = sys.argv[2] if len(sys.argv) > 2 else None
    data = json.load(open(os.path.join(WORKDIR, "unread_raw.json"), encoding="utf-8"))
    if test:
        senders = [only or "newsletter@example.com"]
    else:
        senders = sorted(set(d[0] for d in data))
    results = {}
    logf = open(os.path.join(WORKDIR, "batch_run_log.txt"), "a", encoding="utf-8")
    for snd in senders:
        # ECOM 类需要主题做特判
        subj = next((d[2] for d in data if d[0] == snd), "")
        summ = next((d[4] for d in data if d[0] == snd), "")
        act, fld = action_for(snd, subj, summ)
        if act == "skip":
            results[snd] = {"action": "skip"}
            continue
        lbl = fld if act == "keep" else ("扫读" if fld == "订阅周报" else None)
        txt, n = process_one(snd, act, fld, lbl)
        results[snd] = {"action": act, "folder": fld, "label": lbl, "processed": n}
        print(txt); print("-"*60)
        logf.write(txt + "\n" + "-"*60 + "\n"); logf.flush()
        wait(1.0)
    json.dump(results, open(os.path.join(WORKDIR, "batch_results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    logf.close()
    total = sum(v.get("processed", 0) for v in results.values())
    print(f"\n=== DONE, processed {total} emails across {len(results)} senders ===")

if __name__ == "__main__":
    main()