import argparse
import atexit
import getpass
import os
from pathlib import Path
import signal
from .config import ConfigStore
from .controller import Counter
from .hardware import PCA9685, SimulatedBoard
from .web import create_app
from .auth import PiPasswordAuth, session_key


def main():
    parser = argparse.ArgumentParser(description="Counter mechanical digit dashboard")
    parser.add_argument("--simulate", action="store_true", help="Preview without a Raspberry Pi or servos")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, action="append", help="Listen port; repeat to serve several (default 8080)")
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("COUNTER_CONFIG", str(Path.home() / ".counter" / "config.json"))))
    args = parser.parse_args()
    ports = args.port or [8080]
    auth = None if args.simulate else PiPasswordAuth(os.environ.get("COUNTER_LOGIN_USER", getpass.getuser()))
    key = None if args.simulate else session_key(args.config.with_name("session.key"))
    store = ConfigStore(args.config)
    controller = Counter(store, SimulatedBoard() if args.simulate else PCA9685())
    atexit.register(controller.close)

    def shutdown(signum, frame):
        controller.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    from waitress import serve
    urls = ", ".join(f"http://{args.host}:{port}" for port in ports)
    print(f"Counter at {urls} · {'SIMULATION' if args.simulate else 'PCA9685 hardware'}", flush=True)
    listen = " ".join(f"{args.host}:{port}" for port in ports)
    serve(create_app(controller, auth=auth, secret_key=key), listen=listen, threads=4)


if __name__ == "__main__":
    main()
