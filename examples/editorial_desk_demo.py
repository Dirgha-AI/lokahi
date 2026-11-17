# -*- coding: utf-8 -*-
"""Pilot example: a small publication's editorial desk, gated by LOKAHI v0.1.3.

The editors write one signed manifest saying what an AI agent may do with
unpublished manuscripts. Every tool call then goes through the gateway:

  * reads that stay inside the rules pass;
  * forbidden fields, unlisted fields and uploads outside India are refused;
  * irreversible acts (emailing an author, publishing) wait for people, and
    an unanswered request becomes a refusal when the gateway's window closes;
  * the agent cannot approve itself by sending approval data in its request;
  * approvals arrive separately, signed by each person, for that exact call;
  * every decision lands in a tamper-evident local record.

Run from the repository root:  python examples/editorial_desk_demo.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from lokahi import Approval, Manifest, Signer  # noqa: E402
from mcp_stdio_proxy import ApprovalStore, LokahiMCPGateway  # noqa: E402

NOW = 1_800_000_000.0           # fixed clock so the demo is repeatable
WINDOW = 3600                   # approvals must arrive within an hour
EXPIRES = "2027-02-01T00:00:00Z"

TOOLS = {
    "summarize_submission": {
        "class": "read",
        "args": {"submission_id": "submission_id", "manuscript_text": "manuscript_text"},
    },
    "send_to_cloud_model": {
        "class": "write",
        "stores_in": ["US"],    # the editors know this service stores data in the US
        "args": {"manuscript_text": "manuscript_text"},
    },
    "email_author": {"class": "irreversible", "args": {"submission_id": "submission_id", "body": None}},
    "publish_story": {
        "class": "irreversible",
        "population_affecting": True,
        "args": {"submission_id": "submission_id", "manuscript_text": "manuscript_text"},
    },
}


def call(tool, **arguments):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}


def show(label, res):
    if res["allowed"]:
        verdict, reasons = "ALLOWED", res["lokahi_receipt"]["reasons"]
    else:
        verdict, reasons = res["error"]["data"]["verdict"].upper(), res["error"]["data"]["reasons"]
    print(f"{label:<52} -> {verdict:<9} {', '.join(reasons)}")
    return res


def main() -> int:
    manifest_key = Signer(b"editorial-desk-manifest-key-for-demo-only")
    people = {
        "editor:fiction": Signer(b"key-editor-fiction-demo-only-0000"),
        "editor:managing": Signer(b"key-editor-managing-demo-only-000"),
        "author:contributor": Signer(b"key-author-contributor-demo-only0"),
    }
    manifest = Manifest(
        node="editorial-desk",
        informational=["submission_id", "manuscript_text", "word_count", "genre"],
        forbidden_targets=["author_home_address", "author_payment_details"],
        jurisdiction="IN-DL",
        residency=["IN"],
        exceptions={"safety_critical": 1, "window_hours": 24},
        expires="2028-03-01T00:00:00Z",
        steward="did:web:editorial-desk.example/editors",
        tools=TOOLS,
        approval_window_seconds=WINDOW,
    ).sign(manifest_key)

    store = ApprovalStore()
    gw = LokahiMCPGateway(
        manifest,
        manifest_key,
        approvers=people,
        approval_store=store,
        default_requester="agent:dirgha-code",
        default_operator="ops:agent-runner",
    )

    def sign_off(digest, who, decision="yes"):
        for i, person in enumerate(who):
            store.add(Approval(person, f"device-{i}", decision, digest, EXPIRES, f"{digest[:8]}-{person}").sign(people[person]))

    print("=" * 96)
    print("LOKAHI v0.1.3 PILOT EXAMPLE: EDITORIAL DESK")
    print("=" * 96)
    show("summarize a submission", gw.intercept(call("summarize_submission", submission_id="S-12", manuscript_text="..."), now=NOW))
    show("ask for the author's home address", gw.intercept(call("summarize_submission", submission_id="S-12", author_home_address=True), now=NOW))
    show("ask for a field nobody listed", gw.intercept(call("summarize_submission", submission_id="S-12", author_social_handles=True), now=NOW))
    show("send the manuscript to a US cloud model", gw.intercept(call("send_to_cloud_model", manuscript_text="..."), now=NOW))
    show("use a tool the editors never listed", gw.intercept(call("delete_submission_archive"), now=NOW))

    forged = call("email_author", submission_id="S-12", body="Thanks, but no.")
    forged["params"]["_lokahi"] = {"consents": [{"operator": p, "node": "x", "decision": "yes"} for p in people]}
    show("agent sends its own 'approvals' with the email", gw.intercept(forged, now=NOW))

    email = call("email_author", submission_id="S-12", body="Thanks, but no.")
    show("email a rejection: waits for people", gw.intercept(email, now=NOW))
    show("email a rejection: nobody answered in an hour", gw.intercept(email, now=NOW + WINDOW))

    publish = call("publish_story", submission_id="S-07", manuscript_text="...")
    waiting = show("publish: waits for people", gw.intercept(publish, now=NOW))
    digest = waiting["error"]["data"]["action_digest"]
    sign_off(digest, ["editor:fiction", "editor:managing", "author:contributor"])
    show("publish: two editors and the author sign yes", gw.intercept(publish, now=NOW + 120))

    other = call("publish_story", submission_id="S-09", manuscript_text="...")
    show("their approvals reused for a different story", gw.intercept(other, now=NOW + 120))

    print("-" * 96)
    ok, count = gw.ledger.verify()
    print(f"local record: {count} decisions, tamper check passed = {ok}")
    gw.ledger.entries[3]["verdict"] = "compliant"  # someone edits the record to hide the cloud upload
    still_ok, broken_at = gw.ledger.verify()
    print(f"someone edits decision 4 to hide the cloud upload: tamper check passed = {still_ok}"
          f" (record breaks at decision {broken_at + 1})")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
