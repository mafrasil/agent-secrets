---
name: secret-keys
description: Use whenever a task needs an API key, token, password, webhook secret, private key or other credential, for example to call an API, run a dev server, seed data or deploy. Covers checking which secrets exist, requesting a missing one from the user without it entering the chat, and running commands with secrets injected as env vars.
---

# Secret keys

Secrets are managed by `secretctl` (on your PATH). You never see a secret value: the user enters it outside the chat, and `secretctl run` passes it to the command as an environment variable.

## Rules

- **Never ask the user to paste a secret into the chat**, and never accept one there. If they try, point them to `secretctl set NAME`.
- Never print, echo, log, decode or write a secret value to a file. Never try to read the vault, `/proc/*/environ`, or the env of a `secretctl run` process. A hook blocks this and redacts stored values from tool output as `[secret:NAME]`.
- Name secrets in `UPPER_SNAKE_CASE`, specific to the project when useful: `AMY_STRIPE_SECRET_KEY`, `OPENAI_API_KEY`.

## 1. Check what exists

```bash
secretctl list                 # names only
secretctl has STRIPE_SECRET_KEY
```

## 2. Request a missing secret

```bash
secretctl request STRIPE_SECRET_KEY \
  --why "Stripe test-mode secret key, to run the checkout tests" \
  --where "https://dashboard.stripe.com/test/apikeys" \
  --step "Make sure the Test mode toggle is on" \
  --step "Under Standard keys, click Reveal test key next to Secret key" \
  --step "Copy it (it starts with sk_test_)"
```

`--where` and `--step` are optional, but include them whenever you know where the key lives. They're shown on the page and in the terminal prompt, so the user doesn't have to hunt for it. Use the deepest direct URL you're confident of. If unsure, link the provider's dashboard or API-keys docs page rather than guessing a deep path. Keep steps short (2–5), in order, and mention the expected prefix or format if there is one.

**Need several keys? Ask for them all in one request.** The user gets one link and one page with a card per key, and can save some now and the rest later. Pass a JSON spec on stdin so each key gets its own why, where and steps:

```bash
secretctl request --spec - <<'EOF'
[
  {"name": "STRIPE_SECRET_KEY", "why": "Checkout tests",
   "where": "https://dashboard.stripe.com/test/apikeys",
   "steps": ["Make sure Test mode is on", "Reveal the Secret key and copy it (sk_test_…)"]},
  {"name": "RESEND_API_KEY", "why": "Transactional email", "where": "https://resend.com/api-keys"}
]
EOF
```

`secretctl request A B C --why "…"` also works when the keys share one explanation. Names that are already stored are skipped automatically.

The output has a one-time link and a terminal alternative. **Relay both to the user in your message, as written**, including the steps. Then block until everything arrives:

```bash
secretctl wait STRIPE_SECRET_KEY RESEND_API_KEY   # give the Bash call a 600000 ms timeout
```

- Exit 0 means all of them are stored. Continue.
- Exit 4 means you're still waiting. The output says which keys are stored so far and which are missing. The link is still valid, so tell the user and run `wait` again for the missing ones.
- Exit 1 means the request expired. Request the missing ones again.

If the user says they already stored it, run `secretctl has NAME`. To replace a wrong value, use `secretctl request NAME --replace`.

## 3. Use it

```bash
# Secret becomes $STRIPE_SECRET_KEY for this command only
secretctl run STRIPE_SECRET_KEY -- npm test

# Map to the variable name the app expects: VAR=SECRET_NAME
secretctl run STRIPE_SECRET=AMY_STRIPE_SECRET_KEY OPENAI_API_KEY -- php artisan app:sync

# Inline use: single quotes so the shell inside expands it, not you
secretctl run GITHUB_TOKEN -- sh -c 'curl -sf -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/user | jq .login'

# Dev servers and stacks
secretctl run STRIPE_SECRET_KEY -- npm run dev
secretctl run DB_PASSWORD -- docker compose up -d
```

Real environment variables take precedence over `.env` files in Laravel (phpdotenv), Next.js, Vite and Docker Compose interpolation. So `secretctl run` usually means you don't need to edit `.env` at all. If a tool can only read a secret from a file, stop and ask the user to put it there themselves. Don't write it yourself.

Output from `secretctl run` is redacted line by line. Interactive prompts that don't end in a newline only appear when the command exits.
