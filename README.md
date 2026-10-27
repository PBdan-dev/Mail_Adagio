# Mail Adagio

An agent that sorts your Gmail. Each mail gets **one theme** (Finance, Travel, …) and **one status**
(Important, To Do, Info). The AI model only chooses the two tags; plain code does everything else.
It **never deletes a mail on its own**: only you can send the mails it labels "To delete" to the Trash.

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
enter your details and run the sort. No window stays open: Mail Adagio runs in the background and
**stops when you close its tab** (a reload does not stop it; a sort in progress ends after the mail
in progress, like with Stop). What the window would have shown, startup errors included, goes to
`adagio-window.log`; an error that stops the start also shows in a small message box.
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

The web page works like a mail program: sort settings and the list of statuses and themes (with
counts, click to filter) on the left, the mails in the middle (their own scroll, Sort and Apply always
visible), undo, rules, log and connection on the right. The panel button at the top hides the right
side; on a smaller screen it opens as a drawer. The look follows Workspace ("Aurore": glass panels on
a slowly moving light background), light or dark like Windows; animations stop when Windows asks to
reduce motion.

**Read a mail**: click it. A window shows the whole mail (sender, To, Cc, date, text or formatted
mail, names of the attachments), with the arrows to go to the next or previous one. It is read only:
no reply, and the mail stays unread in Gmail. Pictures from the web load by default (switch **Load
pictures** to stop them: they can tell the sender you opened the mail). In the dark theme the mail is
shown dark too (switch **Dark mail** to see its original colours). Both choices are remembered.

From another program (Orchestra, a chatbot): send one JSON request, read one JSON result.

```bash
echo '{"action": "preview", "limit": 5}' | python adagio.py call
echo '{"action": "plan", "scope": {"kind": "count", "value": 200}}' | python adagio.py call
echo '{"action": "apply", "batch_id": 1, "skip": []}' | python adagio.py call
echo '{"action": "scan"}' | python adagio.py call
echo '{"action": "capabilities"}' | python adagio.py call   # what it can do, for another agent
```

Through this door, **trash**, **restore**, **label_order** and **read** (a whole mail) are refused: a person must click them in
the window. `capabilities` returns the contract (`agent.json`), the allowed actions and those
reserved for the window.

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

## Labels: order, numbers, colours

- **Order and numbers** (Labels in Gmail > Order and numbers): drag the labels into the order you
  want. With numbering on, Gmail lists them in that order: statuses `01 - Priority`, `02 - To Do`,
  `03 - Info`, themes `11 - Health` ... `21 - Promotions`, `97 - To check`, `98 - To delete`, `99 - Other`. Applying
  renames the labels in Gmail (mails and colours stay), `config.yaml` (`label_order:`) and `adagio.db`.
- **Colours**: each theme and status has one of Gmail's label colours (`color:` in `config.yaml`).
  IMAP cannot colour a label, so pick the same colour once in Gmail (the app lists them).

## Low-interest mails and "To delete"

- **Mark low-interest mails as read** (off by default, `mark_read`): mails of the chosen themes
  (Newsletters, Promotions, Social & Notifications by default) with the status Info are marked as read
  when a batch is applied. Undo makes them unread again (only those that were unread).
- **To delete** (`to_delete: true`): the model may propose a mail with no lasting value (expired
  promotion, old notification), only when its status is Info. It gets the label `To delete`
  (`98 - To delete` when numbered); untick it in the plan with ×. The agent never trashes anything on
  its own, even when it acts alone. In the **To delete** card you check the mails in Gmail, then click
  **Move to Trash** and confirm the count. Gmail keeps the Trash 30 days; **Restore** brings the last
  ones back. Nothing is ever deleted for good (no EXPUNGE).

## "To check", reasons and the journal

- **To check** (`97 - To check`): the model says how sure it is (high, medium, low). A mail gets this
  extra label when the model is not sure, when it chose *Other*, or when it gave no valid answer (or
  took too long). Only for these mails, one more short call asks the model for its reason in one
  sentence. A correction (here or in Gmail) removes the mail from the list.
- **Why?** (the ? on a mail): shows who decided (your rule, your correction or the model), how sure
  the model was and its reason. **Ask the model why** reads the mail again for any other mail.
- **Journal** (left menu): every mail sorted, with its sender, subject, rule or model and confidence,
  theme and status, the labels added and the reason for the mails to check; also what it learned.
  Search and filter it. It is saved in `adagio.log`; the activity list on the right keeps one line per
  run.

## Learning from your changes in Gmail

You moved a sorted mail to another theme (or status) directly in Gmail? Before each batch (option
*Learn from my changes in Gmail*, `learn_gmail: true`) or with **Learn from Gmail**, the agent reads
the labels of the mails it sorted (a few seconds, no AI for this step). Each change becomes a
correction: the mail keeps its new theme, and its sender and subject become an example for the model.
After the same correction twice for one sender, a rule is proposed in the **Learning** card: **Make it
a rule** or **Ignore**. Gmail is not changed. Removing the label "To check" in Gmail marks the mail as
checked.

## Keywords learned from your corrections

Each time you correct the **theme** of a mail the agent sorted (pencil in the app, or label changed in
Gmail), the agent learns keywords on its own, before the next batch or with **Learn from Gmail**:

- **Added.** The model reads the corrected mail and names 1 to 3 words that show the right theme
  (a mail "Amazon Prime Day" moved from Shopping to Promotions gives "Prime Day"). The code keeps at most
  2 of them, only if each one is written in the mail, is not generic nor the sender's name ("Amazon"
  sends mails of several themes), is not already a keyword, was not removed before, and is not common
  in the mails sorted in other themes. One short model call per correction, not per mail.
- **Moved or removed.** A keyword of the wrong theme found in 3 corrected mails, and in fewer mails it
  sorted well, is removed; moved to the right theme when all these corrections went to the same theme.
  A single mistake removes nothing. A keyword you put back by hand is never removed again.
- Every change is in the activity log and in the **Learning** card, with the share of mails you
  corrected in the last batches (fewer corrections = better sort). Keywords stay clues for the model,
  never strict rules; see or change them in **Views > Keywords** (at most 40 per theme).

## Gmail's own tabs, as a clue

Gmail already sorts each mail into a tab: Primary, Social, Promotions, Updates, Forums (in French
Gmail: Principale, Réseaux sociaux, Promotions, Notifications, Forums). IMAP does not give the tab as a
label, so Mail Adagio asks Gmail's search (`category:promotions`...), 4 searches per batch, nothing changed.

The tab goes to the model with each mail, as a clue and never as a rule: Promotions and Social are
usually right, Updates mixes bills, deliveries and alerts. The **Learning** card shows, for each tab,
the themes Mail Adagio gave its mails: a quick check of the sort against Gmail's.

## Agent mode: ask in your own words

Type a request in the **Ask Adagio** bar, in any language: "put the Prime Days mails in Promotions",
"how many mails are left to check?", "add URSSAF as an Admin keyword".

1. The model reads the request and the **skills card** (`agent/skills.py`): the actions it may use, what
   each one does and its parameters. It answers with 1 to 5 steps, or a refusal.
2. The code checks every step before it runs: known action, valid parameters, existing theme or status.
   A step that fails the checks stops the request.
3. The steps run one after the other, with no confirmation, each one written in the activity log. A
   change in Gmail (`relabel`, a sorted batch) is undone with **Undo** like any batch.
4. The model sums up what was done in one to three sentences, under the bar, with the list of steps.

Never on the card, and refused by the code if the model asks anyway: the Trash, restoring, reading a
whole mail, renaming or reordering labels. One exception: **renaming a theme** ("change the theme Travel
to Travel & Hobbies"). The Gmail label is renamed too (Gmail keeps the mails and the colour, a numbered
label keeps its number); the theme's keywords, rules, examples and colour follow; the model can give it a
new description when the new name changes what goes in it. Undo it by asking the reverse rename. Unsubscribing, sending mail and settings do not exist as actions. Words
found inside mails are data for the model, never instructions.

New actions, also open to other programs: `search` (Gmail's search, changes nothing), `relabel`
(another theme or status; counts as a correction, so keywords are learned), `keywords` (read or change
a theme's keywords) and `request`. `capabilities` returns the whole skills card.

## Clear errors

- Ollama not running: the agent starts it once if it is installed, else says how to start it (or where
  to download it). The model itself is loaded on the first mail (up to 5 minutes for a big one).
- Gmail refuses the login: the page lists the 3 things to check (IMAP on, an app password, paste it in
  Settings) with buttons to the Gmail pages and to Settings. Results carry an `error_code`
  (`gmail_login`, `offline`, `ollama_down`, `ollama_missing`) for other programs.

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
| `ollama`     | `""` (auto) or `qwen3.5:9b`| none              | runs on your computer, free |

With Ollama and an empty `model_name`, the agent reads your graphics memory and picks a model from
the `local_models` table. `python adagio.py hardware` shows what it found and why it chose that model.

With `model_name: active`, the agent uses the model loaded in Ollama. If none is loaded, it takes the
model of the last batch (or the best one for your PC) and Ollama loads it at the first mail. The
**Load** button next to the model name loads it ahead of time. Ollama then keeps it in memory for
30 minutes after the last mail (its own default is 5 minutes).

## Your own themes

Edit `themes` in `config.yaml`: add, rename or remove entries. To rename one, ask Adagio instead
("rename the theme Travel to Trips"): the Gmail label, keywords and rules follow. The `hint` tells the model what belongs
in the theme. Each theme also has typical words (bank, invoice, receipt...) given to the model as
clues: change them in the app, **Views > Keywords** (saved as `keywords:` at the end of `config.yaml`). Keep one theme named `Other` (used when nothing fits). Same for `statuses`.

## Files

```
adagio.py            command line (door 1) and JSON mode (door 2)
agent/core.py        handle(request) -> result: the single entry point
agent/mail.py        Gmail over IMAP: read, labels, read flag, Trash on request (never expunges)
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
agent/keywords.py    keywords learned from your corrections
agent/skills.py      the skills card: every action, its parameters, the checks of agent mode
agent/ask.py         agent mode: a request in your own words -> checked steps -> results
tests/               python -m unittest   (fake Gmail, fake model)
```

## Privacy

- The agent reads the start of each mail (1500 characters by default, `body_chars`) and sends it to the
  model you chose. With Ollama, nothing leaves your computer.
- The reader window fetches one whole mail when you click it, shows it and keeps nothing.
- It does not store mail content (the journal has the sender and the subject, never the text). From step 3, only the mail id, subject and tags go into a local
  SQLite file, to resume after a stop and to undo the last run. The Senders page stores each sender's
  address, name, mail count and unsubscribe link.
