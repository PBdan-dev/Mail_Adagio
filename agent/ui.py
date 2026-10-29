"""Terminal display. Pure presentation: it only reads the dicts returned by core.handle()."""
import os
import shutil
import sys

CONSOLE = sys.stdout is not None  # None when started without a console (pythonw)


def action_console_colors():
    """Turn on colour codes in the Windows terminal (without a console it would open one)."""
    if os.name == "nt" and CONSOLE:
        os.system("")


action_console_colors()
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COLOR = CONSOLE and sys.stdout.isatty() and not os.environ.get("NO_COLOR")
PALETTE = [75, 114, 179, 168, 141, 80, 215, 110, 186, 204, 145, 109]
STATUS_COLOR = {0: 203, 1: 221, 2: 246}  # first status red, second amber, others grey


def c(text, code=None, bold=False, dim=False):
    if not COLOR:
        return str(text)
    style = []
    if bold:
        style.append("1")
    if dim:
        style.append("2")
    if code is not None:
        style.append(f"38;5;{code}")
    return f"\033[{';'.join(style)}m{text}\033[0m" if style else str(text)


def width():
    return min(shutil.get_terminal_size((100, 20)).columns, 120)


def fit(text, n):
    text = str(text)
    return text if len(text) <= n else text[:max(n - 1, 0)] + "…"


def header(action, subtitle=""):
    title = c("◆ Mail Adagio", 75, bold=True)
    extra = c(f"  ·  {action}" + (f"  ·  {subtitle}" if subtitle else ""), dim=True)
    print(f"\n  {title}{extra}")
    print("  " + c("─" * (width() - 4), dim=True))


def field(label, value):
    print(f"  {c(f'{label:<9}', dim=True)}{value}")


def success(text):
    print(f"\n  {c('✓', 114, bold=True)} {text}\n")


def error(text):
    print(f"\n  {c('✗', 203, bold=True)} {text}\n", file=sys.stderr)


def progress(i, total, row, theme_colors):
    bar_w = 24
    done = int(bar_w * i / max(total, 1))
    bar = c("█" * done, 75) + c("░" * (bar_w - done), dim=True)
    tag = c(fit(row["theme"], 22), theme_colors.get(row["theme"]))
    line = f"  {bar}  {i}/{total}  {tag}"
    end = "\n" if i == total else ""
    print("\r\033[K" + line if COLOR else line, end=end if COLOR else "\n", flush=True)


def theme_colors(cfg):
    return {t["name"]: PALETTE[i % len(PALETTE)] for i, t in enumerate(cfg["themes"])}


def show_check(result):
    header("connection check")
    field("Account", result["email"])
    field("Mailbox", f"{result['mailbox']} · {result['total_in_mailbox']:,} mails".replace(",", " "))
    success("Connected to Gmail (read-only).")


def show_hardware(result):
    header("hardware")
    hw = result["hardware"]
    field("GPU", hw["gpu"] or "none found")
    field("VRAM", f"{hw['vram_gb']} GB" if hw["vram_gb"] else "–")
    field("RAM", f"{hw['ram_gb']} GB" if hw["ram_gb"] else "unknown")
    field("Model", c(result["suggested_local_model"] or "none", 114, bold=True))
    field("Why", result["reason"])
    print()


def show_preview_intro(model_note, limit):
    header("preview", "read-only, nothing is changed")
    field("Model", model_note)
    field("Reading", f"{limit} most recent mails")
    print()


def show_preview(result, cfg):
    colors = theme_colors(cfg)
    statuses = [s["name"] for s in cfg["statuses"]]
    w = width()
    theme_w, date_w = 22, 10
    sender_w = max(14, (w - theme_w - date_w - 12) * 2 // 5)
    subject_w = max(14, w - theme_w - date_w - sender_w - 12)

    for idx, status in enumerate(statuses):
        rows = [r for r in result["mails"] if r["status"] == status]
        if not rows:
            continue
        color = STATUS_COLOR.get(idx, 246)
        print(f"\n  {c(status.upper(), color, bold=True)} {c(f'({len(rows)})', dim=True)}")
        for r in rows:
            dot = c("●", colors.get(r["theme"]))
            theme = c(f"{fit(r['theme'], theme_w - 2):<{theme_w - 2}}", colors.get(r["theme"]))
            sender = fit(short_sender(r["sender"]), sender_w)
            subject = fit(r["subject"] or "(no subject)", subject_w)
            flag = c(" ?", 203) if not r["classified"] else "  "
            print(f"  {dot} {theme}  {sender:<{sender_w}}  {subject:<{subject_w}}"
                  f"  {c(r['date'][5:] if r['date'] else '', dim=True)}{flag}")

    print(f"\n  {c('By theme', bold=True)}")
    top = max(result["by_theme"].values(), default=1)
    for theme, n in result["by_theme"].items():
        bar = c("█" * max(1, round(18 * n / top)), colors.get(theme))
        print(f"    {theme:<24}{bar} {n}")

    note = f"{result['processed']} mails read and sorted"
    if result["fallbacks"]:
        note += c(f" · {result['fallbacks']} could not be classified (marked ?)", 203)
    note += c(f" · nothing changed in Gmail · {result['seconds']} s", dim=True)
    success(note)


def short_sender(sender):
    """'Bank Name <no-reply@bank.com>' -> 'Bank Name'"""
    name = sender.split("<")[0].strip().strip('"')
    return name or sender.strip("<>")
