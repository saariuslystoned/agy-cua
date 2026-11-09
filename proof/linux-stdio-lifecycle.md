# Stdio launcher lifecycle proof

Base: `da3260e2e0f4ebd9e440f6f1aeb90c0e8de56f96` (fresh public main).
Code and regression: `6fdb77d0c50603f7836e7d0be115fbb69050320d`.
This branch is based directly on main; the separate unmerged directory-selection change is not included. Subsequent proof-only commits retain the tested launcher bytes.

## Trigger and change

With client stdin still open, a cooperative driver can exit while the launcher's daemon input thread holds Python's buffered-stdin lock. The launcher then aborts during interpreter finalization. Read raw stdin bytes with `os.read`, retaining newline-delimited messages and the final partial line at EOF. The existing signal forwarding, child wait, and broken-pipe behavior are unchanged.

The [MCP lifecycle contract](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle#shutdown) permits server-initiated shutdown by output closure and exit. Client-initiated shutdown starts with stdin closure, then allows signal escalation. It specifies no numerical grace period. [Cua Driver direct-mode source](https://github.com/trycua/cua/blob/82752858f6256eaf4e8eccf9033cf27ad3163cea/libs/cua-driver/rust/crates/cua-driver/src/proxy.rs) likewise ends on stdin EOF and invokes SDK shutdown. No real driver was executed by this proof.

## Captured behavior

Fresh actual-candidate replay: Linux aarch64 / CPython3.14.8, 21/21 expected observations passed. A separate hosted public-source replay on Linux x86_64 / CPython3.11.2 reproduced the same defect and candidate behavior. The local observer uses the architecture's actual `read` syscall number (x86_64:0, aarch64:63); launcher bytes and expected outcomes are identical.

| Case, while client stdin stays open unless noted | Base launcher | Candidate launcher |
| --- | --- | --- |
| Cooperative child terminates via SIGTERM | SIGABRT (-6), buffered-lock fatal error | 0 |
| SIGINT to launcher, forwarded as existing SIGTERM | SIGABRT (-6) | 0 |
| SIGTERM to launcher | SIGABRT (-6) | 0 |
| Child exits23 | SIGABRT (-6) | 23 |
| Normal request/reply then stdinEOF | 0 | 0 |
| Large/split/coalesced UTF-8 lines and snapshot annotation, then EOF | Preserved, 0 | Preserved, 0 |
| Final request without newline, then EOF | Preserved, 0 | Preserved, 0 |
| Child closes its input before another request | Existing BrokenPipeError warning, then0 | Same warning, then0 |
| Deliberately ignores EOF and TERM | Fixture watchdog cleanup | Fixture watchdog cleanup |

A deliberately wrong candidate that omits child-stdin closure fails normal EOF completion; the replay detects it and cleans up its registered group. These fixture watchdog/observation intervals are not new product timeout or kill policies. All registered PIDs are identified by PID/starttime/group and reaped; none remain after the replay.

Decisive captured summary:

```json
"case_runs": 21,
"passed": 21,
"failed": [],
"cleanup_verified": true,
"proof_pass": true
```

The focused subprocess regression performs an initialize exchange, observes the real launcher thread blocked in `read(0)`, then exercises four exit/signal cases without closing client stdin. All four fail on the base with `_enter_buffered_busy`; all pass on the candidate. The exit sentinel is published using a sibling write followed by atomic rename. A second subprocess test checks exact byte lengths/hashes for a roughly4MB UTF-8 request followed by complete and final-partial lines. Linux x86_64/aarch64 only: other platforms skip this `/proc` observer test rather than claiming equivalent platform proof.

Complete repository command `python3 -m unittest discover -v tests`:

```text
Ran 127 tests in 18.253s

OK
```

`ShellCheck 0.11.0`: `shellcheck bin/agy-cua scripts/*.sh` passed. `git diff --check` passed. Tests ran with private PID/network/home namespaces, without host display/session sockets. Existing pinned Node/lsof supplied unrelated suite prerequisites; no tools were installed.

## Linear framing work

The reader stores partial-line fragments, splits only newly read chunks, and joins once per completed line. Instrumented `bytes` at the `os.read` boundary count actual split operands in the before/after production functions:

| Input line bytes | Previous split operand bytes | Current split operand bytes |
| ---: | ---: | ---: |
| 1,048,577 | 9,961,473 | 1,048,577 |
| 4,194,305 | 140,509,185 | 4,194,305 |

Outputs matched input exactly. These are deterministic operation counts, not wall-clock or native throughput claims. The instrumented split/`+` counters exclude built-in joins and allocator overhead; current code still joins completed lines. The public `work_counts.py` reproduces this measurement against the previous and current source copies. The previous immutable archive remains retained history and is not current-head clearance.

## Replay archive

[Full immutable source, replay, captured results and regression proof](https://github.com/saari-co/public-oss-proof-assets/releases/download/agy-stdio-lifecycle-6fdb77d-20261108/agy-stdio-lifecycle-6fdb77d-public.zip)

- 39,382 bytes; SHA256 `3f37ad303a58d378d007a37d6a592e8135385d48f87183c05d6b7b02a592dbe8`.
- Manifest SHA256 `196dd2a3929c7ec2686d019d1737ad60766891d00c7803c3d339116f69c1cf66`.
- Actual candidate launcher SHA256 `0e0d24e1a5e5edefbf982112812e682ff3945a12fbb86ae2b1a4d83bec96325c`; the replay asserts supplied product bytes match the scoped change before executing them.
- Seventeen payload files plus manifest/checksum list. `observed/results.json` contains all case receipts; `repository-tests.txt` and `regression-red.txt` retain captured test output with temporary paths/runtime addresses redacted.

Download into a new directory:

```sh
curl -fL 'https://github.com/saari-co/public-oss-proof-assets/releases/download/agy-stdio-lifecycle-6fdb77d-20261108/agy-stdio-lifecycle-6fdb77d-public.zip' -o proof.zip
printf '%s  %s\n' '3f37ad303a58d378d007a37d6a592e8135385d48f87183c05d6b7b02a592dbe8' 'proof.zip' | sha256sum -c -
unzip proof.zip -d replay-proof
cd replay-proof
python3 verify.py
```

Then use the exact private-namespace replay command in the archive README. It requires Linux x86_64/aarch64, Python3 and bubblewrap, with no network after download, no actual driver, and a new output directory. It disables core dumps and cleans up only registered fixture-owned process groups.

Provenance: original hosted archive108,949 bytes / SHA256 `3be3a083168087c66e492b9d0a4a5182da6b15dc01a3bf4bede2d2f5cce8a098`, 47 payload hashes verified before inspection and replay. This smaller public archive contains the actual product candidate and independently captured results; it excludes chat, goals, host identities, screenshots and private paths. Secret and private-term scans passed before publication.

## Limits and gates

This proves the fictional stdio process boundary, not real Cua Driver/native desktop/provider behavior or an installed plugin. The existing broken-pipe warning is not repaired here. No service/config/account change or new shutdown policy is introduced. PR publication and final-head Copilot review remain with the coordinator; merge is human-gated.
