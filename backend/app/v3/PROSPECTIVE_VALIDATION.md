# Prospective validation r1

The manifest in `runtime/v34_prospective_validation_r1/manifest.json` fixes the
cutoff permanently. Historical rows and outcomes are not rewritten. The sidecar
SQLite database records immutable observation, date/bucket and outcome identities.
Every observed UTC trading date counts, including dates with no eligible setups.
Late unknown dates and timestamp regressions fail closed; no performance-based
reassignment is permitted. Dates after the planned 80 remain blinded too.

The enforced research contract has `training_allowed=false`. `validate_vector`
requires its sealed contents, exact feature ordering, types and complete provenance.
Feature-level eligibility is not permission to fit a model. All unregistered
fields are denied; all historical 1,626 paths have a registry entry. Availability
must precede decision time. Downloading historical market data after a decision
does not establish that it was available to the collector at that decision.
Only deterministic calendar inputs can be independently reconstructed without
an earlier market-data capture. Derived indicators, macro releases/vintages,
cross-assets, single-tick flow proxies and zero-volume proxies remain excluded
until their actual dependencies can be demonstrated. No latest-value substitute
or inferred zero commission is allowed.

Strategy/policy source hashes are checked at startup and membership ingestion.
No strategy, threshold, RR, signal or session filter is changed. Freeze checks
cover the resolver's outcome semantics too. Collector timestamps and sidecar
provenance are metadata additions, not new signal inputs.

`ops/windows/v34-prospective-start.ps1` refuses existing workers or locks and
requires a fresh read-only MT5 preflight. It starts one logical collector and one
logical resolver, with no execution engine. A Python environment may have a
launcher plus child process; lock ownership identifies the logical worker.
Successful launch is not sustained health certification. Verify advancing
heartbeats, counts, completed M1 timestamps and memory over multiple cycles.
Do not remove a lock without independently proving its owner has exited.

Daily health output contains identities, counts and health only. The resolver
refreshes it each successful normal cycle; it can also be requested using
`v34_prospective_campaign.py health`. No background scheduler is required or
installed. An offline resolver does not generate daily reports automatically.

Routine recertification metrics exclude all observations at/after the campaign
cutoff. Legacy loss diagnostics refuse to run while the campaign exists.
Raw journals are retained unencrypted for integrity/recovery. Blinding is enforced
by supported reporting interfaces, not by OS access control against a developer
who deliberately opens raw outcome files. Archived one-off audit scripts are not
approved prospective reporting entry points. No holdout-unblind endpoint exists.

Future partitions: A=first40 dates, B=next10, C=next10, D=next20. Do not optimize
against B/C/D. Future model work must separately enforce decision-to-max-hold+60s
purging, a 24-hour embargo and authorization to train. This campaign does not
train or implement an authorized model-fitting path.
