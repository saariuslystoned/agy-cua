# Real TextEdit qualification

This pilot uses the Antigravity CLI, the installed agy-cua plugin, and the already
running Cua Driver. It does not qualify the Antigravity IDE or native Codex.

Run `scripts/qualify_textedit.py` from an isolated checkout after approving live
TextEdit use and model quota. The runner does not install or restart anything.
It checks the installed plugin against a committed `--plugin-ref`, opens one
new synthetic seed file, and asks the planner to create and edit a separate
document through TextEdit controls. A preexisting file is never the output.

```sh
python3 scripts/qualify_textedit.py \
  --model gemini-4-argon-low --repetition 1 \
  --plugin-ref <installed-plugin-commit> --out runs/textedit-pilot/argon-r1
```

Use `gemini-3.8-flash-high` with effort `high` for the other arm. The runner sets
effort `low` for Argon Low. Use the same recipe for two repetitions of each arm,
reversing arm order in the second pair. Keep diagnostic attempts made before a
recipe change separate and retain their failures. Do not combine these timings
with the API fixtures or earlier six-task native Codex measurements.

The expected note is established before the run. Acceptance requires all of:

- The new output's bytes exactly match `NOTE`; the seed still matches `SEED`.
- Fresh target observations establish the new document and actual save sheet.
- The deliberately restricted save-sheet query is reported incomplete, followed
  by a sufficient observation before action. The first sheet is cancelled and
  the next save completes. Window discovery alone cannot satisfy this check.
- No uncertain action is replayed, no halted controller is bypassed, and the
  target is never brought to the foreground by an action.
- The chosen model IDs match the live CLI catalog; the configured effort and
  provider/deterministic decisions are reported honestly.

The runner always emits `NEEDS_EVIDENCE_REVIEW`, even if its byte check passes.
Review the private observations, controller receipts and screenshots before
assigning a final verdict. An agent's `SUCCESS` response is not acceptance.
`STATE.md`, `events.jsonl`, `heartbeat` and `PROOF.md` live with each attempt.
Keep those artifacts, screenshots and raw controller receipts under ignored
`runs/`; publish only sanitized measurements and reproducible source.

The installed controller limits Jev to four calls per attempt with no automatic
retry. At the [published Jev 1.13 price and 64k context limit](https://docs.typesafe.ai/models),
that is at most $0.010752 per attempt, including uncertain calls conservatively
reserved at full context. Output is free. Limit a qualification batch to four
attempts and at most one retained diagnostic ($0.05376 upper bound). Actual
billing receipts are not exposed, so reported actual dollars are null. Antigravity
CLI subscription usage is separate; this runner does not enable paid overages or
use the Gemini API. Stop on an actual quota/billing refusal.

Planner-only latency, image delivery/replay counts and provider-side model
identity are unavailable in the current CLI summary. Keep them null rather than
inferring them from total time, requested settings or screenshot file counts.
Focus and pointer samples include operator activity; movement alone cannot be
attributed to the agent. Preserve those limits in the report.
