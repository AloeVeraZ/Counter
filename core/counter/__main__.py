import argparse
import atexit
import os
from pathlib import Path
import signal
from .config import ConfigStore
from .controller import Counter
from .hardware import PCA9685, SimulatedBoard
from .web import create_app


def main():
    parser = argparse.ArgumentParser(description="Counter mechanical digit dashboard")
    parser.add_argument("--simulate", action="store_true", help="Preview without a Raspberry Pi or servos")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("COUNTER_CONFIG", str(Path.home() / ".counter" / "config.json"))))
    args = parser.parse_args()
    store = ConfigStore(args.config)
    controller = Counter(store, SimulatedBoard() if args.simulate else PCA9685())
    atexit.register(controller.close)

    def shutdown(signum, frame):
        controller.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    from waitress import serve
    print(f"Counter at http://{args.host}:{args.port} · {'SIMULATION' if args.simulate else 'PCA9685 hardware'}", flush=True)
    serve(create_app(controller), host=args.host, port=args.port, threads=4)


if __name__ == "__main__":
    main()
