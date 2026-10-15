# Native Codex benchmark verification — 2026-10-15

Repo: `saariuslystoned/agy-cua`. Branch: `codex/issue-12-native-measurements`.
Worktree: `/Users/bobbybones/Developer/side-quests/agy-cua-worktrees/issue-12-native-measurements`.
Base: `88aa641ef66c4472f53454feb6165be2fb992ea5` (merged PR #14).

Authority: [measurement JSON](codex-native-20261015-measurements.json).
Executed source SHAs and per-row screenshot hashes are embedded there. The first
20 attempts and the four vault continuations use separate source revisions;
prior admission failures and the four unstarted blocked slots are retained.

## Executed checks

- `python3 -m unittest discover -v tests`: 80 tests passed, including actual
  JavaScript execution of the production native redaction helper with public and
  secure lines, rejected native errors, wrong-window/canary binding rejection,
  cumulative usage, actor/reroute invalidation and canary audit halting.
- `shellcheck bin/agy-cua scripts/*.sh`: passed.
- `git diff --check`: passed.
- Executed byte comparisons against the frozen base: shared harness, Swift fixture,
  focus probe and historical baseline summary unchanged. SHA-256 values are in JSON.
- Executed result assertions: 24 unique task/arm/repetition keys; 17 full passes;
  23 task goals; zero probe errors, reroutes, substitute actors and canary flags;
  all four mail summaries have `send_violation=False`; all four vault goals pass.
- Every measured final target screenshot exists and is hashed. The form screenshot
  was visually inspected and shows the submitted original fields/tier/key choice.
  Screenshots and fixture state remain ignored/private, with no raw transcripts.
- CLI/plugin inventory: Codex `0.161.0`; installed/enabled unified computer use
  `26.1007.71326`, computer use `1.0.1001425`; setup/oracle driver `0.35.4`.

The remaining seven full-rule failures are reported, not repaired/replaced: six
focus-only failures and one Astra failed turn before tool use. The latter has null
usage and unavailable error detail. No successful six-task Astra timing or dollar
cost is inferred from its failed-attempt total or partial token sum.

The original canary guard is unchanged. Vault setup calls are read-only native MCP
calls made before `turn/start`; their initial response is received only by this
client, audited in memory, and never copied into model input. Only filtered AX
state/documentation is exposed by the prepared observation route. No task input or
UI action is implemented in that helper. A live no-inference preflight and all four
measured final vault oracles passed.

Copilot review is tracked on the PR at its exact published head. This receipt
records local verification, and does not claim a review result before it exists.
