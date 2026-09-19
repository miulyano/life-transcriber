"""Find and repair stored transcripts whose body lost text.

Run inside the ``bot`` container (needs ``OPENAI_API_KEY`` and the ``data/``
volume):

    python -m bot.tools.rebuild_transcript audit --user <telegram_user_id>
    python -m bot.tools.rebuild_transcript rebuild --user <telegram_user_id> <record_id>

``audit`` lists records whose body density is below
``LOW_DENSITY_CHARS_PER_SEC``; ``rebuild`` regenerates one body from its
segments. Deliver the result afterwards with «В чат» in the Mini App or the
MCP ``resend_to_chat`` tool.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from bot.services.transcript_rebuild import (
    LOW_DENSITY_CHARS_PER_SEC,
    is_low_density,
    rebuild_transcript_body,
)
from bot.services.transcript_store import get_transcript_store


async def _audit(user_id: int, min_cps: float) -> int:
    store = get_transcript_store()
    records = await store.list_for_user(user_id, limit=1000)
    flagged = [r for r in records if is_low_density(r, min_cps)]
    for r in flagged:
        cps = r.char_count / r.duration_sec
        print(f"{r.id}  {cps:5.1f} chars/s  {r.duration_sec:7.0f}s  {r.title}")
    print(f"{len(flagged)} of {len(records)} records below {min_cps} chars/s")
    return 0


async def _rebuild(user_id: int, record_id: str) -> int:
    store = get_transcript_store()
    record = await store.get(record_id, user_id)
    if record is None:
        print(f"record {record_id} not found for user {user_id}", file=sys.stderr)
        return 1
    before = record.char_count
    body = await rebuild_transcript_body(store, record)
    print(f"{record.id}: {before} -> {len(body)} chars")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    audit = sub.add_parser("audit", help="list records with suspiciously short bodies")
    audit.add_argument("--user", type=int, required=True)
    audit.add_argument("--min-cps", type=float, default=LOW_DENSITY_CHARS_PER_SEC)

    rebuild = sub.add_parser("rebuild", help="regenerate one body from segments")
    rebuild.add_argument("--user", type=int, required=True)
    rebuild.add_argument("record_id")

    args = parser.parse_args(argv)
    if args.cmd == "audit":
        return asyncio.run(_audit(args.user, args.min_cps))
    return asyncio.run(_rebuild(args.user, args.record_id))


if __name__ == "__main__":
    sys.exit(main())
