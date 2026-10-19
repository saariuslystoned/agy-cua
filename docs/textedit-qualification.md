# Real TextEdit qualification recipe — currently blocked

The October 18 pilot used Antigravity CLI and the installed plugin against real
TextEdit. It did not qualify the save workflow. See the [retained results](../proof/textedit-20261018.md).
There is no qualified unattended executor in this change. The experimental runner
was withdrawn after review found transport-deadline and intermediate-receipt gaps;
its historical revisions remain in PR history and private run output.

This is the bounded recipe for a future supervised attempt after the menu-context
blocker is addressed, not a claim of successful replay.

1. Use an isolated checkout and a new private directory under ignored `runs/` per
   attempt. Establish expected seed/final bytes first. Keep `STATE.md`,
   `events.jsonl`, `heartbeat` and `PROOF.md` during a long run.
2. Verify `gemini-4-argon-low` with effort `low` and `gemini-3.8-flash-high` with
   effort `high` in the live `agy models` catalog. Keep plugin/Driver build, task,
   observation limits and timing boundary constant. Do not relabel old API
   `thinking_budget=512` results as Flash High.
3. Open a new synthetic seed with background `launch_app`. Compare window IDs
   before/after, bind the unique new seed ID to the exact task-created path, and
   pass `(pid, window_id)` explicitly to the planner. Ambiguous discovery stops
   before content capture. Matching titles alone do not establish ownership.
4. Create a new document through native File → New. Discover and inspect its
   window. Enter the draft below, then change `draft` to `ready`. Use guarded
   `set_value` on the observed editable field. `AXShowMenu` does not prove that
   Driver's `AXPress` click can focus a text area.
5. Compare the new document's formatting with its menu state before conversion.
   Stop on disagreement. The retained pilot stopped here: the rich-text document
   and the app-wide Format menu disagreed.
6. After that blocker is resolved, open Save and query the real sheet with
   `query: "Save", max_elements: 1`. If incomplete, obtain sufficient bounded
   evidence before acting. Cancel once, reobserve, reopen Save, and save only to
   a new unique file under the run root. Never overwrite or replay an uncertain
   action.
7. Keep private task-scoped receipts for each intermediate observation: query,
   budget, target/snapshot IDs, completeness, selection, dispatch and postcondition.
   Do not archive raw AGY conversations. Final screenshots or summaries without
   these bindings cannot prove recovery.
8. Run the independent byte check below. Audit the receipts, focus/pointer samples,
   prohibited actions, cancellation and model configuration separately. Correct
   file bytes alone cannot certify UI recovery or overall qualification.

Seed, including a final newline:

```text
Everyday workflow qualification
Status: draft
Next: review the synthetic note
```

The final note changes only `Status: ready`. Exact UTF-8 constants are in
[`scripts/verify_textedit.py`](../scripts/verify_textedit.py).

```sh
python3 scripts/verify_textedit.py \
  --seed runs/<attempt>/seed.txt --note runs/<attempt>/note.txt
```

This verifier reads only the two supplied files, emits hashes and boolean checks,
and writes nothing. Failed byte checks exit nonzero; symlinks are rejected;
`ui_recovery_verified` is always null.

For a future comparison, use two fresh repetitions per arm and reverse their
order in the second pair. Preserve failures and separate recipe changes. Restore
equivalent task-owned window state without touching user documents. Do not mix
these results with old API fixtures or native Codex timings.

The pilot capped Jev at four calls per attempt and retained five attempts. At the
[published Jev 1.13 price and 64k context limit](https://docs.typesafe.ai/models),
that ceiling was $0.05376; output is free. All retained attempts recorded zero Jev
provider calls. Actual billing receipts were unavailable. CLI subscription usage
is separate; no paid-overage setting changed. Future runs need their own approved
spend/attempt envelope and cumulative accounting across every accounting-bearing
receipt, including selections that fail or abstain.
