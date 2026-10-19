# Import verification

2026-10-19

NGCG inherits the improved CCG runtime, with bounded recovery, lossless source
paging, opaque task IDs, UNKNOWN handling and append-only verification history.
Repository branding and portable documentation do not change the existing CLI,
state namespace or charter approval rules.

| Check | Result |
|---|---|
| `python -B -m unittest discover -s tests -v` | 56 tests passed on Windows / Python 3.10 |
| `node --check hub_ui/app.js` | Passed |
| Skill frontmatter validation | Passed |
| Runtime, UI and tests compared with the source installation | 23 files match byte for byte; `atomic_io.py` only removes trailing whitespace, with identical Python AST |
| Import review for personal paths, local state and credential patterns | No matching personal paths or credentials in published files |

Existing tests cover bounded brief and JSON output, exact task selection,
UNKNOWN and malformed evidence, repeated attempt history, original text paging,
source-change detection, snapshot consistency, code fences, approval separation,
CLI behavior, concurrent state writes and the optional local Hub.

No live project records, SQLite databases, external approval records, cached
bytecode or private execution logs are included. Test-only Hub tokens are fixed
fixture values, not live credentials. The repository does not configure CI or
claim validation on other operating systems. Character budgets are not exact
model token counts, and these tests do not prove unchanged quality for every task.
