# Driver executable selection on Linux

Base: `da3260e2e0f4ebd9e440f6f1aeb90c0e8de56f96`.

The real `bin/agy-cua-mcp` launcher accepted a searchable directory as its explicit driver candidate because `os.access(path, os.X_OK)` also succeeds for directories. This raised `PermissionError` before a valid user-local fallback could start. Require a regular file as well as execute access. Candidate ordering and executable symlinks remain supported.

The new subprocess regression supplies a directory override and a fictional executable at the existing user-local fallback. It fails on the base launcher and passes with this change, verifies `mcp --direct` argument forwarding, and never launches a real desktop driver. The complete 126-test repository suite passed in an isolated Linux namespace with a private home, no host display or session bus, and no external network.

This is launcher file-selection proof. It does not qualify Linux desktop automation, permissions, capture, input, or any provider integration. No plugin installation, native desktop operation, service change, or deployment was performed.

An independent offline replay ran 128 subprocess invocations across 32 cases on each of four variants. The candidate passed 32/32; the base passed 22/32, failing the ten directory-selection cases. Negative controls that remove execute-access checking or reject symlinks each passed 28/32, failing their four discriminating cases. Valid candidate priority, fallback behavior, input/output forwarding, explicit arguments, and direct-mode argument handling were retained. The candidate launcher bytes matched the independently replayed proposed version.
