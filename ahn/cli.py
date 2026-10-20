from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from uuid import UUID

from .db import connect, create_reporter, migrate
from .settings import Settings


def _iso_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--since needs a time zone, for example 2026-10-19T00:00:00Z")
    return parsed


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    reporter = sub.add_parser("create-reporter")
    reporter.add_argument("label")
    hide = sub.add_parser("hide-observation")
    hide.add_argument("observation_id", type=UUID)
    pending = sub.add_parser("pending-reports")
    pending.add_argument("--limit", type=int, default=20)
    publish = sub.add_parser("publish-observation")
    publish.add_argument("observation_id", type=UUID)
    sub.add_parser("pause-intake")
    sub.add_parser("resume-intake")
    sub.add_parser("intake-status")
    sub.add_parser("pause-publication")
    sub.add_parser("resume-publication")
    sub.add_parser("publication-status")
    disable = sub.add_parser("disable-reporter")
    disable.add_argument("reporter_id", type=UUID)
    export = sub.add_parser("export", help="write stored facts as JSON Lines envelopes (schema_version 1)")
    export.add_argument("--kind", required=True, choices=("access_profile", "status", "observation"))
    export.add_argument("--since", type=_iso_time, default=None, help="ISO 8601 time with a time zone")
    export.add_argument("--out", default="-", help="output file; '-' (default) writes to standard output")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "migrate":
        print(f"Applied migrations: {migrate(settings)}")
    elif args.command == "create-reporter":
        print("Store this key outside the repository; it will not be shown again:")
        print(create_reporter(settings, args.label))
    elif args.command == "hide-observation":
        with connect(settings) as conn:
            row = conn.execute("UPDATE observations SET review_state='hidden' WHERE id=%s RETURNING id", (args.observation_id,)).fetchone()
            if row:
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("observation_hidden", args.observation_id))
        print("hidden" if row else "not_found")
    elif args.command == "pending-reports":
        if not 1 <= args.limit <= 100:
            parser.error("--limit must be 1-100")
        with connect(settings) as conn:
            rows = conn.execute(
                """SELECT o.id,i.site_host,i.task_step,i.error_class,o.outcome,o.attempt_actor,o.attempt_env,o.agent_family,o.observed_at,o.received_at,o.review_reason
                FROM observations o JOIN incidents i ON i.id=o.incident_id
                WHERE o.review_state='pending' AND o.synthetic=false AND o.withdrawn_at IS NULL
                ORDER BY o.received_at LIMIT %s""",
                (args.limit,),
            ).fetchall()
        for row in rows:
            print(" | ".join(str(row[field]) for field in ("id", "site_host", "task_step", "error_class", "outcome", "attempt_actor", "attempt_env", "agent_family", "observed_at", "received_at", "review_reason")))
        print(f"pending_shown={len(rows)}")
    elif args.command == "publish-observation":
        with connect(settings) as conn:
            publication = conn.execute("SELECT enabled FROM service_flags WHERE name='public_publication' FOR SHARE").fetchone()
            if not publication or not publication["enabled"]:
                print("publication_paused")
                return
            row = conn.execute(
                "UPDATE observations SET review_state='published' WHERE id=%s AND review_state='pending' AND synthetic=false AND withdrawn_at IS NULL RETURNING incident_id",
                (args.observation_id,),
            ).fetchone()
            if row:
                conn.execute("UPDATE incidents SET visibility='published' WHERE id=%s AND visibility<>'hidden'", (row["incident_id"],))
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("observation_published", args.observation_id))
        print("published" if row else "not_found_or_not_pending")
    elif args.command in {"pause-intake", "resume-intake", "intake-status"}:
        with connect(settings) as conn:
            if args.command != "intake-status":
                conn.execute("UPDATE service_flags SET enabled=%s WHERE name='public_intake'", (args.command == "resume-intake",))
            row = conn.execute("SELECT enabled FROM service_flags WHERE name='public_intake'").fetchone()
        print("enabled" if row and row["enabled"] else "paused")
    elif args.command in {"pause-publication", "resume-publication", "publication-status"}:
        with connect(settings) as conn:
            if args.command != "publication-status":
                conn.execute("UPDATE service_flags SET enabled=%s WHERE name='public_publication'", (args.command == "resume-publication",))
            row = conn.execute("SELECT enabled FROM service_flags WHERE name='public_publication'").fetchone()
        print("enabled" if row and row["enabled"] else "paused")
    elif args.command == "export":
        from .export import export, key_id, load_signing_key

        key_path = os.environ.get("AHN_EXPORT_SIGNING_KEY", "").strip()
        signing_key = load_signing_key(key_path) if key_path else None
        if args.out == "-":
            count = export(settings, args.kind, sys.stdout, since=args.since, signing_key=signing_key, kid=key_id())
        else:
            with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
                count = export(settings, args.kind, handle, since=args.since, signing_key=signing_key, kid=key_id())
        print(f"exported={count} kind={args.kind} signed={'yes' if signing_key is not None else 'no'}", file=sys.stderr)
    elif args.command == "disable-reporter":
        with connect(settings) as conn:
            row = conn.execute("UPDATE reporters SET disabled_at=COALESCE(disabled_at,now()) WHERE id=%s RETURNING id", (args.reporter_id,)).fetchone()
            if row:
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("reporter_disabled", args.reporter_id))
        print("disabled" if row else "not_found")


if __name__ == "__main__":
    main()
