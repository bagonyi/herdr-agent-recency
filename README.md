# Agent Recency for Herdr

Lists the agents in Herdr's agents panel by their last activity, latest
first, and shows when that was:

```
 agents            recent
 ● gamma · 14:32
 ○ beta · 14:05
 ○ alpha · Mon 17:40
```

An agent's activity is a change of its state: it starts working, finishes, or
stops to ask you something. Looking at a finished agent isn't activity, and
neither is its output while it works, so a working agent's time is when it
started. The time reads 14:32 for today, Mon 14:32 for the past week and
12 Oct before that.

## How it works

Herdr runs the plugin when an agent's status changes, when a pane, tab or
space closes, and once at startup. There is no background process, and while
one run waits for its turn, further events start none, so a burst of them, as
after a server restart, doesn't crowd out other plugins' commands. Each run
checks every agent against the times it has kept, so a missed event is
corrected on the next run, and reports each agent's time to Herdr as two pane
tokens:

- `$last`: the time as shown. It expires at midnight, so a time from yesterday
  never reads as today's; the next run reports it again as `Mon 14:32`.
  Between midnight and that run the agent shows no time.
- `$last_at`: the same time in seconds, which the agents panel sorts by.

The plugin also sets the agents panel's order (Herdr's agent views), which
the panel names `recent` where it otherwise says `grouped` or `priority`.
Agents without a time follow those with one, in Herdr's own order of state
changes. The order also applies to the previous/next agent keys.

The plugin keeps each pane's last state and time in its state directory
(`~/.local/state/herdr/plugins/bagonyi.agent-recency/activity.json` by
default), so times and order survive a server restart, which clears Herdr's
tokens. A pane's entry goes when the pane closes.

## Showing the time

Add `$last` to the agent rows in Herdr's `config.toml`, e.g.:

```toml
[ui.sidebar.agents]
rows = [["state_icon", "workspace", "tab", "$last"], ["terminal_title_stripped"]]
```

then reload the config (`prefix+shift+r`).

On [Herdrsson](https://github.com/bagonyi/herdrsson/blob/herdrsson/FORK.md), the time can
instead show as the time since then, counting up as you watch (`10s ago` in steps of 10
seconds, `5m ago`, `3h ago`, `2d ago`; `working` while the agent works), by putting
`$last_at` in the row with `format = "ago"`:

```toml
[ui.sidebar.agents]
rows = [["state_icon", "workspace", "tab", { token = "$last_at", format = "ago" }]]
```

Unlike `$last`, it doesn't expire at midnight. Stock Herdr rejects `format` in the config.

## Turning the order off

The panel's own `grouped`/`priority` switch has no effect while the plugin
orders it. To go back to that order, or to the plugin's again:

```sh
herdr plugin action invoke bagonyi.agent-recency.toggle-order
```

It works on the session it runs in and stays as set until toggled again.
The times keep updating either way.

## Requirements

- Herdr 0.9.3 or later
- macOS or Linux
- `python3` on `PATH` (standard library only)

Tested on macOS with Herdr 0.9.5.

## Install

```sh
herdr plugin link /path/to/agent-recency
```

Herdr runs no startup hook when a plugin is linked: the order and times start
with the next agent status change. As the plugin knows no earlier activity,
only that agent gets a time then; the others get theirs as they next change.

## Limitations

- **Times are when the plugin noticed the change**, a fraction of a second
  after Herdr did.
- **An agent that exits and starts again in the same pane** keeps its time
  unless its state differs from before (e.g. it starts working).
- **After a server restart**, an agent that comes back in a different state
  than it had (e.g. it resumes working) gets the restart's time.

## Uninstall

```sh
herdr plugin unlink bagonyi.agent-recency
```

Herdr drops the plugin's order at once. The `$last` times stay until they
expire at midnight or the server restarts; remove `$last` from the agent rows
too.
