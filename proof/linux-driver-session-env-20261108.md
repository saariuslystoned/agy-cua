# Linux DriverClient session environment

Base: `419b41b5114ad0431df93667026d62467e621367`.

`DriverClient` now forwards nonempty `DISPLAY`, `WAYLAND_DISPLAY`,
`XDG_RUNTIME_DIR`, and `DBUS_SESSION_BUS_ADDRESS` from its supplied environment
when Python reports `sys.platform == "linux"`. The existing driver overrides
and command selection remain intact. macOS and unknown platforms retain their
existing child environment. Missing or empty session descriptors are omitted;
they are not filled from the host environment.

The dependency is present in public Cua Driver source
[`e46badaaf789fb529c82e946374d567362286034`](https://github.com/trycua/cua/tree/e46badaaf789fb529c82e946374d567362286034/libs/cua-driver):

- `rust/crates/cua-driver/src/main.rs`, `mcp_uses_direct_runtime_for`, selects
  the direct runtime by default on non-macOS when no explicit socket or
  history-preview exception applies.
- `rust/crates/platform-linux/src/wayland/mod.rs` reads the display and runtime
  descriptors for Linux session selection and connection setup.
- `rust/crates/platform-linux/src/session_bus.rs` honors an explicitly supplied
  D-Bus session address before attempting discovery.

This is source provenance, not a claim about the installed or released driver
version. The patch does not forward the entire environment, selector
credentials, `XAUTHORITY`, inherited Wayland file descriptors, or arbitrary
driver settings.

## Verification

The same seven tests in `DriverSessionEnvironmentTest` exercise the actual
`DriverClient` with a fictional stdio MCP child and capture the environment
passed to the real subprocess constructor. The child completes initialization
and repeated requests; no native driver, provider, or desktop is contacted.

| Source | Result |
| --- | --- |
| Unchanged base with the new tests | 4 passed, 3 failed because Linux descriptors were absent |
| Candidate with the same tests | 7 passed |
| `python3 -m unittest discover -v tests` | 116 passed |
| `shellcheck bin/agy-cua scripts/*.sh` | Passed with ShellCheck 0.11.0 |

Controls cover macOS and unknown-platform compatibility, missing and empty
descriptors, exclusion of unrelated values and credential sentinels, explicit
driver/direct-command precedence, process reuse, timeout reset, and restart.
The full suite ran with an isolated test home and no real desktop or selector
environment. An initial missing Node prerequisite and a subsequent missing
`lsof` prerequisite in an existing benchmark-cleanup fixture were resolved in
the test environment without changing the benchmark or its cleanup guard.

## Limits

These results establish subprocess transport and policy behavior. They do not
qualify native Linux background input, macOS regression behavior, focus or
cursor isolation, installed-plugin activation, or performance. Existing action
refusals and credential routes are unchanged. Native platform qualification
requires its own approved runtime and acceptance evidence.
