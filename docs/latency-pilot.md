# Background Calculator latency pilot

Measured September 29, 2026 on macOS 26.5.2 arm64, AGY CLI 1.2.13,
`gemini-3.8-flash-high`, released Cua Driver 0.30.4. Both skill variants use the
same pass-through shim and driver. Baseline: repository commit
`94f9911ef20700ab93c747980c3ae5d25e7dfe90`.

The revised skill uses existing `get_window_state.query` to select controls
and read back the actual display, avoids rereading known schemas, and asks for
a concise final answer. It adds no driver behavior or MCP tool.

| Skill / conversation | Passed | Median seconds | Median model tool steps | Median MCP response bytes |
| --- | ---: | ---: | ---: | ---: |
| baseline-fresh | 2/2 | 134.3 | 21 | 166,680 |
| baseline-resumed | 2/2 | 68.9 | 12 | 134,706 |
| filtered-fresh | 2/2 | 81.2 | 15.5 | 19,171 |
| filtered-resumed | 2/2 | 59.7 | 10 | 18,080 |

The two pairs use 27 × 43 and 68 × 24 in reversed fresh/resumed order. A resumed
conversation retains context but starts a new CLI process and MCP connection;
it is not a persistent-process latency claim. Baseline pairs ran before revised
pairs, so time-of-run effects were not counterbalanced. The operator continued
working. Four runs per skill are a pilot, not a population latency estimate.
The same model and prompt were used; each workspace contained its assigned skill.

All eight final displays matched a fresh observer connection, with window-only
screenshots retained locally. No scored run sampled Calculator in front. Focus
sampling can miss brief changes. Each action was an element click; screenshots
and fixture reset were outside the timed interval. Timing includes CLI startup,
model work, schema/result reads, and MCP calls. Other global tools could be
visible, but actual tool use was audited against the assigned server.

A separate observation probe returned 14,439 text characters for the full tree,
1,649 for `query: "AXButton"`, and 156 for `query: "AXStaticText"`; both relevant
controls and actual display were checked. All three AX reads took about 250 ms.
The filter reduces response size; it does not eliminate the underlying walk.
The static-text probe queried the role, not the expected answer.

The wire-byte totals include structured content as well as text. They are not
model-visible token counts. Fresh runs improved in both comparisons; one resumed
run was slightly slower, so the data does not support a universal speedup claim.

## Reproduce

Run `bin/agy-cua qualify --model gemini-3.8-flash-high` for the repository's
randomized end-to-end qualification. Compare fresh conversations with explicitly
resumed conversation IDs in separate workspaces containing the respective skill.
For a controlled latency comparison, time actual MCP calls separately from the
whole AGY turn, reset Calculator outside timing, and independently inspect its
final display. Do not accept agent self-report as correctness evidence.

The qualification collector retains tool metadata and usage counters rather
than raw AGY conversations. Generated runs, window images, and machine-specific
identities remain local and are not part of this document.

## Repository qualification

`bin/agy-cua qualify --model gemini-3.8-flash-high --timeout 4m` passed on the
revised skill: independently observed 1,769, matching the randomized expected
product; 79 seconds, 16 tool calls, zero tool/stream errors, only the assigned
agy-cua server, and 123 identified focus samples with zero Calculator-foreground
samples. The separate metadata collector retained no raw AGY turns.
