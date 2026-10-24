# Mail Adagio

An agent that sorts your Gmail. Each mail gets **one theme** (Finance, Travel, …) and **one status**
(Important, To Do, Info). The AI model only chooses the two tags; plain code does everything else.
It **never deletes** a mail.

> **Current version: steps 1 and 2, read-only.** The agent reads and classifies your mails and shows
> the result. Nothing is changed in Gmail yet. Labels, moves, batches and undo come in step 3.

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

`setup` asks for your Gmail address, the app password (hidden while you type), the model provider
and its API key. Everything is saved in `config.yaml`, on your computer only (it is in `.gitignore`).

## Use

```bash
python adagio.py check                  # test the Gmail connection
python adagio.py preview --limit 20     # classify the 20 newest mails, change nothing
python adagio.py preview --since 7      # mails from the last 7 days
python adagio.py hardware               # which local model fits this computer
```

From another program (Orchestra, a chatbot): send one JSON request, read one JSON result.

```bash
echo '{"action": "preview", "limit": 5}' | python adagio.py call
```

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
agent/mail.py        Gmail over IMAP, read-only
agent/classify.py    prompt, answer check, retry, fallback
agent/models.py      Anthropic, OpenRouter, Ollama adapters
agent/hardware.py    graphics memory detection, local model choice
agent/config.py      config.yaml loading and first-run setup
agent/ui.py          terminal display
tests/               python -m unittest   (fake Gmail, fake model)
```

## Privacy

- The agent reads the start of each mail (1500 characters by default, `body_chars`) and sends it to the
  model you chose. With Ollama, nothing leaves your computer.
- It does not store mail content. From step 3, only the mail id, subject and tags go into a local
  SQLite file, to resume after a stop and to undo the last run.
