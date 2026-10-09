"""Small, offline-friendly local-network dashboard."""
import secrets
from urllib.parse import urlsplit
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
from . import __version__
from .updates import Updates


def create_app(controller, updates=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    token = secrets.token_urlsafe(32)
    updater = updates or Updates()

    @app.before_request
    def protect_commands():
        if request.method == "POST":
            if not secrets.compare_digest(request.headers.get("X-Counter-Token", ""), token):
                return jsonify(error="Reload the dashboard before sending commands."), 403
            origin = request.headers.get("Origin")
            expected = urlsplit(request.host_url)
            if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (expected.scheme, expected.netloc):
                return jsonify(error="Commands must come from this dashboard."), 403
            if not request.is_json:
                return jsonify(error="Commands must use JSON."), 415

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(RuntimeError)
    @app.errorhandler(OSError)
    def hardware_error(error):
        try:
            controller.stop()
        except Exception:
            pass
        return jsonify(error=str(error)), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    def body(allowed, required=()):
        data = request.get_json()
        if not isinstance(data, dict) or set(data) - set(allowed) or not set(required) <= set(data):
            raise ValueError("Missing or unknown command fields.")
        return data

    @app.get("/")
    def index():
        return render_template("dashboard.html", token=token, version=__version__)

    @app.get("/api/state")
    def state():
        return jsonify({**controller.snapshot(), "version": __version__,
                        "instance": token[:12],
                        "installed_commit": getattr(updater, "installed", "")})

    @app.post("/api/arm")
    def arm():
        body([])
        controller.arm()
        return state()

    @app.post("/api/stop")
    def stop():
        body([])
        controller.stop()
        return state()

    @app.post("/api/number")
    def number():
        controller.show(body(["number"], ["number"])["number"])
        return state()

    @app.post("/api/step")
    def step():
        controller.step(body(["delta"], ["delta"])["delta"])
        return state()

    @app.post("/api/setup")
    def setup():
        controller.configure(body(["count", "settle_ms", "release_after_move"]))
        return state()

    @app.post("/api/calibration")
    def calibration():
        data = body(["channel", "positions"], ["channel", "positions"])
        controller.calibrate(data["channel"], data["positions"])
        return state()

    @app.post("/api/preview")
    def preview():
        data = body(["channel", "pulse_us"], ["channel", "pulse_us"])
        controller.preview(data["channel"], data["pulse_us"])
        return state()

    @app.get("/api/updates")
    def updates_state():
        return jsonify(updater.snapshot(refresh=request.args.get("refresh") == "1"))

    @app.post("/api/updates")
    def update_now():
        body([])
        controller.stop()
        return jsonify(message=updater.start())

    return app
