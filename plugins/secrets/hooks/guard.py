#!/usr/bin/env python3
"""Hooks for the secrets plugin. One script, dispatched on hook_event_name.

PreToolUse        block tools that touch the vault or read process environments
PostToolUse       redact any stored secret value (and its base64/hex forms) from tool output
UserPromptSubmit  stop prompts that look like a pasted key before they reach the model
SessionStart      put `secretctl` on the user's PATH and tell the agent which names exist
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import secretstore as store  # noqa: E402

KEY_PATTERNS = [
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "Anthropic key"),
    (r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}", "OpenAI-style key"),
    (r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}", "Stripe key"),
    (r"\bwhsec_[A-Za-z0-9]{20,}", "webhook secret"),
    (r"\bgh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{40,}", "GitHub token"),
    (r"\bglpat-[A-Za-z0-9_\-]{20,}", "GitLab token"),
    (r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", "Google API key"),
    (r"\bxox[abprs]-[A-Za-z0-9\-]{10,}", "Slack token"),
    (r"\bnpm_[A-Za-z0-9]{36}\b", "npm token"),
    (r"\bSG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}", "SendGrid key"),
    (r"\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}", "Resend key"),
    (r"\bpolar_(?:oat|pat|at)_[A-Za-z0-9]{20,}", "Polar token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
]
ALLOW_MARKER = "[allow-secret]"


def out(obj: dict):
    print(json.dumps(obj))
    sys.exit(0)


def vault_needles() -> list[str]:
    d = str(store.vault_dir(create=False))
    home = os.path.expanduser("~")
    needles = {store.VAULT_DIRNAME, d}
    if d.startswith(home):
        needles.add("~" + d[len(home):])
    return sorted(needles)


def pre_tool_use(ev: dict):
    blob = json.dumps(ev.get("tool_input", {}))
    for n in vault_needles():
        if n and n in blob:
            out({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason":
                    "The secrets vault is off-limits to the agent. Use `secretctl list`, `secretctl has NAME` "
                    "or `secretctl run NAME -- <command>`; never read secret values.",
            }})
    if ev.get("tool_name") == "Bash":
        cmd = ev.get("tool_input", {}).get("command", "")
        if re.search(r"/proc/[^\s]*/environ", cmd) or re.search(r"secretctl\s+_serve", cmd):
            out({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Reading process environments is blocked because it would expose secrets.",
            }})


def _walk(v, pairs, changed):
    if isinstance(v, str):
        r = store.redact_text(v, pairs)
        if r != v:
            changed[0] = True
        return r
    if isinstance(v, list):
        return [_walk(x, pairs, changed) for x in v]
    if isinstance(v, dict):
        return {k: _walk(x, pairs, changed) for k, x in v.items()}
    return v


def post_tool_use(ev: dict):
    pairs = store.redaction_pairs()
    if not pairs or "tool_response" not in ev:
        return
    changed = [False]
    new = _walk(ev["tool_response"], pairs, changed)
    if changed[0]:
        out({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": new,
            "additionalContext": "A stored secret appeared in this tool output and was replaced with "
                                 "[secret:NAME]. Do not try to recover or print secret values.",
        }})


def user_prompt_submit(ev: dict):
    prompt = ev.get("prompt") or ""
    if ALLOW_MARKER in prompt:
        return
    hit = None
    for pat, label in KEY_PATTERNS:
        if re.search(pat, prompt):
            hit = label
            break
    if not hit:
        for needle, label in store.redaction_pairs():
            if needle in prompt:
                hit = f"stored secret {label}"
                break
    if hit:
        sys.stderr.write(
            f"Blocked: this message looks like it contains a {hit}, so it was not sent to the agent.\n"
            "Store it instead: run `secretctl set SOME_NAME` in a terminal on this machine, "
            "then tell the agent the name.\n"
            f"(Not a secret? Add {ALLOW_MARKER} to the message to send it anyway.)\n")
        sys.exit(2)


def session_start(ev: dict):
    src = os.path.join(ROOT, "bin", "secretctl")
    bindir = os.path.expanduser("~/.local/bin")
    link = os.path.join(bindir, "secretctl")
    try:
        os.makedirs(bindir, exist_ok=True)
        if os.path.islink(link) and os.readlink(link) != src:
            os.unlink(link)
        if not os.path.lexists(link):
            os.symlink(src, link)
    except OSError:
        pass
    names = [e.get("name") for e in store.all_entries() if e.get("name")]
    msg = ("Secrets are handled by `secretctl` (see the secret-keys skill). Never ask the user to paste a key in chat. ")
    msg += ("Stored secret names: " + ", ".join(names) + "." if names else "No secrets stored yet.")
    out({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}})


def main():
    try:
        ev = json.load(sys.stdin)
    except ValueError:
        return
    handler = {
        "PreToolUse": pre_tool_use,
        "PostToolUse": post_tool_use,
        "UserPromptSubmit": user_prompt_submit,
        "SessionStart": session_start,
    }.get(ev.get("hook_event_name"))
    if handler:
        handler(ev)


if __name__ == "__main__":
    main()
