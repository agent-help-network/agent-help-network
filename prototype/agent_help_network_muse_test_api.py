"""Read-only, synthetic Agent Help Network connector probe.

Run with: python agent_help_network_muse_test_api.py --port 8765 --key-file PATH
Only binds to 127.0.0.1. Publish via a temporary HTTPS tunnel for Muse testing.
"""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


DEMO_HOST = "demo.agenthelpnetwork.test"
RECENT_RECOVERY_HOST = "case-a.agenthelpnetwork.test"
RECENT_FAILURE_HOST = "case-b.agenthelpnetwork.test"
TEST_HOSTS = {DEMO_HOST, RECENT_RECOVERY_HOST, RECENT_FAILURE_HOST}


def openapi_spec(public_url: str) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Agent Help Network Connector Test API",
            "version": "0.1.0",
            "description": (
                "Read-only connection test. All incident data is synthetic. "
                "This API does not publish, accept, or store reports."
            ),
        },
        "servers": [{"url": public_url}],
        "paths": {
            "/v1/incidents/check": {
                "get": {
                    "operationId": "checkIncident",
                    "summary": "Check synthetic incident observations for a site",
                    "description": (
                        "Use during an authorized task when a site visit is blocked. "
                        "demo.agenthelpnetwork.test, case-a.agenthelpnetwork.test, and "
                        "case-b.agenthelpnetwork.test have fictional observations. "
                        "All other sites return no_reports. Never infer site health from no_reports."
                    ),
                    "security": [{"ApiKeyAuth": []}],
                    "parameters": [
                        {
                            "name": "site_host",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Hostname only, without a URL path or user data.",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Synthetic observation summary or no reports",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/CheckResult"}
                                }
                            },
                        },
                        "400": {"description": "Invalid hostname"},
                        "401": {"description": "Missing or invalid API key"},
                    },
                }
            }
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-API-Key",
                }
            },
            "schemas": {
                "CheckResult": {
                    "type": "object",
                    "required": [
                        "site_host", "generated_at", "synthetic", "evidence_level",
                        "summary", "failures", "successes", "observations",
                    ],
                    "properties": {
                        "site_host": {"type": "string"},
                        "generated_at": {"type": "string", "format": "date-time"},
                        "synthetic": {"type": "boolean"},
                        "evidence_level": {
                            "type": "string",
                            "enum": ["single_observation", "multiple_observations", "no_reports"],
                        },
                        "summary": {"type": "string"},
                        "failures": {"type": "integer"},
                        "successes": {"type": "integer"},
                        "observations": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Observation"},
                        },
                    },
                },
                "Observation": {
                    "type": "object",
                    "required": ["observed_at", "reporter_id", "attempt_actor", "outcome"],
                    "properties": {
                        "observed_at": {"type": "string", "format": "date-time"},
                        "reporter_id": {"type": "string"},
                        "attempt_actor": {"type": "string", "enum": ["agent", "human"]},
                        "outcome": {
                            "type": "string",
                            "enum": ["verification_failed", "verification_passed"],
                        },
                    },
                },
            },
        },
    }


def check_result(site_host: str) -> dict:
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat().replace("+00:00", "Z")

    def observation(minutes_ago: int, reporter_id: str, actor: str, outcome: str) -> dict:
        return {
            "observed_at": (now_dt - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
            "reporter_id": reporter_id,
            "attempt_actor": actor,
            "outcome": outcome,
        }

    if site_host == DEMO_HOST:
        return {
            "site_host": site_host,
            "generated_at": now,
            "synthetic": True,
            "evidence_level": "single_observation",
            "summary": (
                "TEST DATA ONLY: one fictional agent-browser verification failure. "
                "This is not evidence of a real site outage or intentional block."
            ),
            "failures": 1,
            "successes": 0,
            "observations": [observation(20, "fictional-demo-1", "agent", "verification_failed")],
        }
    if site_host == RECENT_RECOVERY_HOST:
        observations = [
            observation(95, "fictional-a-1", "agent", "verification_failed"),
            observation(80, "fictional-a-2", "human", "verification_failed"),
            observation(70, "fictional-a-3", "agent", "verification_failed"),
            observation(60, "fictional-a-4", "human", "verification_failed"),
            observation(45, "fictional-a-5", "agent", "verification_failed"),
            observation(8, "fictional-a-6", "human", "verification_passed"),
            observation(5, "fictional-a-7", "agent", "verification_passed"),
            observation(2, "fictional-a-8", "human", "verification_passed"),
        ]
        return {
            "site_host": site_host,
            "generated_at": now,
            "synthetic": True,
            "evidence_level": "multiple_observations",
            "summary": (
                "TEST DATA ONLY: five earlier verification failures followed by three "
                "recent passes from separate fictional reporters. Current availability "
                "is not guaranteed."
            ),
            "failures": 5,
            "successes": 3,
            "observations": observations,
        }
    if site_host == RECENT_FAILURE_HOST:
        observations = [
            observation(180, "fictional-b-0", "human", "verification_passed"),
            observation(10, "fictional-b-1", "agent", "verification_failed"),
            observation(9, "fictional-b-2", "human", "verification_failed"),
            observation(7, "fictional-b-3", "agent", "verification_failed"),
            observation(5, "fictional-b-4", "human", "verification_failed"),
            observation(3, "fictional-b-5", "agent", "verification_failed"),
            observation(1, "fictional-b-6", "human", "verification_failed"),
        ]
        return {
            "site_host": site_host,
            "generated_at": now,
            "synthetic": True,
            "evidence_level": "multiple_observations",
            "summary": (
                "TEST DATA ONLY: one older pass followed by six recent verification "
                "failures from separate fictional reporters, including human attempts. "
                "This does not prove universal failure or intentional blocking."
            ),
            "failures": 6,
            "successes": 1,
            "observations": observations,
        }
    return {
        "site_host": site_host,
        "generated_at": now,
        "synthetic": True,
        "evidence_level": "no_reports",
        "summary": "TEST API: no synthetic report for this host. This does not mean the site works.",
        "failures": 0,
        "successes": 0,
        "observations": [],
    }


def valid_host(value: str) -> bool:
    if not (1 <= len(value) <= 253) or value != value.lower():
        return False
    labels = value.split(".")
    return len(labels) >= 2 and all(
        1 <= len(label) <= 63
        and label[0] != "-"
        and label[-1] != "-"
        and all(char.isascii() and (char.isalnum() or char == "-") for char in label)
        for label in labels
    )


def make_handler(api_key: str, public_url: str, audit_file: Path | None = None):
    audit_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentHelpNetworkTest/0.1"

        def respond(self, status: int, payload: object, content_type: str = "application/json"):
            body = (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if content_type == "application/json"
                else str(payload).encode("utf-8")
            )
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self.respond(
                    200,
                    "Agent Help Network connector test. Synthetic data only. "
                    "OpenAPI: /openapi.json. This server does not accept reports.",
                    "text/plain",
                )
                return
            if parsed.path == "/health":
                self.respond(200, {"status": "ok", "synthetic_only": True, "read_only": True})
                return
            if parsed.path == "/openapi.json":
                self.respond(200, openapi_spec(public_url))
                return
            if parsed.path == "/privacy":
                self.respond(
                    200,
                    "Connector test only. No user accounts or incident reports are collected. "
                    "The server sees requests needed to return synthetic responses. "
                    "Do not send personal data or production credentials.",
                    "text/plain",
                )
                return
            if parsed.path != "/v1/incidents/check":
                self.respond(404, {"error": "not_found"})
                return
            supplied_key = self.headers.get("X-API-Key", "")
            if supplied_key != api_key:
                self.respond(401, {"error": "invalid_api_key"})
                return
            values = parse_qs(parsed.query, keep_blank_values=True).get("site_host", [])
            if len(values) != 1 or not valid_host(values[0]):
                self.respond(400, {"error": "site_host_must_be_lowercase_hostname_only"})
                return
            result = check_result(values[0])
            if audit_file is not None and values[0] in TEST_HOSTS:
                audit_entry = {"checked_at": result["generated_at"], "site_host": values[0]}
                with audit_lock, audit_file.open("a", encoding="utf-8") as log:
                    log.write(json.dumps(audit_entry, separators=(",", ":")) + "\n")
            self.respond(200, result)

        def do_POST(self):
            self.respond(405, {"error": "read_only_test_server"})

        def log_message(self, format_string, *args):
            # Avoid printing query values or credentials to terminal logs.
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--audit-file", type=Path)
    args = parser.parse_args()
    api_key = args.key_file.read_text(encoding="utf-8").strip()
    if len(api_key) < 24:
        raise SystemExit("API key is missing or too short")
    public_url = args.public_url.rstrip("/")
    if not public_url.startswith("https://"):
        raise SystemExit("--public-url must be HTTPS")
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), make_handler(api_key, public_url, args.audit_file)
    )
    print(f"Agent Help Network test API listening on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
