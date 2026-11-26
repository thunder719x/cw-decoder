"""把项目上传/更新到 GitHub（只用一个 Token，不需要安装 git）。

用法：
    双击  上传到GitHub.bat
或：
    python tools/upload_github.py --dry-run        # 只列出将要上传的文件
    python tools/upload_github.py                  # 真正上传

Token 第一次运行时会询问，并保存到：
    %APPDATA%\\CWDecoder\\github_token.txt
（只保存在你自己电脑上；不要把它提交到仓库）

需要的权限：classic token 勾选 "repo" 即可（或 fine-grained 的 Contents: Read and write）
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:                                  # 让中文在 cmd/终端里正常显示
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

API = "https://api.github.com"
SKIP_DIRS = {".git", "out", "__pycache__", "build", "dist", "venv", ".venv", ".idea", ".vscode"}
SKIP_SUFFIX = (".pyc", ".pyo", ".log", ".tmp")
SKIP_FILES = {"crash.log", "Thumbs.db", ".DS_Store"}


def token_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "CWDecoder")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "github_token.txt")


def _clipboard() -> str:
    """读剪贴板（用 tkinter，不需要额外依赖）。"""
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        txt = r.clipboard_get()
        r.destroy()
        return (txt or "").strip()
    except Exception:
        return ""


def _looks_like_token(t: str) -> bool:
    return bool(t) and len(t) >= 20 and (
        t.startswith(("ghp_", "gho_", "ghu_", "ghs_", "github_pat_")))


def load_token() -> str:
    # 1) 环境变量
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok

    # 2) 本地保存过的
    p = token_path()
    if os.path.exists(p):
        tok = open(p, encoding="utf-8").read().strip()
        if tok:
            return tok

    # 3) 脚本旁边的 token.txt
    here_txt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
    if os.path.exists(here_txt):
        tok = open(here_txt, encoding="utf-8").read().strip()
        if tok:
            return tok

    print("需要一次 GitHub Token（只需输入一次，之后会记住）")
    print("获取方式：GitHub → 头像 → Settings → Developer settings")
    print("          → Personal access tokens → Tokens (classic)")
    print("          → Generate new token (classic) → 勾选 repo → 生成后复制")
    print()

    # 4) 剪贴板里已经有？直接用
    clip = _clipboard()
    if _looks_like_token(clip):
        print(f"检测到剪贴板里有 Token（{clip[:8]}…{clip[-4:]}）")
        ans = input("直接使用这个 Token 吗？[Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            tok = clip
        else:
            clip = ""

    if not tok:
        print("请先复制 Token 到剪贴板，然后回来按回车（或直接粘贴）")
        print("提示：窗口里 右键=粘贴，也可以 Ctrl+V")
        try:
            tok = input("Token：").strip()
        except (EOFError, KeyboardInterrupt):
            tok = ""
        if not tok:
            tok = _clipboard()          # 用户直接按了回车，改用剪贴板
        if not tok:
            print("没有拿到 Token，退出。")
            sys.exit(1)
        if not _looks_like_token(tok):
            print("注意：这串字符不像 GitHub Token（一般以 ghp_ 或 github_pat_ 开头）")
            ans = input("仍然使用吗？[y/N] ").strip().lower()
            if ans != "y":
                sys.exit(1)

    tok = tok.strip()
    with open(p, "w", encoding="utf-8") as f:
        f.write(tok)
    print(f"已保存到：{p}（下次不用再输入）")
    return tok


def api(method: str, path: str, token: str, payload: dict | None = None) -> tuple[int, dict]:
    url = API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "cw-decoder-uploader")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "ignore")
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"message": body[:200]}
    except Exception as e:                       # 网络问题
        return 0, {"message": f"{type(e).__name__}: {e}"}


def collect(root: str) -> list[tuple[str, str]]:
    """返回 [(仓库内相对路径, 本地绝对路径)]，按 .gitignore 的意图过滤。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name in SKIP_FILES or name.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            out.append((rel, full))
    out.sort()
    return out


def blob_sha(data: bytes) -> str:
    """Git blob 的 sha1（和 GitHub 用的算法一致）。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def remote_tree(token: str, owner: str, repo: str, tree_sha: str) -> dict[str, str]:
    """取远端所有文件的 path -> blob sha。"""
    st, data = api("GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1", token)
    if st != 200:
        return {}
    out = {}
    for item in data.get("tree", []):
        if item.get("type") == "blob":
            out[item["path"]] = item["sha"]
    return out


def human(n: int) -> str:
    return f"{n/1024/1024:.1f} MB" if n >= 1024 * 1024 else f"{n/1024:.0f} KB"


def main() -> int:
    ap = argparse.ArgumentParser(description="上传/更新仓库（无需 git）")
    ap.add_argument("--owner", default="thunder719x")
    ap.add_argument("--repo", default="cw-decoder")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="")
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = collect(args.root)
    total = sum(os.path.getsize(p) for _, p in files)
    print(f"项目目录：{args.root}")
    print(f"将上传 {len(files)} 个文件，共 {human(total)}")
    for rel, p in files[:12]:
        print(f"   {rel:<44} {human(os.path.getsize(p))}")
    if len(files) > 12:
        print(f"   … 其余 {len(files)-12} 个")
    if args.dry_run:
        print("\n[dry-run] 未上传。")
        return 0

    token = load_token()
    msg = args.message or ("上传/更新 " + time.strftime("%Y-%m-%d %H:%M"))

    # 1) 取当前分支头（空仓库会返回 404）
    st, ref = api("GET", f"/repos/{args.owner}/{args.repo}/git/ref/heads/{args.branch}", token)
    parent = None
    base_tree = None
    if st == 200:
        parent = ref.get("object", {}).get("sha")
        st2, commit = api("GET", f"/repos/{args.owner}/{args.repo}/git/commits/{parent}", token)
        if st2 == 200:
            base_tree = commit.get("tree", {}).get("sha")
    elif st not in (404, 409):
        print(f"读取仓库失败：HTTP {st} {ref.get('message','')}")
        return 1

    # 2) 空仓库：先用 Contents API 建一个初始提交（git data API 在空仓库上会 409）
    if not parent:
        boot = None
        for rel, path in files:
            if rel == ".gitignore":
                boot = (rel, path); break
        if boot is None:
            boot = files[0]
        raw = open(boot[1], "rb").read()
        st_b, res_b = api("PUT", f"/repos/{args.owner}/{args.repo}/contents/{boot[0]}", token,
                          {"message": "initial commit",
                           "content": base64.b64encode(raw).decode("ascii"),
                           "branch": args.branch})
        if st_b not in (200, 201):
            print(f"初始化空仓库失败：HTTP {st_b} {res_b.get('message','')}")
            return 1
        # 刚建好的提交可能有短暂延迟，重试几次
        for attempt in range(5):
            st, ref = api("GET", f"/repos/{args.owner}/{args.repo}/git/ref/heads/{args.branch}", token)
            if st == 200:
                break
            time.sleep(1.2)
        if st != 200:
            print(f"读取初始提交失败：HTTP {st}（可稍后重新运行本脚本）")
            return 1
        parent = ref["object"]["sha"]
        st2, commit0 = api("GET", f"/repos/{args.owner}/{args.repo}/git/commits/{parent}", token)
        base_tree = commit0.get("tree", {}).get("sha") if st2 == 200 else None

    # 3) 与远端比较，只上传新增/修改的文件
    old = remote_tree(token, args.owner, args.repo, base_tree) if base_tree else {}
    local = {}
    added, changed, same = [], [], []
    tree_items = []
    skipped = 0
    for i, (rel, path) in enumerate(files, 1):
        raw = open(path, "rb").read()
        local[rel] = blob_sha(raw)
        if rel in old and old[rel] == local[rel]:
            same.append(rel)
            continue                          # 内容没变：沿用远端 shas
        st, blob = api("POST", f"/repos/{args.owner}/{args.repo}/git/blobs", token,
                       {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"})
        if st not in (200, 201):
            print(f"上传失败：{rel} → HTTP {st} {blob.get('message','')}")
            return 1
        tree_items.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        (added if rel not in old else changed).append(rel)
        skipped += 1
        if skipped % 10 == 0:
            print(f"   已上传 {skipped} 个")

    # 远端有、本地没有 → 删除
    removed = [p for p in old if p not in local]
    for p in removed:
        tree_items.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    print(f"新增 {len(added)} | 修改 {len(changed)} | 删除 {len(removed)} | 未变 {len(same)}")
    for p in (added[:6] + changed[:6]):
        print(f"   + {p}")
    if removed:
        for p in removed[:6]:
            print(f"   - {p}")

    if not tree_items:
        print("已是最新，无需提交 ✅")
        return 0

    # 3) 建 tree
    payload = {"tree": tree_items}
    if base_tree:
        payload["base_tree"] = base_tree
    st, tree = api("POST", f"/repos/{args.owner}/{args.repo}/git/trees", token, payload)
    if st not in (200, 201):
        print(f"创建目录树失败：HTTP {st} {tree.get('message','')}")
        return 1

    # 4) 建 commit
    cpayload = {"message": msg, "tree": tree["sha"], "parents": [parent] if parent else []}
    st, commit = api("POST", f"/repos/{args.owner}/{args.repo}/git/commits", token, cpayload)
    if st not in (200, 201):
        print(f"创建提交失败：HTTP {st} {commit.get('message','')}")
        return 1

    # 5) 更新分支（空仓库需要创建 ref）
    if parent:
        st, _ = api("PATCH", f"/repos/{args.owner}/{args.repo}/git/refs/heads/{args.branch}",
                    token, {"sha": commit["sha"], "force": True})
    else:
        st, _ = api("POST", f"/repos/{args.owner}/{args.repo}/git/refs", token,
                    {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})
    if st not in (200, 201):
        print(f"更新分支失败：HTTP {st}")
        return 1

    print("\n完成 ✅")
    print(f"提交：https://github.com/{args.owner}/{args.repo}/commit/{commit['sha'][:10]}")
    print(f"仓库：https://github.com/{args.owner}/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
