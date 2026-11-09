"""po-mailer-demo: a send_email stub for demonstrating the counterparty gate.

It sends nothing. It returns a JSON receipt so the agent believes the email went out,
which is exactly what the counterparty plugin must intercept beforehand.
Install for the demo only: ``hermes plugins install /path/to/hermes-counterparty/demo/po-mailer``.
"""

from __future__ import annotations

import json

SEND_EMAIL = {
    "name": "send_email",
    "description": "Send an email from the user's mailbox (demo stub: nothing is actually sent).",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient address"},
            "subject": {"type": "string", "description": "Subject line"},
            "body": {"type": "string", "description": "Plain-text body"},
        },
        "required": ["to", "subject", "body"],
    },
}


def send_email(args: dict, **_: object) -> str:
    return json.dumps({"ok": True, "demo": True, "to": args.get("to"), "subject": args.get("subject"),
                       "note": "po-mailer-demo stub: no email was sent"})


def register(ctx) -> None:
    ctx.register_tool(name="send_email", toolset="po_mailer_demo", schema=SEND_EMAIL, handler=send_email,
                      description="Demo email sender (sends nothing)", emoji="📧")
