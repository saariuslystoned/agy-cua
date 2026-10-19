# TextEdit document targeting — guarded refusal, workflow blocked

The app-wide Format menu was bound to the plain-text sibling, not the requested rich-text document. This is now demonstrated by independent AXFocusedWindow/AXMainWindow and AXDocument URL reads, window-scoped screenshots, and the installed Driver's refusal. It is no longer only the inference retained in the [October 18 report](textedit-20261018.md).

The [upstream repair PR](https://github.com/trycua/cua/pull/5514) adds a conservative guard. Its private native candidate refused the mismatched document before menu lookup and preserved both documents. **The everyday workflow is still not qualified:** even a correctly bound Format command remains disabled while TextEdit is in the background. No edit/save/cancel/reopen workflow or four-model rerun is claimed.

[Sanitized assertions, identities and timings](textedit-document-targeting-20261019-summary.json) accompany this report. Raw AX/window IDs, screenshots and transport receipts remain in ignored local run output. No raw Antigravity conversation was read or archived.

## What was tested

1. Open task-created RTF and UTF-8 siblings in a fresh TextEdit instance with process-local restoration suppression. Independently bind their AXDocument URLs to the discovered window IDs. The RTF exposes a ruler/Bold controls; the text file does not. The plain sibling is AXFocusedWindow and AXMainWindow. TextEdit is behind the user's app.
2. Address the **rich window** with Format → Make Plain Text on installed Driver 0.35.4. It returns `menu_path_unavailable` and lists Make Rich Text. An independent AX menu read lists the same items and identifies the plain sibling as the document context. A separate Make Rich Text probe returns `background_unavailable`; no final menu action is dispatched. Both source files and observed context remain unchanged.
3. After the operator reported closing task documents, invalidate the first pair's live handles. Open a new pair for all candidate evidence; do not reuse the interrupted pair or mix its timing with the candidate.
4. Run the production candidate through `cua-driver mcp --direct`, with existing host Accessibility permissions and process-only telemetry/update-check opt-outs. It reports source `0364597d88946626ccd8e90478adcfd69c42d92a` and the binary hash in the JSON summary. The shared signed Driver continues running unchanged.
5. Address the new rich sibling with Make Plain Text: `window_not_key`, `effect: refused`, and the independently observed plain sibling's context. No menu action is dispatched. Address the correctly bound plain window with an intentionally absent label: `menu_path_unavailable`, proving it reaches native path lookup. Address it with the actual Make Rich Text command: `background_unavailable`, the remaining OS enablement blocker.
6. Independently verify both document URLs, AX focus/main context, format controls, exact file bytes, and fresh post-observation snapshot IDs/invalidation. The plain before/after screenshot hashes match; rich screenshots both visibly retain the rich document and formatting controls. Twenty-four focus samples contain zero target-frontmost samples and zero errors. Pointer preservation/attribution is not established.

The upstream guard checks both focused and main-window IDs before resolving a background menu, then checks again immediately before its final AX action. It uses the existing per-PID mutation coordinator. It retains enablement priming for an already-bound document and existing exact-file Save fallback. It does not switch an unbound document into place. An unreadable context is refused too. External focus changes cannot be made atomic with an AX action; this is not a claim that AX equality proves every app's menu semantics.

## Identities, tests and review

- agy-cua base: `003d65ded36a7a7c22b18af5dbf03fce67bf8465`; no downstream driver reimplementation or unattended executor was added. The shipped read-only byte verifier is unchanged.
- Shared installed Driver: `0.35.4`, source `d74a9cab3fb2e80ab7f0cef47025ec828586ea75`, executable SHA-256 `d0fff7f17a79ddf862ae187fa828f83c77de43c078d4964470e9ad93f5f1e88f`.
- Upstream base: `46907096b3511b13db00150f197647800871a72d`; candidate: `0364597d88946626ccd8e90478adcfd69c42d92a`, reported version `0.36.0`, binary SHA-256 `be3034ce4fce58c1dd81222312bccf649027d28b14e3ce53d4f6be979b7fbc89`. This is private source-candidate proof, not an installed-product qualification.
- Native tests on pinned Rust 1.97.1: **630 passed, 7 opt-in tests ignored**. `cargo fmt --all -- --check` and `git diff --check` pass. The inherited Rust 1.94.1 run also passed before switching to the repository pin. Existing unrelated native warnings remain.
- agy-cua tests: **97 passed**; `shellcheck bin/agy-cua scripts/*.sh` passes. The private single-call transport passes completion, JSON-RPC error, EOF, and actual deadline tests. Its first smoke used an over-short initialization budget; the final test separates bounded initialization from the action deadline.
- Upstream PR is a draft. GitHub rejected the author's attempt to request Copilot review because the account lacks `RequestReviews` permission in `trycua/cua`. Upstream maintainer review and CI acceptance remain open; no self-merge or reviewed-repair claim.

## Qualification boundary and next action

The current catalog contains `gemini-4-argon-low` and `gemini-3.8-flash-high`; CLI version is 1.4.2. No planner workflow trial was started after the driver prerequisite remained blocked. There is no timing comparison, provider-side model identity, IDE qualification, or recovery evidence. Jev provider calls are zero; its usefulness remains unmeasured. Actual billing and subscription usage remain null; the approved API ceiling remains $1.

The installed plugin hashes still match the October 18/PR20 installation. PR21's click eligibility fix and this upstream guard have not been installed. No shared runtime, global configuration, or TCC permission was changed; VM work remains paused.

The next narrow action is upstream review of the guard and a supported, empirically verified route for the requested document's background Format/Save commands. A foreground or installation route requires separate authorization. Once that prerequisite works, rerun the [same supervised recipe](../docs/textedit-qualification.md) with two fresh repetitions per configured CLI identity, alternating order, real save cancellation/reopen receipts, exact output bytes and unchanged seeds. Do not interpret these driver probes as those four trials.
