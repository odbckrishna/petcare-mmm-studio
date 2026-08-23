"""Start the Petcare MMM Studio locally.

Usage:  python run.py            (then open http://127.0.0.1:8050)
        python run.py --port 9000
"""
import argparse

import uvicorn

BANNER = r"""
  ____       _                        __  __ __  __ __  __   ____  _             _ _
 |  _ \ ___ | |_ ___ __ _ _ __ ___   |  \/  |  \/  |  \/  | / ___|| |_ _   _  __| (_) ___
 | |_) / _ \| __/ __/ _` | '__/ _ \  | |\/| | |\/| | |\/| | \___ \| __| | | |/ _` | |/ _ \
 |  __/  __/| || (_| (_| | | |  __/  | |  | | |  | | |  | |  ___) | |_| |_| | (_| | | (_) |
 |_|   \___| \__\___\__,_|_|  \___|  |_|  |_|_|  |_|_|  |_| |____/ \__|\__,_|\__,_|_|\___/
"""

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Petcare MMM Studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    args = parser.parse_args()
    print(BANNER)
    print(f"  Petcare MMM Studio  ->  http://{args.host}:{args.port}")
    print("  Your data stays on this machine. Press Ctrl+C to stop.\n")
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
