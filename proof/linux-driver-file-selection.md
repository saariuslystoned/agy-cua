# Linux driver executable selection proof

Base: `da3260e2e0f4ebd9e440f6f1aeb90c0e8de56f96`.
Code and regression commit: `7d4ae8da51799796bf93fb6881509fc1b682eed2`.
Subsequent proof-only commits retain these executable bytes.

The actual `bin/agy-cua-mcp` launcher accepted a searchable directory as its explicit driver candidate because `os.access(path, os.X_OK)` also succeeds for directories. This raised `PermissionError` before a valid user-local fallback could start. The fix additionally requires a regular file, preserving executable symlinks and candidate order.

## Observed red/green and opposite controls

The replay invokes the actual Python `__main__` launcher with fictional executable files and a clean per-case environment. It checks selected driver identity, stdin/stdout bytes, forwarded arguments, direct-mode arguments, exit codes and stderr. Captured `observed/summary.json` and `observed/results.json` in the archive below contain all cases.

| Executable variant | Contract passes | Exact expected failures observed |
| --- | ---: | --- |
| Unchanged base | 22/32 | 10 directory-selection cases |
| Candidate | 32/32 | None |
| Wrong control: omit execute-access check | 28/32 | 4 non-executable-file cases |
| Wrong control: reject symlinks | 28/32 | 4 valid executable-symlink cases |

The base `directory__fallback_file` case returned exit1 and `PermissionError: [Errno 13] Permission denied: '<CASE>/explicit-driver'` instead of launching the valid fallback. Candidate returned exit0 and the expected fictional fallback record. `<CASE>` is the runner's redaction of its temporary fixture directory.

Decisive captured fields from the independent replay:

```json
"case_count": 32,
"invocations": 128,
"fixed_native_candidate_absent": true,
"inherited_environment": false,
"proof_pass": true
```

Runtime: Linux, Python 3.14.8 standard library. Independent replay ran with a private PID/network namespace, private home/tmp, no inherited environment and no host display/session sockets. Candidate launcher SHA256 `e9d790e1422df10d59075a685a3b09ed6df5881e558984fcb9ae3cadc61ce218` matched the committed file. Base launcher SHA256 is `8726241b66e26a84792efe681186d0d6c27e67becc5dc80a8d71afe4531e9a16`.

The new repository subprocess test `test_directory_override_falls_back_to_executable_file` fails against the original launcher. With the candidate, the complete repository command `python3 -m unittest discover -v tests` produced:

```text
Ran 126 tests in 17.349s

OK
```

This suite ran once in isolation with the existing pinned Node/lsof prerequisites; proof-only edits did not rerun or change its result. The complete captured test output is `repository-tests.txt` in the archive.

## Immutable public replay

[Download full source/replay/results archive](https://github.com/saari-co/public-oss-proof-assets/releases/download/agy-cua-driver-selection-7d4ae8d-20261108/agy-cua-driver-selection-7d4ae8d-public.zip)

- Archive: 20,658 bytes.
- SHA256: `dc6de648865f4f9b67a55ee0c395f16049bdd09abb8e764a61b15870e0338f40`.
- Manifest SHA256: `effdb3f21554f4bc51cef979daf1d602534013d5bbff6f7b3126900a490600da`.
- 10 payload files plus manifest/checksum list; all hashes verified.
- Public release API digest and independently downloaded bytes matched the archive hash.

From a new directory on Linux:

```sh
curl -fL 'https://github.com/saari-co/public-oss-proof-assets/releases/download/agy-cua-driver-selection-7d4ae8d-20261108/agy-cua-driver-selection-7d4ae8d-public.zip' -o proof.zip
printf '%s  %s\n' 'dc6de648865f4f9b67a55ee0c395f16049bdd09abb8e764a61b15870e0338f40' 'proof.zip' | sha256sum -c -
unzip proof.zip -d replay-proof
cd replay-proof
python3 verify.py
python3 replay.py --output replay-local
```

`replay-local` must not exist. The runner refuses if the fixed macOS driver path exists, and requires a Python executable path without whitespace for the fictional shebang. No network is used after download; no package installation is required. It creates only task-local variants and temporary fictional drivers. The source bytes are pinned and checked before execution.

Provenance: the hosted public-source packet supplied the portable replay design (original archive: 33,726 bytes, SHA256 `f2781f98ebfc21363a68bd9e160a505648876da911cc8f033f595f513824b3cf`). Its 17 payload hashes were checked before inspection and independent local replay. The published archive includes only the minimal source, standalone scripts and independently captured local results, with private-term and secret scans passing; it excludes chat, objectives, screenshots and private paths.

## Scope

This proves launcher file selection, not native Linux automation readiness, permissions, capture/input, installed plugin behavior or provider integration. The doctor and guarded Jev server are unchanged. No real desktop operation, installation, service/configuration change, PR, merge or deployment was performed by this proof run. PR publication and exact-head Copilot review remain with the coordinator.
