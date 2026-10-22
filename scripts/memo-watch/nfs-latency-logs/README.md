# Raw arms — `nfs-latency-probe` series

One gzipped tarball per arm, six host logs each, format `<iso8601> <host> <rc> <elapsed_ms>`.

⛔ **These are here because the window cannot be re-run.** Arm A is the 09:29–16:00 EDT
market-hours window of 2026-10-22 with a31 RUNNING; the comparison arm is a different day,
so a lost arm costs a day and a changed pause policy can cost it permanently. The logs are
written host-local to `~/.memo-watch/nfs-latency/` and that path is cleaned.

Re-analyse with `../nfs-latency-episodes.py <extracted>/*.log [--cadence-control]`.
Summary and the pooling rules: `../nfs-latency-results.md`.
