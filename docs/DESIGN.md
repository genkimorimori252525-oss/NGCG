# Continuity Guard shared runtime

CCG does not define a default department count or team structure. Team Chat and
Hooks remain OFF unless explicitly enabled for the project.

The optional Hub starts with `general` (全体連絡) and an aggregate message view.
Department/channel IDs are explicitly supplied when posting or observing, and
the UI lists only IDs already used in that project's stored messages. IDs may
contain 1–64 ASCII letters, digits, `_` and `-`, starting with a letter or digit;
`all` is reserved for the aggregate view.

The Osaka AI project's five-department operating rules belong to that project's
instructions, not CCG defaults. Existing `dept-1`–`dept-5` messages remain readable;
CCG neither creates five channels in other projects nor starts agents from this
structure. This change does not migrate or delete existing SQLite data.

## Recovery: quality with less context

**品質を落とさない程度に軽くする。** Keep original records, load only what is needed.

- `restore --brief` defaults to a bounded 2500-character index and verbatim source
  excerpts. `--max-chars 1000..20000` adjusts the total visible-text budget.
  Characters and UTF-8 bytes are measured; these are not exact model token counts.
- The index exposes source SHA-256, line references, all evidence-state counts,
  malformed-row diagnostics and a deferred count. Unread material never implies
  resolution, approval or permission. Review applicable goals, constraints,
  amendments and unresolved records before acting. Unknown scope remains HOLD.
- `recall CURRENT.md --section "Next steps"` reads an exact unique heading.
  `recall EVIDENCE_INDEX.md --task "Case-sensitive-ID"` returns all exact ID matches.
  `recall AMENDMENTS` lists original amendment files with hashes; their bodies are
  never silently promoted to approved instructions.
- Recall is paginated even for a giant single line. Continue with the returned
  `--offset` and `--sha256`; changed sources fail rather than mix versions.
  `--json` exposes content once with cursor metadata; its serialized output,
  including escaping and metadata, also fits the budget. Paths are confined to the project's
  continuity documents. Selection does not prove that other sections are irrelevant.
- `restore --full` remains an explicit expansion. Startup, resume and compact hooks
  all use brief; hooks remain disabled by default. Brief defers Git/Beads inspection:
  inspect current Git state before editing; it does not invent a clean worktree.
- Evidence IDs are opaque and case-sensitive, independent of prefixes. UNKNOWN,
  missing and unrecognised states remain visible; unrecognised states are counted
  as UNKNOWN with a diagnostic. Repeated attempts append to the index and retain
  their original receipts/logs. A later PASS does not erase historical failures.
- Run only relevant checks, batch them, and avoid redundant restore, audit or AI
  summaries. Local indexing, hashing and paging do not call a model.

Design references: [claude-mem progressive disclosure](https://github.com/thedotmack/claude-mem/blob/main/docs/public/usage/search-tools.mdx)
and [OpenCode compaction](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/compaction.ts).
These informed the design; their services, dependencies and source code were not
imported. No new package, model or cloud dependency is required.

## Optional parent view for two Git worktree variants

The main Git worktree is the **parent/coordinator**. For an experiment that truly
benefits from comparing two independent implementations, an agent may choose two
*linked* worktrees. Ordinary single-folder work remains the default; this
feature never creates worktrees, launches agents, or automatically merges.

- `continuity.py worktrees [--json]` runs `git worktree list --porcelain -z`
  to identify the main worktree and all linked worktrees, and reports the two
  intended variants (any extra linked worktrees are visible, never hidden).
- Each worktree continues to use its existing path-keyed SQLite state and its
  own checkout of `docs/ai-continuity/`. No DB schema changes or state
  migration are necessary. The overview opens existing SQLite files read-only;
  missing state is not silently initialized.
- The parent's own Charter/decisions provide coordination. Branch-specific
  notes, evidence, attempts, failures and checkpoints remain separate until an
  explicit human-reviewed synthesis. An approved child Charter does not
  authorize parent-wide changes. Passing evidence from a variant is not proof
  that the entire project is complete.
- Files committed before `git worktree add` form the initial shared baseline.
  Branch edits and uncommitted files are not synchronized between variants.
  Git history and refs are shared, working files and `HEAD` are distinct.
- Worktree metadata derives from Git rather than names invented by the model.
  AI-agent names identify checkpoint authors, **not** project ownership.
- If a branch is merged, keep its source evidence and attribution; do not
  auto-delete an experiment's failed attempts or private local state.
