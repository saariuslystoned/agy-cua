# Linux doctor diagnostic proof

Base: `46a1563f8cb0e99eb7e9c94f50a9deca1ee4f7ed`. Candidate: the commit containing this file.

The unchanged real `bin/agy-cua doctor` CLI was run against fictional driver, Antigravity CLI, and Jev-check responses in an empty isolated HOME with network and host desktop access absent. The driver and Jev implementation were never invoked. These are CLI diagnostic results, not native Linux or macOS qualification.

| Case | Base exit | Candidate exit | Base Mac grant guidance | Candidate Mac grant guidance |
|---|---:|---:|---|---|
| linux_ready | 1 | 1 | yes | no |
| linux_missing | 1 | 1 | yes | no |
| linux_unknown | 1 | 1 | yes | no |
| linux_malformed | 1 | 1 | yes | no |
| mac_ready | 0 | 0 | no | no |
| mac_denied | 1 | 1 | yes | yes |

All four Linux cases retain the unqualified warning and exit 1. The candidate treats failed status commands and explicit unknown/error responses as unavailable or invalid, including responses that also carry healthy-looking fields. It reports only boolean backend fields or unknown values from successful status responses; it does not print the driver's session-bus address or claim background automation readiness. The Mac case outputs and all external command sequences match the base exactly.

## Captured Linux available-response output

Base:

```text
agy-cua doctor
  warn  only macOS is qualified so far; Cua Driver also supports Windows and Linux
  ok    Antigravity CLI agy fictional
  ok    cua-driver fictional at /work/bin/driver
  FAIL  permissions not confirmed; start the daemon and grant: open -n -g -a CuaDriver --args serve && cua-driver permissions grant
  ok    Jev route ready (fictional, model fictional, key_source=fixture)
  warn  agy-cua plugin not installed (run: agy-cua install)
```

Candidate:

```text
agy-cua doctor
  warn  only macOS is qualified so far; Cua Driver also supports Windows and Linux
  ok    Antigravity CLI agy fictional
  ok    cua-driver fictional at /work/bin/driver
  warn  Linux driver reports: X11=true, Wayland=false, Wayland enabled=false, AT-SPI=true
  FAIL  Linux remains unqualified; reported driver status does not establish background automation readiness
  ok    Jev route ready (fictional, model fictional, key_source=fixture)
  warn  agy-cua plugin not installed (run: agy-cua install)
```

## Validation

- All 125 unit/stdio/CLI tests passed in the isolated fixture.
- ShellCheck on `bin/agy-cua` and `scripts/*.sh` passed; `git diff --check` passed.
- The same six-case replay and the maintained CLI tests exercise the production script, with external responses substituted at executable boundaries.
- All six baseline/candidate stderr streams are empty. No unexpected command was requested by the fixture.

The local coordinator retains the complete immutable archive with the unchanged replay fixture, both full result sets, source identity, and hashes. No live desktop, provider, installation, credentials, permission grants, or deployment was tested.

The doctor fixture resolves jq from the caller and exposes only that executable beside its stubs. A non-system-location control verifies this path, without a native Mac run.
