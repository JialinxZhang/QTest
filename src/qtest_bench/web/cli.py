from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

from ..adapter import load_adapter
from ..contracts import QTestError, read_json, require, validate_device
from .store import Conflict, Store, audit, encode, label


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qtest-web", description="QTest laboratory Web management")
    parser.add_argument("--data", default="web-data", help="Local-disk data directory shared by API and Worker")
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("init", help="Initialize data and interactively create the first administrator")
    item.add_argument("--username", default="admin")
    item = sub.add_parser("user-add", help="Interactively create an account")
    item.add_argument("username")
    item.add_argument("--role", choices=("admin", "operator", "viewer"), default="operator")
    item = sub.add_parser("password-reset", help="Reset an account password interactively and revoke its sessions")
    item.add_argument("username")
    item = sub.add_parser("device-add", help="Register a trusted installed adapter; configuration stays server-side")
    item.add_argument("id")
    item.add_argument("--name", required=True)
    item.add_argument("--adapter", required=True)
    item.add_argument("--config")
    item = sub.add_parser("serve", help="Run the API and static UI; start the worker separately")
    item.add_argument("--host", default="127.0.0.1")
    item.add_argument("--port", type=int, default=8765)
    item.add_argument("--origin", default="http://127.0.0.1:8765", help="Exact browser-facing origin; LAN requires HTTPS")
    item = sub.add_parser("worker", help="Run the single persistent task executor")
    item.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        store = Store(args.data)
        if args.command == "init":
            store.initialize()
            with store.db() as conn:
                exists = conn.execute("SELECT 1 FROM users WHERE role='admin' AND enabled=1").fetchone()
            if not exists:
                from .auth import add_user
                password = getpass.getpass("Administrator password (12+ characters): ")
                require(password == getpass.getpass("Repeat password: "), "Passwords differ")
                add_user(store, args.username, password, "admin")
            print(f"Initialized: {store.root}")
        else:
            require(store.path.exists(), "请先执行 qtest-web init")
            if args.command in ("user-add", "password-reset"):
                from .auth import add_user, update_user
                password = getpass.getpass("Password (12+ characters): ")
                require(password == getpass.getpass("Repeat password: "), "Passwords differ")
                if args.command == "user-add":
                    add_user(store, args.username, password, args.role)
                else:
                    with store.db() as conn:
                        user = conn.execute("SELECT id,role,enabled FROM users WHERE username=?", (args.username,)).fetchone()
                    require(user is not None, "账号不存在")
                    update_user(store, user["id"], {"role": user["role"], "enabled": bool(user["enabled"]), "password": password}, "server-admin")
                print("Account updated")
            elif args.command == "device-add":
                import re
                require(bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.id)), "无效设备 ID")
                config_path = str(Path(args.config).resolve()) if args.config else None
                adapter = load_adapter(args.adapter, read_json(config_path) if config_path else {})
                descriptor = adapter.describe()
                validate_device(descriptor)
                with store.db() as conn:
                    require(not conn.execute("SELECT 1 FROM devices WHERE id=?", (args.id,)).fetchone(), "设备 ID 已存在；配置变更请注册新的 ID")
                    conn.execute("INSERT INTO devices(id,name,adapter,config_path,descriptor) VALUES(?,?,?,?,?)", (args.id, label(args.name), args.adapter, config_path, encode(descriptor)))
                    audit(conn, "server-admin", "device.register", args.id)
                print("Device registered")
            elif args.command == "serve":
                import uvicorn
                from .app import create_app
                require(not args.origin.startswith("http://") or args.host in ("127.0.0.1", "localhost", "::1"), "HTTP 预览只能绑定回环地址；内网请使用 HTTPS 反向代理")
                uvicorn.run(create_app(store.root, args.origin), host=args.host, port=args.port, proxy_headers=False)
            else:
                from .worker import main_worker
                main_worker(store.root, args.once)
        return 0
    except (QTestError, Conflict, OSError) as exc:
        print(f"qtest-web: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
