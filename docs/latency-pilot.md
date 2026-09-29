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

## Overhead and held-out checks

Summing measured MCP calls in the two revised fresh Calculator runs accounts
for 17.7 and 16.4 seconds of their 75.8 and 86.5 second totals. The residual
58.1 and 70.1 seconds includes model work, harness work, startup, and context
reads; it is not a measurement of inference alone. Baseline driver time was
similar. Two held-out High-profile problems, 39 × 26 and 57 × 18, also passed
at 86.2 and 84.0 seconds, with independently verified window images.

The CLI rejects `--model gemini-3.8-flash-high --effort low` as conflicting
settings. Two such attempts failed before model/tool execution; they are not
latency successes. Testing the separately advertised `gemini-3.8-flash-low`
profile produced correct arithmetic in 73.4 and 50.0 seconds, but the first
used broad app discovery and the second's initial window-discovery scope was
not retained by the original metadata collector. Neither is accepted as a
qualified optimization. The default remains High. High/Low condition order
was reversed across the two problems; the skill and driver were unchanged.

A TextEdit pilot changed a scratch document's Status field and increased its
Quantity, preserving the other visible text. The first High-profile attempt
performed the edit correctly in 78.0 seconds through confirmed background AX
`set_value`, but used broad app discovery and failed the named-app scope audit.
The follow-up skill clarification requires `launch_app` for a known bundle ID
and explicitly forbids broad discovery as a shortcut.

A fresh document variant passed with that clarification in 43.7 seconds:
five MCP calls, twelve model tool steps, zero tool/stream errors, and 69
identified foreground samples with no TextEdit-foreground sample. An independent
AX read and a window-only image verified both changed fields and the preserved
visible text. The receipt confirmed accessibility/background delivery; all
window observations and the write matched the owned process and window.
Different document contents, uncontrolled load, and one pair prevent attributing
the entire time difference to the instruction change. This is evidence for a
bounded text-editing workflow, not general TextEdit or desktop coverage. AX
omits the fixture's final newline, so the oracle compares the observed AX value,
not byte-for-byte file serialization. Documents were not saved or closed.

Two fixture-only stops were retained: Calculator's Clear/All Clear label and
TextEdit's AX newline representation. Both were corrected before model actions;
no mutations were replayed to obtain a passing screenshot. The later TextEdit
collector retains target-match booleans and delivery enums, without arguments,
document text, or raw model turns in its timing log.

The strongest demonstrated direction is smaller observations and fewer model
round trips while retaining independent verification. Lower reasoning effort
is unqualified here. Caller-side sequential orchestration evidence is posted
on [upstream #2794](https://github.com/trycua/cua/issues/2794#issuecomment-5895190857).
The later #4308 cursor comparison is recorded below; its driver-level results
are separate from these AGY measurements. Driver waits and freshness checks
remain unchanged.

The discovery clarification also passed the repository's randomized
qualification: observed 4,320 matched the expected product in 70 seconds,
16 tool calls, zero tool/stream errors, only the assigned server, and 112
identified focus samples with no Calculator-foreground samples. This is a
qualification result, not another controlled before/after pair.

## Refusal and recovery control

On the same owned TextEdit document, a raw public-MCP probe obtained two
successive snapshots, then submitted a write using the superseded snapshot.
Driver 0.30.4 refused it in approximately 1 ms with
`element_token is stale; call get_window_state again to refresh`. A fresh
independent connection confirmed that the document had not changed. The probe
then obtained a fresh snapshot and changed only Status from reviewed to
validated; the driver reported confirmed accessibility/background delivery,
and an independent read plus window image verified the result. All twelve
focus samples identified a foreground app other than TextEdit.

This tests explicit snapshot supersession and recovery on a native text field.
It does not test model recovery, same-label target replacement without a new
snapshot, operator intervention between sampling intervals, or `batch_actions`.
The expected refusal is a passing negative control, not an ignored task failure.

## Upstream cursor candidate: native Mac evidence

Released Driver 0.30.4 was compared with upstream #4308 at exact source
`e923309280ee4961e79baa195c2c910ed8dfb453`, built as a separate local app.
The source build reports 0.30.3; the SHA identifies the tested candidate.
These are release-versus-PR binaries, not identically packaged builds.

| Window / cursor setting | Release median click | Candidate median click | Clicks per binary |
| --- | ---: | ---: | ---: |
| Primary / default | 2.264 s | 2.282 s | 6 |
| Primary / runtime disabled | 2.285 s | 1.137 s | 6 |
| Negative-coordinate display / default | 3.815 s | 2.279 s | 3 |
| Negative-coordinate display / runtime disabled | 3.784 s | 1.150 s | 3 |

Each trial used a named MCP session and three already-observed Calculator
buttons, 7, 8, 9. Timing covers individual MCP clicks and excludes configuration,
reset, observations, and independent verification. Runtime disable used the
public session cursor setting; automatic motion remained the default. Primary
conditions were repeated in reversed mode order and binary order was
release/candidate/candidate/release. Negative-coordinate cases ran only once
per condition, release first. Load was uncontrolled.

All twelve trials and 36 actions passed independent display verification and
background accessibility receipt checks. All six window restorations were
verified. Window-only images were checked, and none of 195 identified focus
samples showed Calculator foreground. Sampling can miss brief transitions.
No driver wait, target freshness, or permission protection was weakened.
Earlier incomplete release probes remain recorded: one fixture-label stop and
one missing capture/failed automatic restoration, followed by a separately
verified restoration. Their causes were not isolated.

This demonstrates a driver-level latency improvement when the candidate honors
runtime cursor disable, plus an improvement on this negative-coordinate display.
It does not prove the visual cursor trajectory or combined AGY whole-task gain.
The installed driver was not replaced and no upstream patch was copied downstream.

## Consolidation recommendation

Use CUA as the maintained driver and keep AGY integration thin. Retain Gemini
3.8 Flash High as the verified default, carry forward the scoped-discovery and
observation guidance, and use resumed context when appropriate while refreshing
all UI handles. Low reasoning remains unqualified in this pilot. Within-25%
Astra parity has not been demonstrated.

The strongest contribution niche is macOS background execution performance
with discriminating correctness evidence: bounded sequential actions, target
freshness/refusal, operator intervention, and multi-display cursor behavior.
The existing [#2794 batching proposal](https://github.com/trycua/cua/issues/2794#issuecomment-5895190857) and [#4308 cursor fix](https://github.com/trycua/cua/pull/4308#issuecomment-5895841986) are concrete places
to contribute, rather than a second driver or permanent downstream fork.
A future upstream release containing #4308 may reduce action time; combining
that with fewer model round trips still needs an end-to-end AGY measurement.
