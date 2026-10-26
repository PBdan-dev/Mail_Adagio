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
  python adagio.py sort --newest 500     sort by batches: shows each plan, asks before changing Gmail
  python adagio.py sort --all --batch 200 --yes    whole inbox, no questions (act alone)
  python adagio.py undo                  undo the last applied batch
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
    srt = sub.add_parser("sort", help="sort the inbox by batches (labels + out of the Inbox)")
    which = srt.add_mutually_exclusive_group()
    which.add_argument("--newest", type=int, metavar="N", help="the N newest mails (default 100)")
    which.add_argument("--days", type=int, metavar="N", help="mails of the last N days")
    which.add_argument("--all", action="store_true", help="the whole mailbox")
    srt.add_argument("--batch", type=int, help="mails per batch, 50 to 1000 (default from config.yaml)")
    srt.add_argument("--yes", action="store_true", help="act alone: apply every batch without asking")
    sub.add_parser("undo", help="undo the last applied batch")
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
            core.MEMORY_PATH = config.ROOT / "adagio-demo.db"
            for f in (core.LOG_PATH, core.MEMORY_PATH):  # the made-up mailbox starts fresh each time
                f.unlink(missing_ok=True)
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

    if args.command == "undo":
        return finish(core.handle({"action": "undo", "config": args.config}),
                      lambda r: ui.success(r["log"].replace("undo: ", "Undone: ")))

    if args.command == "sort":
        return sort(args)

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
                         progress=lambda i, n, row: i and ui.progress(i, n, row, colors))
    return finish(result, lambda r: ui.show_preview(r, cfg))


def sort(args):
    """Door 1 for step 3: estimate, then batch after batch, asking before each change."""
    try:
        cfg = config.load(args.config)
        _, note = core.resolve_model(cfg)
    except Exception as e:
        ui.error(str(e))
        return 1
    scope = ({"kind": "all"} if args.all else {"kind": "days", "value": args.days} if args.days
             else {"kind": "count", "value": args.newest or 100})
    base = {"scope": scope, "batch_size": args.batch or cfg["behavior"]["batch_size"]}
    act_alone = args.yes or cfg["behavior"]["act_alone"]
    est = core.handle({"action": "estimate", **base}, cfg=cfg)
    if not est["ok"]:
        ui.error(est["error"])
        return 1
    ui.header("sort", "acts alone" if act_alone else "asks before each batch")
    ui.field("Model", note)
    ui.field("To sort", f"{est['mails']} mails, {est['batches']} batches of {est['batch_size']}")
    ui.field("Estimate", f"about {max(1, round(est['duration_s'] / 60))} min · {est['cost_note']}")
    colors = ui.theme_colors(cfg)
    while True:
        print()
        r = core.handle({"action": "plan", **base, "act_alone": act_alone}, cfg=cfg,
                        progress=lambda i, n, row: i and ui.progress(i, n, row, colors))
        if not r["ok"]:
            ui.error(r["error"])
            return 1
        p = r.get("progress") or {}
        if act_alone:
            if r.get("applied") is not None:
                ui.success(f"Batch {p['batch_no'] - 1} of {p['batches']}: {r['applied']} labelled, "
                           f"{r['moved']} out of the Inbox · {p['done']} of {p['total']} done")
            if not r.get("applied") and not r.get("plan") or p.get("finished"):
                break
            continue
        if not r.get("plan"):
            break
        plan = {**r["plan"], "seconds": r["seconds"]}
        ui.show_preview(plan, cfg)
        try:
            answer = input(f"\n  Batch {p['batch_no']} of {p['batches']}: apply to these {plan['processed']} mails? "
                           "[y]es / [n]o, forget this plan / [q]uit, keep it for later: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "q"
        if answer.startswith("y"):
            done = core.handle({"action": "apply", "batch_id": plan["batch_id"], **base}, cfg=cfg)
            if not done["ok"]:
                ui.error(done["error"])
                return 1
            ui.success(done["log"].replace("apply: ", "Applied: "))
            if done["progress"]["finished"]:
                break
        elif answer.startswith("n"):
            core.handle({"action": "discard"}, cfg=cfg)
            ui.success("Plan forgotten, nothing changed in Gmail.")
            return 0
        else:
            ui.success("Stopped. The plan is kept: run the same command to see it again.")
            return 0
    ui.success("All done. Undo the last batch with: python adagio.py undo")
    return 0


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
        result = core.handle(request, door="json")  # trash, restore, renaming: refused, window only
    except json.JSONDecodeError as e:
        result = {"ok": False, "error": f"Invalid JSON request: {e}", "agent": core.AGENT}
    return print_json(result)


def print_json(result):
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
