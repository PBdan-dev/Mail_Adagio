"""Mail Adagio: sorts your Gmail with an AI model. Run `python adagio.py --help`."""
import argparse
import json
import sys

from agent import config, core, ui

EXAMPLES = """examples:
  python adagio.py ui                    open the web interface (default for the .bat)
  python adagio.py ui --demo             same, with made-up mails, no Gmail needed
  python adagio.py setup                 first run: Gmail address, app password, model
  python adagio.py check                 test the Gmail connection
  python adagio.py preview --limit 20    classify the 20 newest mails, change nothing
  python adagio.py hardware              which local model fits this computer
  echo '{"action":"preview","limit":5}' | python adagio.py call    JSON in, JSON out
"""


def main(argv=None):
    parser = argparse.ArgumentParser(prog="adagio", description="Mail Adagio, Gmail sorting agent.",
                                     epilog=EXAMPLES, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--config", default=str(config.DEFAULT_PATH), help="path to config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="create or complete config.yaml")
    sub.add_parser("check", help="test the Gmail connection")
    sub.add_parser("hardware", help="show the local model chosen for this computer")
    p = sub.add_parser("preview", help="read and classify recent mails (read-only)")
    p.add_argument("--limit", type=int, default=20, help="how many recent mails (default 20)")
    p.add_argument("--since", type=int, metavar="DAYS", help="only mails from the last DAYS days")
    p.add_argument("--json", action="store_true", help="print the JSON result instead")
    sub.add_parser("call", help="read one JSON request on stdin, write the JSON result on stdout")
    u = sub.add_parser("ui", help="open the web interface in your browser")
    u.add_argument("--demo", action="store_true", help="made-up mails, no Gmail account needed")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args(argv)

    if args.command == "ui":
        from agent import web
        if args.demo:
            core.LOG_PATH = config.ROOT / "adagio-demo.log"
        web.serve(args.config, port=args.port, demo_mode=args.demo, open_browser=not args.no_browser)
        return 0

    if args.command == "call":
        return call_mode(args.config)

    try:
        if args.command == "setup" or (args.command in ("check", "preview")
                                        and config.needs_setup(args.config)):
            if args.command != "setup":
                ui.header("first run")
            config.setup(args.config, say=lambda s: print("  " + s))
            if args.command == "setup":
                ui.success("Setup done. Next: python adagio.py check")
                return 0
    except (config.ConfigError, KeyboardInterrupt, EOFError) as e:
        ui.error(str(e) or "Cancelled")
        return 1

    if args.command == "hardware":
        result = core.handle({"action": "hardware"}, cfg=load_quietly(args.config))
        return finish(result, ui.show_hardware)

    if args.command == "check":
        return finish(core.handle({"action": "check", "config": args.config}), ui.show_check)

    # preview
    request = {"action": "preview", "limit": args.limit, "since_days": args.since,
               "config": args.config}
    if args.json:
        return print_json(core.handle(request))
    try:
        cfg = config.load(args.config)
        _, note = core.resolve_model(cfg)
    except Exception as e:
        ui.error(str(e))
        return 1
    ui.show_preview_intro(note, args.limit)
    colors = ui.theme_colors(cfg)
    result = core.handle(request, cfg=cfg,
                         progress=lambda i, n, row: ui.progress(i, n, row, colors))
    return finish(result, lambda r: ui.show_preview(r, cfg))


def finish(result, show):
    if not result["ok"]:
        ui.error(result["error"])
        return 1
    show(result)
    return 0


def load_quietly(path):
    try:
        return config.load(path)
    except config.ConfigError:
        return None


def call_mode(config_path):
    """Door 2: another program sends a JSON request and reads a JSON answer."""
    try:
        request = json.loads(sys.stdin.read() or "{}")
        request.setdefault("config", config_path)
        result = core.handle(request)
    except json.JSONDecodeError as e:
        result = {"ok": False, "error": f"Invalid JSON request: {e}", "agent": core.AGENT}
    return print_json(result)


def print_json(result):
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
