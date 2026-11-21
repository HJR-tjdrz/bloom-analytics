"""Flask API：为看板提供 JSON 数据。

为什么 API 不直接拼 HTML？
------------------------
前后端分离：Flask 只返回 JSON，前端用 ECharts 渲染。
好处：同一套接口既能给 Web 看板用，也能给 Tableau / Power BI / 手机端复用；
坏处（也是取舍）：多了一次 HTTP 往返，但换来的是可复用性与职责清晰。
"""
from __future__ import annotations

import time
from functools import wraps
from typing import Any, Callable, Dict

from flask import Flask, jsonify, request, send_from_directory

from ..config import WEB_DIR
from ..logger import get_logger
from . import queries

log = get_logger("api")

# 简单进程内缓存：看板切页会重复请求同一接口，缓存 5 分钟足够，
# 也避免每次刷新都去扫 11 万行事实表。
_CACHE: Dict[str, tuple[float, Any]] = {}
_TTL = 300


def cached(fn: Callable) -> Callable:
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = f"{fn.__name__}:{sorted(kwargs.items())}"
        now = time.time()
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
        value = fn(*args, **kwargs)
        _CACHE[key] = (now, value)
        return value

    return wrapper


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False

    # ---------------- 静态资源 ----------------
    @app.get("/")
    def index():
        return send_from_directory(WEB_DIR, "index.html")

    @app.get("/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(WEB_DIR, filename)

    # ---------------- 接口 ----------------
    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})

    @app.get("/api/meta")
    def meta():
        return jsonify(queries.meta())

    @app.get("/api/overview")
    def overview():
        start = request.args.get("start") or None
        end = request.args.get("end") or None
        channels = request.args.getlist("channel")
        stores = request.args.getlist("store")
        return jsonify(queries.overview(start, end, channels, stores))

    @app.get("/api/rfm")
    def rfm():
        return jsonify(queries.rfm())

    @app.get("/api/product")
    def product():
        return jsonify(queries.product())

    @app.get("/api/promo")
    def promo():
        return jsonify(queries.promo())

    @app.get("/api/channel")
    def channel():
        return jsonify(queries.channel())

    @app.get("/api/inventory")
    def inventory():
        return jsonify(queries.inventory())

    @app.get("/api/forecast")
    def forecast():
        return jsonify(queries.forecast())

    @app.errorhandler(Exception)
    def on_error(exc: Exception):  # pragma: no cover
        log.exception("接口异常")
        return jsonify({"error": str(exc)}), 500

    log.info("Flask 应用已创建，静态目录：%s", WEB_DIR)
    return app
