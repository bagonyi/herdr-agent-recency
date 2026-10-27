#!/usr/bin/env python3
"""List Herdr's agents by their last activity, and show when that was.

An agent's activity is a change of its state: it starts working, finishes,
or stops to ask you something. Looking at a finished agent isn't activity,
and neither is its output while it works, so a working agent's time is when
it started. Each change gets the time this script notices it.

Agent rows show the time as the `$last` token: 14:32 today, Mon 14:32 in the
past week, 12 Oct before that. Each label is reported to expire at the next
midnight, so it never shows yesterday's time as today's; the next run
reports it again in its new form. The agents panel lists the agent with the
latest activity first, by the `$last_at` token (seconds since the epoch).
Agents without a time follow, in Herdr's own order of state changes.

Herdr runs this script on agent status changes, closed panes, tabs and
spaces, and at startup. Every run reconciles all agents from Herdr's current state instead
of acting on the single event, so a missed event is corrected by the next
run. While one run waits for its turn, further events start none (see
turn). Times are kept by pane in the state directory, so they survive a server
restart, which drops Herdr's tokens and its agents panel order. The first
run in a session knows no agent's last activity, only that the agent whose
change started it changed just now: the others get a time once they next
change.

Usage:
  agent_recency.py           reconcile every agent's time, and the order
  agent_recency.py --toggle  stop ordering the agents panel by last activity
                             in this session, or start again, then reconcile
"""
import fcntl
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

# Herdr's agent status -> its activity state. "done" is idle and not yet
# seen, so seeing a finished agent changes nothing. Agents in any other
# status ("unknown") are left as they are.
ACTIVITY = {
    "idle": "idle",
    "done": "idle",
    "working": "working",
    "blocked": "blocked",
}

PLUGIN_ID = os.environ.get("HERDR_PLUGIN_ID", "bagonyi.agent-recency")
# Owns the tokens and the agents panel order. Herdr drops a plugin's order
# when the plugin is disabled or unlinked.
SOURCE = f"plugin:{PLUGIN_ID}"
LABEL_TOKEN = "last"
TIME_TOKEN = "last_at"
VIEW = {
    "source": SOURCE,
    # Shown where the agents panel names its order.
    "label": "recent",
    "sort": [
        {"field": {"token": TIME_TOKEN}, "order": "desc"},
        {"field": "state_change_seq", "order": "desc"},
    ],
}

STATE_DIR = os.environ.get("HERDR_PLUGIN_STATE_DIR", ".")
SESSION = os.environ.get("HERDR_SOCKET_PATH", "")
# Session socket -> pane id -> {"status": activity, "at": seconds}. Every
# session shares this state directory and pane ids repeat between sessions,
# so entries are kept per session. "at" is missing until the pane's first
# change after the session's first run.
ACTIVITY_FILE = os.path.join(STATE_DIR, "activity.json")
# Session sockets whose agents panel keeps Herdr's own order.
ORDER_OFF_FILE = os.path.join(STATE_DIR, "order_off.json")


def herdr(*args):
    """Run a Herdr CLI command and return its parsed JSON result, if it
    prints one."""
    binary = os.environ.get("HERDR_BIN_PATH", "herdr")
    completed = subprocess.run(
        [binary, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)["result"] if completed.stdout.strip() else None


def request(method, params):
    """Send one request over Herdr's socket and return its result.

    The CLI has no command for the agents panel order.
    """
    line = json.dumps({"id": "agent-recency", "method": method, "params": params})
    reply = b""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(SESSION)
        sock.sendall(line.encode() + b"\n")
        while not reply.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            reply += chunk
    response = json.loads(reply)
    if "error" in response:
        raise RuntimeError(f"{method}: {response['error']}")
    return response["result"]


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def time_label(at, now):
    """14:32 for today, Mon 14:32 in the past week, 12 Oct before that."""
    then = datetime.fromtimestamp(at)
    days = (datetime.fromtimestamp(now).date() - then.date()).days
    if days <= 0:
        return then.strftime("%H:%M")
    if days < 7:
        return then.strftime("%a %H:%M")
    return f"{then.day} {then.strftime('%b')}"


def ms_until_midnight(now):
    current = datetime.fromtimestamp(now)
    midnight = datetime.combine(current.date() + timedelta(days=1), datetime.min.time())
    return max(1, int((midnight - current).total_seconds() * 1000))


def report_time(agent, at, now):
    """Bring the agent's two tokens up to date, reporting only what changed."""
    pane_id, tokens = agent["pane_id"], agent.get("tokens") or {}
    at_value = f"{int(at):010d}"
    if tokens.get(TIME_TOKEN) != at_value:
        herdr("pane", "report-metadata", pane_id, "--source", SOURCE,
              "--token", f"{TIME_TOKEN}={at_value}")
    label = time_label(at, now)
    if tokens.get(LABEL_TOKEN) != label:
        herdr("pane", "report-metadata", pane_id, "--source", SOURCE,
              "--token", f"{LABEL_TOKEN}={label}",
              "--ttl-ms", str(ms_until_midnight(now)))


def order_on(toggle):
    """Whether this session's agents panel is ordered by last activity,
    after this run's --toggle."""
    off = set(load_json(ORDER_OFF_FILE, []))
    if toggle:
        off ^= {SESSION}
        save_json(ORDER_OFF_FILE, sorted(off))
    return SESSION not in off


@contextmanager
def turn(coalesce):
    """Wait for this run's turn, as Herdr may start several hooks at once,
    and yield whether to run.

    With `coalesce`, a run gives up while another one is already waiting for
    its turn: that one starts once the current run ends, and reconciles all
    that has happened by then. So a burst of events, as when a restarted
    server finds its agents again, starts at most two runs at a time instead
    of one per event. Herdr runs at most 32 plugin commands at once, across
    all plugins.
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "waiting.lock"), "w") as waiting, open(
        os.path.join(STATE_DIR, "reconcile.lock"), "w"
    ) as running:
        try:
            fcntl.flock(waiting, fcntl.LOCK_EX | (fcntl.LOCK_NB if coalesce else 0))
        except BlockingIOError:
            yield False
            return
        fcntl.flock(running, fcntl.LOCK_EX)
        # The next run can wait behind this one now.
        fcntl.flock(waiting, fcntl.LOCK_UN)
        yield True


def event_pane(event):
    """The pane whose agent's status change started this run, if one did."""
    if event != "pane.agent_status_changed":
        return None
    try:
        return json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))["data"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return None


def reconcile(event):
    now = time.time()
    changed_pane = event_pane(event)
    activity = load_json(ACTIVITY_FILE, {})
    first_run = SESSION not in activity
    panes = activity.get(SESSION, {})

    # Every run looks, as the run for a close event may have been skipped.
    if panes:
        existing = {pane["pane_id"] for pane in herdr("pane", "list")["panes"]}
        panes = {pane_id: entry for pane_id, entry in panes.items() if pane_id in existing}

    for agent in herdr("agent", "list")["agents"]:
        status = ACTIVITY.get(agent["agent_status"])
        if status is None:
            continue
        entry = panes.get(agent["pane_id"])
        if entry is None or entry["status"] != status:
            entry = {"status": status}
            if not first_run or agent["pane_id"] == changed_pane:
                entry["at"] = int(now)
            panes[agent["pane_id"]] = entry
        if "at" in entry:
            report_time(agent, entry["at"], now)

    activity[SESSION] = panes
    save_json(ACTIVITY_FILE, activity)


def run(toggle):
    try:
        reconcile(os.environ.get("HERDR_PLUGIN_EVENT"))
    finally:
        # Set again on every run: Herdr forgets it when its server
        # stops, and runs no startup hook when the plugin is linked.
        if order_on(toggle):
            request("agent.view.set", VIEW)
        else:
            request("agent.view.clear", {"source": SOURCE})


if __name__ == "__main__":
    toggle = "--toggle" in sys.argv[1:]
    # A toggle always runs: a waiting run wouldn't make its change.
    with turn(coalesce=not toggle) as my_turn:
        if my_turn:
            run(toggle)
