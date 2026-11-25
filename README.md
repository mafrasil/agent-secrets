# secrets: a Claude Code plugin

Lets agents ask for API keys without the values ever entering the chat.

```
agent:  secretctl request STRIPE_SECRET_KEY --why "checkout tests"
        → "Open http://localhost:7787/s/Xk3… (or run `secretctl set STRIPE_SECRET_KEY`)"
you:    paste the key into the one-time page, or the hidden terminal prompt
agent:  secretctl wait STRIPE_SECRET_KEY        → "stored (value hidden)"
agent:  secretctl run STRIPE_SECRET_KEY -- npm test
```

Python 3.8+ standard library only, so it runs on macOS, Fedora/Ubuntu cloud boxes and OrbStack machines alike. Nothing to install.

## Install

Push this folder to a git repo (for example `mafrasil/agent-secrets`), then in Claude Code:

```
/plugin marketplace add mafrasil/agent-secrets
/plugin install secrets@mafrasil
```

Or try it locally: `/plugin marketplace add /path/to/agent-secrets`.

Install it on **every machine where Claude Code runs**: each VM, OrbStack machine or cloud box. Secrets are stored per machine, which keeps one client's keys inside that client's VM.

## What's inside

| Piece | What it does |
|---|---|
| `bin/secretctl` | The CLI. The plugin's `bin/` is on the agent's PATH. The SessionStart hook also links it to `~/.local/bin/secretctl` so you can use it in your own terminal |
| `skills/secret-keys` | Tells the agent when and how to request and use secrets, and never to ask for a paste |
| `hooks/guard.py` (PreToolUse) | Blocks any tool call that touches the vault folder or `/proc/*/environ` |
| `hooks/guard.py` (PostToolUse) | Scans every tool result and replaces any stored value (raw, base64 or hex) with `[secret:NAME]` before the model sees it |
| `hooks/guard.py` (UserPromptSubmit) | If you paste something that looks like a key (Stripe, OpenAI, Anthropic, GitHub, AWS, Slack, Polar, PEM and more), the message is stopped before it's sent |
| `hooks/guard.py` (SessionStart) | Tells the agent which secret names exist, so it doesn't ask for them again |

## Storage

`~/.local/share/secretctl-vault/`, or `$XDG_DATA_HOME/secretctl-vault/`. The folder is `0700` and holds one `0600` file per secret, named by the SHA-256 of the secret's name. That's the same protection your `~/.ssh` keys get. It is not encrypted at rest. On a single-user dev box the encryption key would sit next to the data anyway. For real protection, use full-disk encryption, which you already have with LUKS in the VMs.

## Entering values

**Several keys at once.** One request, one link, one page with a card per key. You can save some now and come back for the rest. Keys you enter in the terminal meanwhile disappear from the page, and the page closes once everything is in. The page follows your system's light or dark mode, in Claude's colour palette.

**Where to find the key.** Agents can pass `--where <url>` and any number of `--step "…"`. The page shows the numbered steps and a "Get it from dashboard.stripe.com ↗" button. The same steps are printed above the terminal prompt.

**One-time page.** Served on `127.0.0.1:7787`, falling back to a random port if that one is busy. Each link has a random token and expires after 15 minutes or one use. The page has a multi-line mode for PEM or JSON keys.

- **Mac, running locally:** the page opens by itself.
- **OrbStack machine:** open the link on your Mac as is, because OrbStack forwards machine ports to Mac `localhost`. If it doesn't load, set `SECRETCTL_BIND=0.0.0.0` in that machine. OrbStack's network is private to your Mac.
- **Cloud box over SSH:** add `LocalForward 7787 localhost:7787` to its `~/.ssh/config` entry once. Or, on Tailscale, set `SECRETCTL_BIND=<tailscale-ip>` and `SECRETCTL_URL_HOST=<magicdns-name>`.

**Terminal.** `secretctl set NAME` gives a hidden prompt. `secretctl set NAME --file ~/Downloads/key.pem` reads a value from a file. This works everywhere, including over plain SSH.

Settings: `SECRETCTL_PORT` (0 means random), `SECRETCTL_BIND`, `SECRETCTL_URL_HOST`, `SECRETCTL_NO_OPEN=1`, `SECRETCTL_VAULT` (custom folder).

## Commands

```
secretctl request NAME [NAME...] [--why TEXT] [--where URL] [--step TEXT ...] [--replace] [--ttl SECONDS]
secretctl request --spec JSON|@file|-        several keys, each with its own why/where/steps
secretctl wait NAME [NAME...] [--timeout S]  exit 0 all stored · 4 still waiting · 1 expired
secretctl set NAME [NAME...]                 hidden prompt per key (Enter skips); or --file / --stdin for one
secretctl run [VAR=]NAME ... -- command [args]
secretctl list | has NAME... | rm NAME...
```

## Limits

- This protects against **accidental** leaks: keys ending up in chat, transcripts, logs, screenshots of the conversation or model context. It does **not** stop a deliberately malicious agent. Such an agent could send `$KEY` to a remote server from inside `secretctl run`.
- Values shorter than 8 characters aren't redacted, to avoid false matches.
- The guard blocks any tool input containing `secretctl-vault`. So if you have Claude edit this plugin's own source, that one string will trip it. Edit that line by hand.
- Codex and other agents in T3 Code don't load Claude Code plugins. `secretctl` itself works for any agent, though. Put the "Rules" and "Use it" parts of `SKILL.md` into an `AGENTS.md` file for them. The redaction hooks only apply in Claude Code.
