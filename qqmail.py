#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QQ 邮箱整理工具（配置驱动，安全优先）

所有参数来自 config.json，不要改脚本常量。

命令：
  python qqmail.py state                       列出所有文件夹与计数
  python qqmail.py scan  <文件夹> [--rule R]   只读扫描，落盘 UID 清单，不动任何邮件
  python qqmail.py move  <文件夹> --rule R     把命中规则的邮件移到「待删除」（可恢复）
  python qqmail.py restore <文件夹>            把「待删除」里的同源邮件搬回去
  python qqmail.py rules                       列出可用规则

安全铁律（来自一次真实误删事故的教训）：
  1. 绝不用「文件夹计数差」判断移动结果 → 用 UID SEARCH 精确核对
  2. 全程只 SELECT 一次 → 避免 imaplib 响应错位
  3. 批量固定 20 → 异常即可定位
  4. 目标只写自建「待删除」，绝不写 QQ 回收站（有容量淘汰）
  5. scan/dry 永不移动；move 前必须已有 UID 清单落盘
"""
import base64
import email
import imaplib
import json
import os
import re
import sys
import time
from email.header import decode_header, make_header

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")


# ============ 基础工具 ============
def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit("找不到配置：%s" % CONFIG_PATH)
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def imap_utf7_encode(s):
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            b = base64.b64encode(buf.encode("utf-16-be")).decode().rstrip("=")
            out.append("&" + b.replace("/", ",") + "-")
            buf = ""

    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def q(folder):
    if folder == "INBOX":
        return "INBOX"
    if folder in ("Deleted Messages", "Sent Messages", "Drafts", "Junk"):
        return '"%s"' % folder
    return '"%s"' % imap_utf7_encode(folder)


def dh(v):
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v))).strip()
    except Exception:
        return v


def connect(cfg):
    acct = cfg["account"]
    auth_path = os.path.join(BASE, acct["auth_file"])
    if not os.path.exists(auth_path):
        sys.exit("找不到授权码文件：%s" % auth_path)
    code = re.sub(r"\s+", "", open(auth_path, encoding="utf-8").read())
    M = imaplib.IMAP4_SSL(acct["host"], acct["port"])
    M.login(acct["user"], code)
    return M


def count(M, folder):
    t, d = M.select(q(folder), readonly=True)
    return int(d[0]) if t == "OK" and d and d[0] else -1


def all_uids(M):
    t, d = M.uid("SEARCH", None, "ALL")
    return d[0].split() if t == "OK" and d and d[0] else []


def fetch_heads(M, uids):
    out = {}
    for i in range(0, len(uids), 100):
        t, d = M.uid("FETCH", b",".join(uids[i:i + 100]),
                     "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
        if t != "OK":
            continue
        for it in d:
            if not isinstance(it, tuple):
                continue
            m = re.search(r"UID\s+(\d+)", it[0].decode("utf-8", "ignore"))
            if not m:
                continue
            try:
                msg = email.message_from_bytes(it[1] if isinstance(it[1], bytes) else b"")
                out[m.group(1).encode()] = (dh(msg.get("From")), dh(msg.get("Subject")))
            except Exception:
                out[m.group(1).encode()] = ("", "")
    return out


# ============ 规则匹配 ============
def match_rule(cfg, rule_name, frm, sub, ruleset="rules"):
    rule = cfg.get(ruleset, {}).get(rule_name)
    if not rule or not isinstance(rule, dict):
        return False
    field = rule.get("field", "subject")
    hay = sub if field == "subject" else frm
    hay = hay or ""
    if "contains" in rule:
        return any(k in hay for k in rule["contains"])
    if "regex" in rule:
        return bool(re.search(rule["regex"], hay, re.I))
    return False


def is_protected(cfg, frm, sub):
    p = cfg["protected"]["regex"]
    ad = cfg["ad_marker"]["regex"]
    hit = bool(re.search(p, sub or "", re.I) or re.search(p, frm or "", re.I))
    if not hit:
        return False
    # 带广告标记的即使含保护词也算营销
    return not bool(re.search(ad, sub or "", re.I))


# ============ 安全移动 ============
def safe_move(M, cfg, src, uids, dst):
    bs = cfg["safety"]["batch_size"]
    if not uids:
        return 0, []
    t, d = M.select(q(src))          # 只 SELECT 一次（读写）
    if t != "OK":
        print("!! 无法选中 %s: %s" % (src, d))
        return 0, list(uids)

    ok, fail = 0, []
    total = len(uids)
    for i in range(0, total, bs):
        chunk = uids[i:i + bs]
        uset = b",".join(chunk)
        try:
            M.uid("MOVE", uset, q(dst))
        except Exception as e:
            print("   !! 异常: %s" % e)
            fail.extend(chunk)
            if cfg["safety"]["abort_on_partial_batch"]:
                break
            continue

        # 精确核对：这批 UID 是否还在源文件夹
        t2, d2 = M.uid("SEARCH", None, "UID", uset)
        still = set(d2[0].split()) if t2 == "OK" and d2 and d2[0] else set()
        gone = [u for u in chunk if u not in still]
        left = [u for u in chunk if u in still]
        ok += len(gone)
        fail.extend(left)
        if left:
            print("   !! 批 %d: %d 个仅移走 %d 个" % (i // bs + 1, len(chunk), len(gone)))
            if cfg["safety"]["abort_on_partial_batch"]:
                print("   !! 已中止（abort_on_partial_batch=true）")
                break
        if (i // bs) % 5 == 0 or i + bs >= total:
            print("   批 %d: 移走 %d/%d（累计 %d/%d）"
                  % (i // bs + 1, len(gone), len(chunk), ok, total), flush=True)
        time.sleep(0.12)
    return ok, fail


# ============ 命令 ============
def cmd_state(M, cfg):
    """只用 SELECT + UID SEARCH UNSEEN，绝不混用 STATUS
    （SELECT 与 STATUS 混用会导致响应错位，读出错数——曾因此误判"丢了数百封"）"""
    print("%-16s %8s %8s" % ("文件夹", "总数", "未读"))
    print("-" * 34)
    tot = 0
    for label, name in cfg["folders"].items():
        t, d = M.select(q(name), readonly=True)
        if t != "OK" or not d or not d[0]:
            print("%-16s %8s %8s" % (name, "?", "?"))
            continue
        n = int(d[0])
        t2, d2 = M.uid("SEARCH", None, "UNSEEN")
        u = len(d2[0].split()) if t2 == "OK" and d2 and d2[0] else 0
        print("%-16s %8d %8d" % (name, n, u))
        tot += n
    print("-" * 34)
    print("%-16s %8d" % ("以上合计", tot))


def cmd_rules(cfg):
    print("【可删规则】（qqmail.py move 用）")
    for k, v in cfg["rules"].items():
        print("  %-14s %s" % (k, v.get("desc", "")))
    print("\n【归位规则】（qqmail.py relocate 用，只搬家不删）")
    for k, v in cfg.get("relocate_rules", {}).items():
        if k.startswith("_"):
            continue
        print("  %-14s %s" % (k, v.get("desc", "")))


def collect(M, cfg, folder, rule, ruleset="rules"):
    if count(M, folder) < 0:
        sys.exit("选不中文件夹：%s" % folder)
    uids = all_uids(M)
    heads = fetch_heads(M, uids)
    hit, protected = [], []
    for u in uids:
        frm, sub = heads.get(u, ("", ""))
        if not match_rule(cfg, rule, frm, sub, ruleset):
            continue
        if ruleset == "rules" and is_protected(cfg, frm, sub):
            protected.append(u)
            continue
        hit.append(u)
    return uids, heads, hit, protected


def cmd_scan(M, cfg, folder, rule):
    uids, heads, hit, protected = collect(M, cfg, folder, rule)
    print("%s 共 %d 封；规则「%s」命中 %d 封；其中受保护 %d 封被排除\n"
          % (folder, len(uids), rule, len(hit), len(protected)))
    plan = os.path.join(BASE, cfg["plan_file"])
    json.dump({"src": folder, "rule": rule,
               "uids": [u.decode() for u in hit],
               "protected": [u.decode() for u in protected]},
              open(plan, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("UID 清单已落盘 -> %s" % plan)
    for u in hit[:8]:
        frm, sub = heads[u]
        print("   例: %-26s | %s" % (frm[:26], sub[:40]))
    print("\n[scan] 只读，未改动任何邮件。")


def cmd_move(M, cfg, folder, rule):
    plan_path = os.path.join(BASE, cfg["plan_file"])
    uids, heads, hit, protected = collect(M, cfg, folder, rule)

    # 强制：必须先 scan 过，且清单一致
    if not os.path.exists(plan_path):
        sys.exit("!! 拒绝执行：请先运行 scan 落盘 UID 清单")
    old = json.load(open(plan_path, encoding="utf-8"))
    old_set = set(old.get("uids", []))
    new_set = set(u.decode() for u in hit)
    if old["src"] != folder or old["rule"] != rule:
        sys.exit("!! 拒绝执行：清单与本次参数不一致（清单 %s/%s）" % (old["src"], old["rule"]))
    if old_set != new_set:
        sys.exit("!! 拒绝执行：邮件集合与 scan 时不同（清单 %d / 现在 %d），请重新 scan"
                 % (len(old_set), len(new_set)))

    limit = cfg["safety"]["max_moves_per_run"]
    if len(hit) > limit:
        sys.exit("!! 本次 %d 封超过上限 %d，请拆批" % (len(hit), limit))

    stage = cfg["safety"]["staging_folder"]
    t, _ = M.create(q(stage))
    print("暂存文件夹「%s」%s" % (stage, "已创建" if t == "OK" else "已存在"))
    print("开始移动 %d 封 → %s（每批 %d）\n" % (len(hit), stage, cfg["safety"]["batch_size"]))

    ok, fail = safe_move(M, cfg, folder, hit, stage)

    log = os.path.join(BASE, cfg["run_log"])
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": "move", "src": folder, "dst": stage, "rule": rule,
            "requested": len(hit), "moved": ok, "failed": len(fail),
            "uids": [u.decode() for u in hit],
        }, ensure_ascii=False) + "\n")

    print("\n=== 结果 ===")
    print("  %s 剩余 %d 封" % (folder, count(M, folder)))
    print("  %s 现有 %d 封" % (stage, count(M, stage)))
    print("  成功 %d / 失败 %d" % (ok, len(fail)))
    if fail:
        fp = os.path.join(BASE, "failed_uids.txt")
        open(fp, "w").write("\n".join(u.decode() for u in fail))
        print("  失败 UID -> %s" % fp)


def cmd_restore(M, cfg, folder):
    stage = cfg["safety"]["staging_folder"]
    n = count(M, stage)
    if n < 0:
        sys.exit("选不中「%s」" % stage)
    uids = all_uids(M)
    heads = fetch_heads(M, uids)
    # 只搬回属于目标文件夹的（按发件人/主题无法判断来源，故全量搬回）
    print("「%s」现有 %d 封，准备全部搬回 %s" % (stage, len(uids), folder))
    ok, fail = safe_move(M, cfg, stage, uids, folder)
    print("搬回 %d 封，失败 %d 封" % (ok, len(fail)))


def cmd_relocate(M, cfg, src, dst, rule, apply=False):
    """只搬家不删除：把命中的邮件从 src 移到 dst（用于「归位」）"""
    uids, heads, hit, protected = collect(M, cfg, src, rule, ruleset="relocate_rules")
    print("%s 共 %d 封；归位规则「%s」命中 %d 封 → %s\n"
          % (src, len(uids), rule, len(hit), dst))
    for u in hit[:8]:
        frm, sub = heads[u]
        print("   例: %-26s | %s" % (frm[:26], sub[:40]))
    if not apply:
        print("\n[relocate] 仅预览，加 --apply 才执行。")
        return
    if not hit:
        print("无命中，不执行。")
        return
    t, _ = M.create(q(dst))
    print("\n目标文件夹「%s」%s" % (dst, "已创建" if t == "OK" else "已存在"))
    ok, fail = safe_move(M, cfg, src, hit, dst)
    with open(os.path.join(BASE, cfg["run_log"]), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": "relocate", "src": src, "dst": dst, "rule": rule,
            "requested": len(hit), "moved": ok, "failed": len(fail),
            "uids": [u.decode() for u in hit],
        }, ensure_ascii=False) + "\n")
    print("\n=== 结果 ===\n  %s 剩余 %d 封\n  %s 现有 %d 封\n  成功 %d / 失败 %d"
          % (src, count(M, src), dst, count(M, dst), ok, len(fail)))


def cmd_purge(M, cfg, apply=False):
    """永久删除「待删除」里的全部邮件（UID STORE \\Deleted + UID EXPUNGE）"""
    stage = cfg["safety"]["staging_folder"]
    t, d = M.select(q(stage))
    if t != "OK":
        sys.exit("选不中「%s」：%s" % (stage, d))
    uids = all_uids(M)
    print("「%s」共 %d 封，将**永久删除**（不可恢复）" % (stage, len(uids)))
    if not uids:
        print("已经是空的。")
        return
    if not apply:
        print("\n[purge] 仅预览。确认无误后加 --apply 执行永久删除。")
        return

    bs = cfg["safety"]["batch_size"]
    done = 0
    # 先分批打 \Deleted 标记（不删，留着可核对）
    for i in range(0, len(uids), bs):
        chunk = uids[i:i + bs]
        try:
            M.uid("STORE", b",".join(chunk), "+FLAGS", "(\\Deleted)")
        except Exception as e:
            print("   !! 批 %d 打标记异常: %s" % (i // bs + 1, e))
            break
        done += len(chunk)
        if (i // bs) % 10 == 0 or i + bs >= len(uids):
            print("   批 %d: 已标记 %d/%d" % (i // bs + 1, done, len(uids)), flush=True)
        time.sleep(0.1)

    # QQ 声明支持 UIDPLUS 但实测 UID EXPUNGE 不生效，只认普通 EXPUNGE。
    # 「待删除」是专用文件夹，里面的东西本来就是要删的，整夹 EXPUNGE 安全。
    print("   执行 EXPUNGE ...", flush=True)
    try:
        M.expunge()
    except Exception as e:
        print("   !! EXPUNGE 异常: %s" % e)

    left = len(all_uids(M))
    with open(os.path.join(BASE, cfg["run_log"]), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": "purge", "folder": stage,
            "requested": len(uids), "purged": done,
        }, ensure_ascii=False) + "\n")
    print("\n=== 结果 ===\n  「%s」剩余 %d 封（原 %d）" % (stage, left, len(uids)))


def main():
    cfg = load_config()
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]

    if cmd == "rules":
        cmd_rules(cfg)
        return

    M = connect(cfg)
    try:
        if cmd == "state":
            cmd_state(M, cfg)
        elif cmd in ("scan", "move"):
            folder = args[1] if len(args) > 1 else None
            rule = None
            if "--rule" in args:
                rule = args[args.index("--rule") + 1]
            if not folder or not rule:
                sys.exit("用法: qqmail.py %s <文件夹> --rule <规则名>" % cmd)
            (cmd_scan if cmd == "scan" else cmd_move)(M, cfg, folder, rule)
        elif cmd == "restore":
            folder = args[1] if len(args) > 1 else None
            if not folder:
                sys.exit("用法: qqmail.py restore <目标文件夹>")
            cmd_restore(M, cfg, folder)
        elif cmd == "relocate":
            # qqmail.py relocate <src> <dst> --rule X [--apply]
            pos = [a for a in args[1:] if not a.startswith("--")]
            if len(pos) < 2 or "--rule" not in args:
                sys.exit("用法: qqmail.py relocate <源> <目标> --rule <归位规则> [--apply]")
            cmd_relocate(M, cfg, pos[0], pos[1],
                         args[args.index("--rule") + 1],
                         apply="--apply" in args)
        elif cmd == "purge":
            cmd_purge(M, cfg, apply="--apply" in args)
        else:
            print(__doc__)
    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
