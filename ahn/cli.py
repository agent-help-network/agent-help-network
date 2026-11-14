from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from uuid import UUID, uuid4

from .db import connect, create_reporter, migrate
from .settings import Settings


def _iso_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--since needs a time zone, for example 2026-10-19T00:00:00Z")
    return parsed


def task_route_record(args, today=None):
    """The operator record that ``task-route add`` stores, checked by the answer model (ahn.task_routes.TaskRoute) with every
    server-decided value computed here. Raises ValueError for an invalid record or a site that is not kept (route_host)."""
    from datetime import date

    from .task_routes import TaskRoute, government_suffix, is_sensitive, period_for, route_host

    site = route_host(args.site_host)
    if site is None:
        raise ValueError("site_host_not_accepted")
    fields = {"task_kind": args.task_kind, "obstacle": args.obstacle, "verification_kind": args.verification_kind,
              "site_requires": args.site_requires, "document_kind": args.document_kind, "entry_point_kind": args.entry_point_kind,
              "route_worked": args.route_worked, "basis": args.basis, "outcome": args.outcome, "duration": args.duration,
              "official_url": args.official_url}
    fields = {name: value for name, value in fields.items() if value not in (None, "")}
    tried = [item.strip() for item in args.routes_tried.split(",") if item.strip()]
    sensitive = is_sensitive(fields["task_kind"], site_requires=fields.get("site_requires"), document_kind=fields.get("document_kind"),
                             entry_point_kind=fields.get("entry_point_kind"), government_host=government_suffix(site))
    return TaskRoute(site_host=site, routes_tried=tried, period=period_for(today or date.today()), source_kind=args.source_kind,
                     sensitive=sensitive, **fields)


def _task_route_command(settings, args, parser):
    from .task_route_intake import STORED_COLUMNS

    if args.route_command == "add":
        try:
            record = task_route_record(args)
        except ValueError as error:  # pydantic's ValidationError is a ValueError; an operator sees the reason
            print(f"invalid: {error}", file=sys.stderr)
            raise SystemExit(2) from None
        values = record.model_dump(mode="json")
        route_id = uuid4()
        with connect(settings) as conn:
            conn.execute(
                f"INSERT INTO task_routes (id,{','.join(STORED_COLUMNS)},sensitive,review_state) VALUES (%s,{','.join(['%s'] * len(STORED_COLUMNS))},%s,'pending')",
                (route_id, *[values.get(name) if name != "routes_tried" else list(values.get(name) or []) for name in STORED_COLUMNS],
                 record.sensitive),
            )
            conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("task_route_operator_added", route_id))
        print(route_id)
    elif args.route_command == "pending":
        if not 1 <= args.limit <= 100:
            parser.error("--limit must be 1-100")
        with connect(settings) as conn:
            rows = conn.execute(
                f"""SELECT id,{','.join(STORED_COLUMNS)},sensitive,review_reason,received_at FROM task_routes
                    WHERE review_state='pending' AND withdrawn_at IS NULL ORDER BY received_at LIMIT %s""",
                (args.limit,),
            ).fetchall()
        columns = ("id", *STORED_COLUMNS, "sensitive", "review_reason", "received_at")
        print("# columns: " + " | ".join(columns))
        for row in rows:
            print(" | ".join(str(row[name]) for name in columns))
        print(f"pending_shown={len(rows)}")
    elif args.route_command == "publish":
        with connect(settings) as conn:
            publication = conn.execute("SELECT enabled FROM service_flags WHERE name='public_publication' FOR SHARE").fetchone()
            if not publication or not publication["enabled"]:
                print("publication_paused")
                return
            row = conn.execute(
                """UPDATE task_routes SET review_state='published',review_reason='operator',reviewed_at=now()
                   WHERE id=%s AND review_state IN ('pending','held') AND withdrawn_at IS NULL RETURNING id""",
                (args.route_id,),
            ).fetchone()
            if row:
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("task_route_published", args.route_id))
        print("published" if row else "not_found_or_not_reviewable")
    elif args.route_command == "hold":
        with connect(settings) as conn:
            row = conn.execute(
                """UPDATE task_routes SET review_state='held',review_reason='operator',reviewed_at=now()
                   WHERE id=%s AND review_state IN ('pending','published') RETURNING id""",
                (args.route_id,),
            ).fetchone()
            if row:
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("task_route_held", args.route_id))
        print("held" if row else "not_found_or_held")
    elif args.route_command == "withdraw":
        # Works whatever the switches say, so an operator record (or a report, on a deletion request) can be taken out without SQL.
        # The row then follows the withdrawn-record rule of prune_task_route_reports.
        with connect(settings) as conn:
            row = conn.execute("UPDATE task_routes SET withdrawn_at=now() WHERE id=%s AND withdrawn_at IS NULL RETURNING id",
                               (args.route_id,)).fetchone()
            if row:
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("task_route_operator_withdrawn", args.route_id))
        print("withdrawn" if row else "not_found_or_withdrawn")


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
    window = export.add_mutually_exclusive_group()
    window.add_argument("--since", type=_iso_time, default=None, help="ISO 8601 time with a time zone")
    window.add_argument("--all", action="store_true", dest="all_reports",
                        help="reports only: every stored published report, not just the public 7-day horizon")
    export.add_argument("--out", default="-", help="output file; '-' (default) writes to standard output")
    bundle = sub.add_parser("bundle", help="build the offline data bundle (gzip JSON Lines, Export v1) from the database")
    bundle_target = bundle.add_mutually_exclusive_group(required=True)
    bundle_target.add_argument("--out", help="write the .jsonl.gz file here (to inspect it; nothing is published)")
    bundle_target.add_argument("--store", action="store_true",
                               help="replace the stored bundle the web service serves while AHN_PUBLIC_BUNDLE=1")
    task_route = sub.add_parser("task-route", help="operator only: stored task-route records (D118)")
    route_sub = task_route.add_subparsers(dest="route_command", required=True)
    route_add = route_sub.add_parser("add", help="add an operator record; it starts pending, publish it to show it")
    route_add.add_argument("--site-host", required=True)
    route_add.add_argument("--task-kind", required=True)
    route_add.add_argument("--obstacle", required=True)
    for name in ("verification-kind", "site-requires", "document-kind", "entry-point-kind", "route-worked", "duration", "official-url"):
        route_add.add_argument(f"--{name}", default=None)
    route_add.add_argument("--routes-tried", default="", help="comma-separated route kinds")
    route_add.add_argument("--basis", required=True)
    route_add.add_argument("--outcome", required=True)
    # Only operator records: a curated_community record needs a collection method that is not decided yet.
    route_add.add_argument("--source-kind", choices=("operator_panel",), default="operator_panel")
    route_pending = route_sub.add_parser("pending", help="list records waiting for review")
    route_pending.add_argument("--limit", type=int, default=20)
    for name in ("publish", "hold", "withdraw"):
        route_sub.add_parser(name).add_argument("route_id", type=UUID)
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "task-route":
        _task_route_command(settings, args, parser)
        return
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
                """SELECT o.id,i.site_host,i.task_step,i.error_class,o.outcome,o.attempt_actor,o.attempt_env,o.agent_family,o.observed_at,o.received_at,o.review_reason,o.cause,o.cause_basis,
                       CASE WHEN o.cause IS NULL THEN NULL ELSE (SELECT count(*)::bigint FROM observations o2 JOIN incidents i2 ON i2.id=o2.incident_id
                        WHERE o2.cause=o.cause AND i2.site_host=i.site_host AND o2.synthetic=false AND o2.withdrawn_at IS NULL
                          AND o2.received_at >= now() - interval '7 days') END AS same_cause_7d
                FROM observations o JOIN incidents i ON i.id=o.incident_id
                WHERE o.review_state='pending' AND o.synthetic=false AND o.withdrawn_at IS NULL
                ORDER BY o.received_at LIMIT %s""",
                (args.limit,),
            ).fetchall()
        print("# columns: id | site_host | task_step | error_class | outcome | attempt_actor | attempt_env | agent_family | observed_at | received_at | review_reason | cause (reporter's word, unverified) | cause_basis | same cause 7d (same host, incl. this one)")
        for row in rows:
            print(" | ".join(str(row[field]) for field in ("id", "site_host", "task_step", "error_class", "outcome", "attempt_actor", "attempt_env", "agent_family", "observed_at", "received_at", "review_reason", "cause", "cause_basis", "same_cause_7d")))
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
            count = export(settings, args.kind, sys.stdout, since=args.since, all_reports=args.all_reports,
                           signing_key=signing_key, kid=key_id())
        else:
            with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
                count = export(settings, args.kind, handle, since=args.since, all_reports=args.all_reports,
                               signing_key=signing_key, kid=key_id())
        print(f"exported={count} kind={args.kind} signed={'yes' if signing_key is not None else 'no'}", file=sys.stderr)
    elif args.command == "bundle":
        from .bundle import build_from_db, lock, signing_from_env, store

        signing_key, kid = signing_from_env()
        with connect(settings) as conn:
            if args.store and not lock(conn):
                print("bundle locked: another build is running; try again in a minute", file=sys.stderr)
                raise SystemExit(1)
            built = build_from_db(conn, signing_key=signing_key, kid=kid)
            if args.store:
                store(conn, built)
        if args.out:
            with open(args.out, "wb") as handle:
                handle.write(built.body)
        counts = " ".join(f"{kind}={count}" for kind, count in built.counts.items())
        print(f"bundle records={built.record_count} {counts} bytes={len(built.body)} sha256={built.sha256} "
              f"signed={'yes' if signing_key is not None else 'no'} {'stored' if args.store else 'file=' + args.out}", file=sys.stderr)
    elif args.command == "disable-reporter":
        with connect(settings) as conn:
            row = conn.execute("UPDATE reporters SET disabled_at=COALESCE(disabled_at,now()) WHERE id=%s RETURNING id", (args.reporter_id,)).fetchone()
            if row:
                conn.execute("INSERT INTO audit_events (event_type,record_id) VALUES (%s,%s)", ("reporter_disabled", args.reporter_id))
        print("disabled" if row else "not_found")


if __name__ == "__main__":
    main()
