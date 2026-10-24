# Mail Adagio

An agent that sorts your Gmail. Each mail gets **one theme** (Finance, Travel, …) and **one status**
(Important, To Do, Info). The AI model only chooses the two tags; plain code does everything else.
It **never deletes** a mail.

> **Current version: step 3.** The agent sorts your inbox by batches. For each batch it shows a plan;
> you untick what you want to keep as is, then apply. Each mail gets its theme and status labels and
> leaves the Inbox (it stays in its theme label, like a folder). The last batch can always be undone.

## Install (5 minutes)

You need Python 3.10 or newer.

1. **Turn on IMAP in Gmail**: Gmail → ⚙ Settings → See all settings → *Forwarding and POP/IMAP* →
   *Enable IMAP* → Save.
2. **Create an app password**: turn on 2-step verification on your Google account, then open
   <https://myaccount.google.com/apppasswords>, create one named "Mail Adagio" and copy the 16 letters.
3. **Install and set up**:

```bash
git clone https://github.com/PBdan-dev/Mail_Adagio.git
cd Mail_Adagio
pip install -r requirements.txt
python adagio.py setup
```

**On Windows, the simplest way**: double-click `Mail Adagio.bat`. The first time, it prepares a private
Python environment and installs the libraries. Then it opens Mail Adagio in your browser, where you
enter your details and run the sort. Keep the black window open while you use it.
`Mail Adagio - demo.bat` opens the same interface with made-up mails, no Gmail account needed.
Python must be installed (<https://www.python.org/downloads/>).

`setup` asks for your Gmail address, the app password (hidden while you type), the model provider
and its API key. Everything is saved in `config.yaml`, on your computer only (it is in `.gitignore`).

## Use

```bash
python adagio.py ui                     # web interface in your browser (127.0.0.1 only)
python adagio.py ui --demo              # same, with made-up mails
python adagio.py check                  # test the Gmail connection
python adagio.py sort --newest 500      # sort by batches, asks before each batch
python adagio.py sort --all --yes       # whole inbox, applies every batch on its own
python adagio.py undo                   # undo the last applied batch
python adagio.py preview --limit 20     # classify the 20 newest mails, change nothing
python adagio.py preview --since 7      # mails from the last 7 days
python adagio.py hardware               # which local model fits this computer
```

From another program (Orchestra, a chatbot): send one JSON request, read one JSON result.

```bash
echo '{"action": "preview", "limit": 5}' | python adagio.py call
echo '{"action": "plan", "scope": {"kind": "count", "value": 200}}' | python adagio.py call
echo '{"action": "apply", "batch_id": 1, "skip": []}' | python adagio.py call
echo '{"action": "scan"}' | python adagio.py call
```

## How sorting works (step 3)

1. **Choose what to sort**: the newest N mails, the last N days, or the whole inbox, and the batch size
   (50 to 1000). Before starting, the agent shows the number of mails, batches, time and cost.
2. **Plan**: the agent reads one batch and proposes a theme and a status for each mail. Nothing changes
   in Gmail yet. Untick a mail or a whole theme to leave it as it is; click the pencil to correct one.
3. **Apply**: the theme and status labels are added and the mail leaves the Inbox. Your own labels stay.
4. **Undo**: removes the labels the agent added and puts the mails back in the Inbox.

With **Let the agent act alone** (or `act_alone: true`), batches are applied one after the other
without asking. Progress is saved in `adagio.db`: after a stop or a crash, it resumes where it was.
Gmail reserves the name *Important*, so that status uses the label **Priority** (change it with
`label:` in `config.yaml`). Set `archive: false` to keep the mails in the Inbox.

## Senders: subscriptions and ready-made rules

The **Senders** tab of the interface:

1. **Scan** reads only the sender, date and unsubscribe headers of every mail (All Mail, or the Inbox).
   No AI, nothing changes, a few seconds for thousands of mails.
2. **Subscriptions**: every sender with an unsubscribe link (the `List-Unsubscribe` header, or a link
   in the mail). **Unsubscribe** only opens that link in your browser; the agent never unsubscribes on
   its own. Hide a sender you want to keep.
3. **Suggest rules**: for the 20 senders who write the most (5 mails or more, no rule yet), the agent
   classifies 3 of their mails and proposes a rule ("16 mails from Jobs Digest -> Job Search / Info").
   **Accept** or **Change** it; the rule then sorts their mails without calling the model.

The contract (input and output) is in [`agent.json`](agent.json). Errors come back as
`{"ok": false, "error": "..."}`: the caller never crashes because of the agent.

## Choose the model

In `config.yaml`, section `model`:

| provider     | model_name example          | api_key           | notes |
|--------------|-----------------------------|-------------------|-------|
| `anthropic`  | `claude-haiku-5-5`          | Anthropic key     | default, fast and cheap |
| `openrouter` | `anthropic/claude-haiku-5.5`| OpenRouter key    | any model OpenRouter offers |
| `ollama`     | `""` (auto) or `qwen3.6:27b`| none              | runs on your computer, free |

With Ollama and an empty `model_name`, the agent reads your graphics memory and picks a model from
the `local_models` table. `python adagio.py hardware` shows what it found and why it chose that model.

## Your own themes

Edit `themes` in `config.yaml`: add, rename or remove entries. The `hint` tells the model what belongs
in the theme. Keep one theme named `Other` (used when nothing fits). Same for `statuses`.

## Files

```
adagio.py            command line (door 1) and JSON mode (door 2)
agent/core.py        handle(request) -> result: the single entry point
agent/mail.py        Gmail over IMAP: read, add/remove labels (never deletes)
agent/batches.py     progress of the sort and what was done to each mail, in adagio.db
agent/classify.py    prompt, answer check, retry, fallback
agent/models.py      Anthropic, OpenRouter, Ollama adapters
agent/hardware.py    graphics memory detection, local model choice
agent/config.py      config.yaml loading and first-run setup
agent/ui.py          terminal display
agent/web.py         local web server (127.0.0.1 only) for the interface
agent/web/index.html the interface: one page, no external files
agent/demo.py        made-up mails for the demo mode
agent/memory.py      what you taught it: rules and corrections, kept in adagio.db
agent/senders.py     who writes to you: counts, unsubscribe links, suggested rules, in adagio.db
tests/               python -m unittest   (fake Gmail, fake model)
```

## Privacy

- The agent reads the start of each mail (1500 characters by default, `body_chars`) and sends it to the
  model you chose. With Ollama, nothing leaves your computer.
- It does not store mail content. From step 3, only the mail id, subject and tags go into a local
  SQLite file, to resume after a stop and to undo the last run. The Senders page stores each sender's
  address, name, mail count and unsubscribe link.
