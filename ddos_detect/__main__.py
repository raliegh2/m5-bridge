"""Command-line interface.

    python -m ddos_detect preflight            # report security posture
    python -m ddos_detect adduser --admin      # create the first account
    python -m ddos_detect authorize --cidr ... # record an authorized range
    python -m ddos_detect serve                # run the dashboard
    python -m ddos_detect scenario syn_flood   # offline detector check

Account creation, authorization, and revocation are exposed here as well as in
the API so that an operator can bootstrap and audit the system without the web
interface being reachable at all.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import signal
import sys
import time
from typing import Sequence

from .app import Application
from .config import Settings
from .authz import ATTESTATION_TEXT
from .detector import Detector
from .errors import DdosDetectError
from .simulate import SCENARIO_HELP, SCENARIOS, build_scenario


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ddos_detect",
        description="Defensive DDoS detection with a local dashboard.",
    )
    parser.add_argument("--verbose", action="store_true", help="log at DEBUG level")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight", help="report capture readiness and security posture")
    sub.add_parser("serve", help="run the dashboard server")

    p_user = sub.add_parser("adduser", help="create a dashboard account")
    p_user.add_argument("--username", required=True)
    p_user.add_argument("--role", choices=("admin", "operator", "viewer"), default="viewer")
    p_user.add_argument("--admin", action="store_true", help="shorthand for --role admin")
    p_user.add_argument("--password-stdin", action="store_true",
                        help="read the password from stdin instead of prompting")

    p_passwd = sub.add_parser("passwd", help="change an account password")
    p_passwd.add_argument("--username", required=True)
    p_passwd.add_argument("--password-stdin", action="store_true")

    sub.add_parser("users", help="list accounts")

    p_auth = sub.add_parser("authorize", help="record authorization for a range")
    p_auth.add_argument("--cidr", required=True, help="address or CIDR range")
    p_auth.add_argument("--justification", required=True)
    p_auth.add_argument("--days", type=int, default=30)
    p_auth.add_argument("--actor", default="cli")
    p_auth.add_argument("--i-am-authorized", action="store_true",
                        help="required attestation: " + ATTESTATION_TEXT)

    p_revoke = sub.add_parser("revoke", help="revoke an authorization entry")
    p_revoke.add_argument("--id", type=int, required=True)
    p_revoke.add_argument("--actor", default="cli")

    p_list = sub.add_parser("authorizations", help="list authorization entries")
    p_list.add_argument("--all", action="store_true", help="include expired and revoked")

    p_audit = sub.add_parser("audit", help="print the audit log and verify its chain")
    p_audit.add_argument("--limit", type=int, default=50)

    p_scenario = sub.add_parser(
        "scenario",
        help="run a generated scenario through the detector offline (no capture, no traffic)",
    )
    p_scenario.add_argument("name", choices=SCENARIOS)
    p_scenario.add_argument("--target", default="192.0.2.10")
    p_scenario.add_argument("--json", action="store_true", help="emit machine-readable output")
    p_scenario.add_argument("--seed", type=int, default=1337)

    sub.add_parser("scenarios", help="describe the available scenarios")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.command == "scenarios":
        for name in SCENARIOS:
            print(f"{name:<16} {SCENARIO_HELP[name]}")
        return 0
    if args.command == "scenario":
        return run_scenario(args)

    try:
        settings = Settings.from_env()
    except DdosDetectError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    app = Application.build(settings)
    try:
        return dispatch(args, app)
    except DdosDetectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if args.command != "serve":
            app.close()


def dispatch(args, app: Application) -> int:
    if args.command == "preflight":
        print(json.dumps(app.preflight(), indent=2, default=str))
        return 0

    if args.command == "adduser":
        role = "admin" if args.admin else args.role
        password = _read_password(args.password_stdin, confirm=True)
        name = app.auth.create_user(args.username, password, role, actor="cli")
        print(f"created {name} with role {role}")
        return 0

    if args.command == "passwd":
        password = _read_password(args.password_stdin, confirm=True)
        app.auth.set_password(args.username, password, actor="cli")
        print(f"password updated for {args.username}; existing sessions were revoked")
        return 0

    if args.command == "users":
        for user in app.auth.list_users():
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(user["last_login"])) \
                if user["last_login"] else "never"
            print(f"{user['username']:<20} {user['role']:<9} last login: {last}")
        return 0

    if args.command == "authorize":
        if not args.i_am_authorized:
            print("refusing without the attestation flag --i-am-authorized.\n"
                  f"  {ATTESTATION_TEXT}", file=sys.stderr)
            return 2
        entry = app.ledger.grant(
            args.cidr, args.justification, args.actor,
            attestation=True, days=args.days,
        )
        print(json.dumps(entry, indent=2, default=str))
        return 0

    if args.command == "revoke":
        if app.ledger.revoke(args.id, args.actor):
            print(f"revoked authorization {args.id}")
            return 0
        print("no such active authorization", file=sys.stderr)
        return 1

    if args.command == "authorizations":
        entries = app.ledger.list(include_inactive=args.all)
        if not entries:
            print("no authorization entries")
        for entry in entries:
            expiry = time.strftime("%Y-%m-%d", time.localtime(entry["expires_at"]))
            status = "revoked" if entry.get("revoked_at") else f"expires {expiry}"
            print(f"[{entry['id']:>3}] {entry['cidr']:<20} {entry['scope']:<9} {status:<20} "
                  f"{entry['justification']}")
        return 0

    if args.command == "audit":
        ok, detail = app.audit.verify_chain()
        print(f"chain: {'OK' if ok else 'FAILED'} - {detail}\n")
        for record in reversed(app.audit.read(args.limit)):
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.ts))
            print(f"{record.seq:>5} {when} {record.actor:<16} {record.action:<26} "
                  f"{json.dumps(record.detail, default=str)}")
        return 0

    if args.command == "serve":
        return serve(app)

    return 2


def serve(app: Application) -> int:
    from .server import serve as build_server

    if not app.auth.has_users():
        print("no accounts exist yet. Create one first:\n"
              "  python -m ddos_detect adduser --username you --admin", file=sys.stderr)
        return 2

    posture = app.preflight()
    server = build_server(app)
    url = f"http://{app.settings.bind_host}:{server.bound_port}/"
    print(f"dashboard: {url}")
    print(f"authorization enforced: {posture['authorization_enforced']}   "
          f"public targets: {posture['public_targets_allowed']}   "
          f"capture ready: {posture['capture'].get('privileged')}")
    if not posture["audit_chain_ok"]:
        print(f"WARNING: audit chain verification failed: {posture['audit_chain_detail']}",
              file=sys.stderr)

    def shutdown(signum, frame) -> None:  # pragma: no cover - signal path
        print("\nshutting down...")
        server.shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, shutdown)
        except (ValueError, AttributeError, OSError):
            pass
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        server.server_close()
        app.close()
    return 0


def run_scenario(args) -> int:
    """Replay a generated scenario through the detector, offline.

    No socket is opened and no packet is transmitted; this reads generated
    records and prints what the detector concluded. It is the quickest way to
    confirm the rules behave before pointing anything at a real network.
    """
    scenario = build_scenario(args.name, args.target, seed=args.seed)
    detector = Detector(target=scenario.target, started_at=0.0)
    evaluations = []
    index = 0
    records = scenario.records
    for second in range(int(scenario.duration)):
        while index < len(records) and records[index].ts < second + 1:
            detector.observe(records[index])
            index += 1
        evaluations.append(detector.evaluate(float(second) + 1.0))

    severity_rank = ("none", "low", "medium", "high", "critical")
    alerting = [e for e in evaluations if e.severity != "none"]
    peak_severity = max((e.severity for e in alerting), key=severity_rank.index, default="none")
    result = {
        "scenario": args.name,
        "target": scenario.target,
        "packets": scenario.packet_count,
        "attack_window": [scenario.attack_start, scenario.attack_end],
        "alerting_seconds": len(alerting),
        "first_alert_at": alerting[0].ts if alerting else None,
        "detection_latency_seconds": (
            alerting[0].ts - scenario.attack_start if alerting else None
        ),
        "classification": alerting[0].classification if alerting else "none",
        "label": alerting[0].label if alerting else "none",
        "peak_severity": peak_severity,
        "peak_score": round(max((e.score for e in evaluations), default=0.0), 3),
        "peak_pps": round(max((e.metrics["pps"] for e in evaluations), default=0.0), 1),
    }
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"scenario         {args.name}: {SCENARIO_HELP[args.name]}")
    print(f"target           {scenario.target}")
    print(f"packets          {scenario.packet_count:,}")
    print(f"attack window    t+{scenario.attack_start:.0f}s to t+{scenario.attack_end:.0f}s")
    print(f"peak rate        {result['peak_pps']:,.0f} pkt/s")
    print(f"peak score       {result['peak_score']}")
    if alerting:
        first = alerting[0]
        print(f"detected         {first.label} at t+{first.ts:.0f}s "
              f"({result['detection_latency_seconds']:.0f}s after onset)")
        print(f"peak severity    {peak_severity}")
        print(f"alerting for     {len(alerting)}s")
        print(f"guidance         {first.advice}")
        top = ", ".join(f"{src}({count})" for src, count in first.top_sources[:3])
        print(f"top sources      {top}")
    else:
        print("detected         nothing - no alert raised")
    return 0


def _read_password(from_stdin: bool, confirm: bool = False) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("password: ")
    if confirm and getpass.getpass("confirm: ") != password:
        raise DdosDetectError("passwords did not match")
    return password


if __name__ == "__main__":
    raise SystemExit(main())
