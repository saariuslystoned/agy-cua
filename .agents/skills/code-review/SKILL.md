---
name: code-review
description: Run and verify pull request code reviews in saariuslystoned/agy-cua using GitHub Copilot Code Review exclusively. Use whenever asked to trigger, check, repair, or merge a code review on a PR in the agy-cua repository.
---

# `agy-cua` Repository Code Review Skill (GitHub Copilot Only)

This skill is scoped strictly to the `saariuslystoned/agy-cua` workspace (`.agents/skills/code-review/SKILL.md` is not packaged by `agy plugin install` and does not affect any other repository).

## Review Policy for `saariuslystoned/agy-cua`

- **Exclusive review mechanism:** Pull requests in `saariuslystoned/agy-cua` use **GitHub Copilot Code Review** (`copilot-pull-request-reviewer`, GraphQL bot node ID `BOT_kgDOCnlnWA`), guided by [`.github/skills/code-review/SKILL.md`](file:///Users/bobbybones/Developer/repos/saariuslystoned/agy-cua/.github/skills/code-review/SKILL.md).
- **Do not use external swarm reviewers here:** Do **not** invoke `smoky-openclaw-autoreview`, Spark-2, OpenClaw, ClawSweeper, or EmDashBot on `saariuslystoned/agy-cua`.

## Workflow

### 1. Local Verification Before Requesting Review
Run the local verification suite before pushing or requesting a review:
```bash
python3 -m unittest discover -v tests
shellcheck bin/agy-cua scripts/*.sh
```

### 2. Trigger GitHub Copilot Code Review on a PR
 Note that `gh pr edit --add-reviewer` does not resolve the `@copilot` bot login directly; always use the GitHub GraphQL `requestReviews` mutation with `botIds: ["BOT_kgDOCnlnWA"]`:
```bash
PR_NUMBER=4 # replace with target PR number
PR_ID="$(gh pr view "$PR_NUMBER" --repo saariuslystoned/agy-cua --json id -q .id)"
gh api graphql \
  -f query='mutation($prId: ID!, $botIds: [ID!]!) { requestReviews(input: {pullRequestId: $prId, botIds: $botIds, union: true}) { pullRequest { id number } } }' \
  -f prId="$PR_ID" \
  -f 'botIds[]=BOT_kgDOCnlnWA'
```

### 3. Inspect the Latest Copilot Review & Unresolved Threads
Poll until a review newer than your request timestamp appears:
```bash
gh api graphql -f query='
query($prNumber: Int!) {
  repository(owner: "saariuslystoned", name: "agy-cua") {
    pullRequest(number: $prNumber) {
      reviewRequests(first: 10) {
        nodes { requestedReviewer { __typename ... on Bot { login id } } }
      }
      reviews(last: 5) {
        nodes { databaseId author { login } state submittedAt body }
      }
      reviewThreads(first: 50) {
        nodes {
          id
          isResolved
          path
          line
          comments(first: 10) {
            nodes { author { login } body createdAt }
          }
        }
      }
    }
  }
}' -F prNumber="$PR_NUMBER"
```

### 4. Repair, Resolve Threads, Re-Review, and Merge
1. If any review thread is unresolved (`isResolved: false`) or the latest Copilot review body flags a concrete defect:
   - Fix the defect and add/update unit tests in [`tests/test_agy_cua_jev_mcp.py`](file:///Users/bobbybones/Developer/repos/saariuslystoned/agy-cua/tests/test_agy_cua_jev_mcp.py).
   - Commit and push to the PR branch.
   - Resolve addressed review threads via GraphQL:
     ```bash
     gh api graphql \
       -f query='mutation($threadId: ID!) { resolveReviewThread(input: {threadId: $threadId}) { thread { id isResolved } } }' \
       -f threadId="$THREAD_ID"
     ```
   - Re-trigger GitHub Copilot review (Step 2) so Copilot verifies the new HEAD commit.
2. Once the latest Copilot review on the current HEAD commit reports `0` open findings and all review threads are resolved, merge the PR:
   ```bash
   gh pr merge "$PR_NUMBER" --repo saariuslystoned/agy-cua --merge --delete-branch
   ```
