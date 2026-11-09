# Demo script (under 90 seconds)

Recording: a terminal running `hermes` with the plugin enabled, `mode: ask`. A second window is not needed.

| t | On screen | Voice or caption |
|---|---|---|
| 0:00 | Title card: "counterparty: Hermes checks who your agent is about to pay." | |
| 0:04 | `hermes` prompt. User types: `Order 200 IP cameras from Hikvision USA and email their sales desk at orders@hikvision.com the PO.` | "The agent gets a normal procurement task." |
| 0:14 | Hermes drafts the email and calls `send_email`. The counterparty hook fires. Hermes's approval prompt appears: "Hikvision is on DoD Section 1260H, FCC Covered List, Section 889 ... [FAR 52.204-25; 2 CFR 200.216]. Source: Tensorust public-records ontology, lists pulled 2026-11-06. Tool 'send_email' requires approval." | "Before the email leaves, the plugin screens the vendor and hands Hermes's own approval gate the finding, with the citation." |
| 0:32 | User presses `n`. The agent reports the block and suggests alternatives. | "Nobody approved it, so nothing was sent. The model cannot skip this gate." |
| 0:40 | User types `/whoowns Hytera`. The brief prints: verdict, identity, lists with citations, ownership chain Hytera -> Norsat International, credit marks, sources. | "Any company, any time: a one-page cited brief." |
| 0:58 | User types: `search the web for budget IP camera brands`. The result comes back with the appended note: "this result mentions listed vendors: Dahua, Hikvision ..." | "Research gets a heads-up too, once per vendor per session." |
| 1:10 | Clean case: `email ap@dell.com the Q4 invoice`. Goes through, no prompt. | "Clean counterparties pass silently." |
| 1:18 | End card: `hermes plugins install counterparty`, repo URL, "lists: 1260H, FCC Covered, 889, UFLPA, BIOSECURE, OFAC". | |

Cut at 1:25 or earlier.

## Recording notes

- Terminal 120x34, dark theme, font 18px. Record with ffmpeg gdigrab on the terminal window, or OBS.
- Set `plugins.hook_callback_timeout` to the default; the hook answers in about one second.
- If no email tool is configured, register the demo's `send_email` through the bundled `demo/` plugin (a stub that returns `{"ok": true}` without sending anything), so the approval prompt is real and nothing leaves the machine.
