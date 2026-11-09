# -*- coding: utf-8 -*-
"""
收尾脚本（在 batch_process_mail.py 全量跑完后执行）：
  1. 重试批处理中 ABORT 的发件人（移动类）
  2. 订阅周报：全选 -> 打 扫读 标签（不存在则创建）-> 全部标已读
  3. 广告通知：全选 -> 全部标已读（无标签）
  4. keep 类发件人：搜索 -> 未读门禁 -> 打 建议读/重点读 -> 取消选择（保持未读）
  5. 最终清点各文件夹计数
"""
import sys, json, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from batch_process_mail import (
    ab, evaljs, wait, click_ref_text, click_panel_item, open_mark_submenu,
    close_submenu, body_text, get_view_count, selected_count, chip_ok,
    clear_and_type, click_unread_tab, rows_unread_check, parse_eval_json,
    action_for, SENDER, WORKDIR,
)

def click_sidebar(name):
    js = f"""(() => {{
      const el=[...document.querySelectorAll('.sidebar-menu-text')].find(e=>e.textContent.trim()==='{name}');
      if(!el) return 'NOMATCH'; el.click(); return 'CLICKED'; }})()"""
    return evaljs(js)

def folder_select_all():
    """文件夹视图的全选：优先 .mail-search-select-all，找不到用 snapshot ref 兜底"""
    r = evaljs('(() => { return document.querySelector(".mail-search-select-all")?"OK":"NO"; })()')
    if "OK" in r:
        return ab(["click", ".mail-search-select-all"])
    return click_ref_text("已选中")

def apply_label_smart(label, log):
    """在已打开选中的前提下打标签：标签存在直接点，否则新建（创建即应用）"""
    if open_mark_submenu() != "OK":
        log.append(f"label({label}): SUBMENU_FAIL")
        return False
    try:
        items = parse_eval_json(evaljs(
            "(() => { return JSON.stringify([...document.querySelectorAll('.ui-panel-item-content')].filter(e=>e.offsetParent!==null).map(e=>e.textContent.trim())); })()"))
    except Exception:
        items = []
    if label in items:
        rr = click_panel_item(label)
        wait(1.2)
        log.append(f"label_click({label}): {rr}")
        close_submenu()
        return "CLICKED" in rr
    rr = click_panel_item("新建标签")
    wait(1.3)
    probe = evaljs("""(() => {
      const inputs=[...document.querySelectorAll('input')].filter(i=>i.offsetParent!==null && !i.className.includes('filter-input'));
      return JSON.stringify(inputs.map((i,k)=>({k, cls:i.className.slice(0,50), ph:i.placeholder||"", type:i.type})));
    })()""")
    log.append(f"dialog: {probe[:200]}")
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
        close_submenu()
        log.append(f"label({label}): NO_DIALOG_INPUT")
        return False
    ph = target.get("ph") or ""
    sel = f'input[placeholder="{ph}"]' if ph else "input"
    ab(["fill", sel, label]); wait(0.5)
    ab(["press", "Enter"]); wait(1.5)
    t = body_text()
    for btn in ("确定", "创建", "保存"):
        if btn in t:
            rr2 = click_ref_text(btn)
            wait(1.0)
            log.append(f"confirm({btn}): {rr2}")
            break
    log.append(f"label_created: {label}")
    close_submenu()
    return True

def mark_selected_read(log):
    """选中状态下：标记为 -> 已读邮件"""
    if open_mark_submenu() != "OK":
        log.append("mark_read: SUBMENU_FAIL")
        return False
    rr = click_panel_item("已读邮件")
    wait(1.5)
    log.append(f"mark_read: {rr}")
    close_submenu()
    return "CLICKED" in rr

def process_folder(folder, label):
    """进入文件夹 -> 全选 -> (打标签) -> 标已读"""
    log = [f"### FOLDER {folder} label={label}"]
    r = click_sidebar(folder); wait(2.5)
    log.append(f"open: {r}")
    n = get_view_count()
    log.append(f"count: {n}")
    if n <= 0:
        log.append("empty -> skip")
        print("\n".join(log)); return
    ab(["click", ".mail-search-select-all"]); wait(1.5)
    ns = selected_count()
    log.append(f"selected: {ns}")
    if ns <= 0:
        log.append("select FAILED -> skip")
        print("\n".join(log)); return
    if label:
        apply_label_smart(label, log)
    mark_selected_read(log)
    # 收尾：取消选择
    ab(["click", ".mail-search-select-all"]); wait(0.8)
    print("\n".join(log))

def keep_sender_label(sender, label):
    """keep 类：搜索 -> 未读门禁 -> 打标签 -> 取消选择（保持未读原地）"""
    log = [f"### KEEP {sender} label={label}"]
    ct = clear_and_type(sender)
    if ct != "OK":
        log.append(f"ABORT search: {ct}")
        print("\n".join(log)); return 0
    r = ab(["click", ".include-item"]); wait(2.2)
    if not chip_ok(sender):
        log.append("ABORT chip")
        print("\n".join(log)); return 0
    n_total = get_view_count()
    click_unread_tab(); wait(1.8)
    ok, nrows, wu = rows_unread_check()
    log.append(f"total={n_total} rows={nrows}/{wu}")
    if not ok:
        log.append("ABORT rows_not_all_unread")
        print("\n".join(log)); return 0
    n_final = get_view_count()
    if n_final <= 0:
        log.append("no unread")
        print("\n".join(log)); return 0
    ab(["click", ".mail-search-select-all"]); wait(1.5)
    ns = selected_count()
    log.append(f"selected: {ns}")
    if ns != n_final:
        ab(["click", ".mail-search-select-all"]); wait(0.8)
        log.append(f"ABORT count_mismatch {ns}!={n_final}")
        print("\n".join(log)); return 0
    apply_label_smart(label, log)
    ab(["click", ".mail-search-select-all"]); wait(0.8)
    print("\n".join(log))
    return n_final

def retry_aborts():
    """重试批处理里 processed=0 的 move 类发件人"""
    from batch_process_mail import process_one
    try:
        results = json.load(open(f"{WORKDIR}/batch_results.json", encoding="utf-8"))
    except Exception:
        results = {}
    retry = [s for s, v in results.items()
             if v.get("action") == "move" and v.get("processed", 0) == 0]
    print(f"RETRY move-aborts: {retry}")
    out = {}
    for snd in retry:
        act, fld = action_for(snd)
        if act != "move":
            continue
        txt, n = process_one(snd, act, fld, None)
        print(txt); print("-"*50)
        out[snd] = n
        wait(1.0)
    return out

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    logf = open(f"{WORKDIR}/finalize_log.txt", "a", encoding="utf-8")
    if mode in ("all", "retry"):
        retry_aborts()
    if mode in ("all", "folders"):
        process_folder("订阅周报", "扫读")
        process_folder("广告通知", None)
    if mode in ("all", "keep"):
        data = json.load(open(f"{WORKDIR}/unread_raw.json", encoding="utf-8"))
        senders = sorted(set(d[0] for d in data))
        for snd in senders:
            act, fld = action_for(snd)
            if act == "keep":
                keep_sender_label(snd, fld)
                wait(1.0)
    print("FINALIZE DONE")
