"""Small, offline-friendly local-network dashboard."""
import secrets
from datetime import timedelta
from urllib.parse import urlsplit
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from . import __version__
from .updates import Updates, update_job
from .auth import LoginThrottle


def create_app(controller, updates=None, *, auth=None, secret_key=None):
    if auth is None and not controller.board.simulated:
        raise ValueError("Hardware mode requires Pi password authentication.")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    token = secrets.token_urlsafe(32)
    updater = updates or Updates()
    if auth is not None:
        if not secret_key:
            raise ValueError("Password authentication requires a session signing key.")
        app.config.update(SECRET_KEY=secret_key, SESSION_COOKIE_HTTPONLY=True,
                          SESSION_COOKIE_SAMESITE="Strict", PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                          SESSION_REFRESH_EACH_REQUEST=False)
    login_throttle = LoginThrottle()

    def same_origin():
        origin = request.headers.get("Origin")
        expected = urlsplit(request.host_url)
        return not origin or (urlsplit(origin).scheme, urlsplit(origin).netloc) == (expected.scheme, expected.netloc)

    @app.before_request
    def protect_commands():
        if auth is not None and request.endpoint not in {"login", "static", "health"}:
            if session.get("user") != auth.username:
                if request.path.startswith("/api/"):
                    return jsonify(error="Enter your Pi password to continue."), 401
                return redirect(url_for("login"))
        if request.endpoint == "login":
            return None
        if request.method == "POST":
            if not secrets.compare_digest(request.headers.get("X-Counter-Token", ""), token):
                return jsonify(error="Reload the dashboard before sending commands."), 403
            if not same_origin():
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

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if auth is None or session.get("user") == auth.username:
            return redirect(url_for("index"))
        error, status, retry_after = "", 200, 0
        if request.method == "POST":
            csrf = session.get("login_csrf", "")
            if not csrf or not secrets.compare_digest(csrf.encode(), request.form.get("csrf", "").encode()) or not same_origin():
                error, status = "Reload this page and try again.", 403
            else:
                retry_after = login_throttle.reserve()
                if retry_after:
                    error, status = "Too many attempts. Wait a minute and try again.", 429
                else:
                    try:
                        accepted = auth.verify(request.form.get("password", ""))
                    except Exception:
                        accepted = False
                        error, status = "Pi password verification is unavailable. Try again later.", 503
                    if accepted:
                        session.clear()
                        session["user"] = auth.username
                        session.permanent = True
                        return redirect(url_for("index"))
                    if not error:
                        error, status = "Incorrect Pi password.", 401
        session.setdefault("login_csrf", secrets.token_urlsafe(32))
        response = app.make_response((render_template("login.html", csrf=session["login_csrf"],
                                                      username=auth.username, error=error, version=__version__), status))
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.post("/api/logout")
    def logout():
        body([])
        controller.stop()
        if auth is not None:
            session.clear()
        return jsonify(message="Signed out. Outputs are stopped.")

    @app.get("/health")
    def health():
        # Installer needs a readiness check before anyone can log in.
        return jsonify(installed_commit=getattr(updater, "installed", ""))

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
        return render_template("dashboard.html", token=token, version=__version__, login_required=auth is not None,
                               login_user=getattr(auth, "username", ""))

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
        controller.configure(body(["count", "settle_ms", "pause_ms", "release_after_move"]))
        return state()

    @app.post("/api/calibration")
    def calibration():
        data = body(["channel", "positions"], ["channel", "positions"])
        controller.calibrate(data["channel"], data["positions"])
        return state()

    @app.post("/api/test-sequence")
    def test_sequence():
        body([])
        tested, skipped = controller.test_sequence()
        message = f"Testing servo{'s' if len(tested) > 1 else ''} {', '.join(map(str, tested))}: 0 up to 9, then back to 0."
        if skipped:
            message += f" Skipped servo{'s' if len(skipped) > 1 else ''} {', '.join(map(str, skipped))} (not all ten numbers set)."
        return jsonify({**controller.snapshot(), "version": __version__, "instance": token[:12],
                        "installed_commit": getattr(updater, "installed", ""), "message": message})

    @app.post("/api/preview")
    def preview():
        data = body(["channel", "pulse_us"], ["channel", "pulse_us"])
        controller.preview(data["channel"], data["pulse_us"])
        return state()

    @app.get("/api/updates")
    def updates_state():
        return jsonify(updater.snapshot(refresh=request.args.get("refresh") == "1"))

    @app.get("/api/updates/job")
    def update_progress():
        # Lets the dashboard notice when the restarted service is a different instance.
        return jsonify({**update_job(), "instance": token[:12],
                        "installed_commit": getattr(updater, "installed", "")})

    @app.post("/api/updates")
    def update_now():
        data = body(["branch", "acknowledge_testing", "password"])
        if auth is not None:
            # Confirm the Pi password again before installing, sharing the login attempt limit.
            password = data.get("password")
            if not password:
                return jsonify(error="Enter the Pi password to install this update.", password_required=True), 403
            retry_after = login_throttle.reserve()
            if retry_after:
                return jsonify(error="Too many attempts. Wait a minute and try again.", password_required=True), 429
            try:
                accepted = auth.verify(password)
            except Exception:
                return jsonify(error="Pi password verification is unavailable. Try again later.", password_required=True), 503
            if not accepted:
                return jsonify(error="That password was not accepted. Try again.", password_required=True), 403
        controller.stop()
        return jsonify(message=updater.start(data.get("branch"), acknowledge_testing=data.get("acknowledge_testing", False)))

    return app
