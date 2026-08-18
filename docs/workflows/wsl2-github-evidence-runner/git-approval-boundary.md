# Git and GitHub Approval Boundary

## Principle

Lifecycle authorization, execpolicy routing, and operating-system elevation are
separate. The fixed Evidence Runner grants no GitHub write, Git write, merge,
cleanup, or lifecycle permission.

## Boundary matrix

| Operation | Evidence Runner | Direct command policy | Reason |
| --- | --- | --- | --- |
| Task/PR/check/thread/Project read | Fixed internal query | Narrow direct `gh issue/pr/run/repo view` and `gh pr checks` prefixes are allowed; `gh api` remains prompt | Prevent arbitrary API and argument expansion. |
| Changed files/commits/diff digest | Fixed internal query | Direct `gh pr diff/view` remains prompt/unmatched | Keep one audited entry. |
| `git status`, `rev-parse`, `merge-base`, `ls-files` | Fixed internal argv | Narrow direct prefixes are allowed | `diff`, `log`, `show`, branch, remote, and worktree shapes remain fail-closed because prefix rules cannot prove their full argv safe. |
| `git ls-remote --heads origin ...` | Fixed internal argv | Direct command not broadly allow-listed | Current remote comparison without local ref mutation. |
| `git fetch` | Never executed | `prompt` | Refreshes local remote-tracking refs and remains a separate approval decision. |
| `git add`, `commit`, `switch`, `checkout` | Never executed | `prompt` | Repository writes or branch/worktree state changes. |
| `git push`, force push, remote branch deletion | Never executed | `prompt` or stricter | Remote mutation; force push remains forbidden by workflow policy. |
| `git branch -d/-D` | Never executed | `prompt` | Exact cleanup only after Closeout safety gates. |
| `git reset --hard`, `git clean` | Never executed | `prompt` in Rules, forbidden by workflow policy | Destructive commands are not authorized by this Task. |
| GitHub comment/review/label/Project/Issue write | Never executed | `prompt` or stricter | Active Skill and maintainer gates remain authoritative. |
| `gh auth token` | Never executed | `forbidden` | Prevent credential disclosure. |
| Merge | Never executed | Forbidden by Task workflow | Maintainer manual gate. |

A `prompt` rule does not authorize the operation. It only ensures that a later
Skill-authorized exact operation cannot silently inherit the runner's allow
boundary.

## Credential choice

The runner normally relies on the authenticated `gh` configuration available in
the WSL2 user's `HOME` or `GH_CONFIG_DIR`.

Minimum scopes depend on repository visibility and Project use:

- repository read permission for private repository metadata;
- organization read permission when organization metadata is required;
- Project read permission for ProjectV2 fields.

A scoped `GH_TOKEN`/`GITHUB_TOKEN` may be inherited for non-interactive use, but
the runner never prints, hashes into public material, or stores the token. A
missing scope must become partial/unknown evidence rather than a false pass.

## Prefix Rules boundary

The Rules language matches command prefixes. The runner remains responsible for
complete argv validation. Tests therefore cover both layers:

```text
execpolicy fixed profile prefix -> allow
runner fixed complete argv       -> accepted
runner profile + arbitrary tail  -> rejected before evidence subprocess
```

No claim is made that the Rules language provides end-of-command matching.
