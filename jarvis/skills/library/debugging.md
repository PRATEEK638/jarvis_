---
name: debugging
description: Diagnose why a program, command or script is failing
triggers: [error, exception, traceback, failing, fails, broken, crash, bug, stack trace, not working, debug]
---
Work from evidence, never from a guess about what is probably wrong.

1. **Read the actual error first.** The last line names the exception; the
   frames above it name the file and line. Retrieve those before theorising.
2. **Reproduce it.** If you cannot make it fail on demand, you cannot know you
   fixed it. Run the failing command and capture the real output.
3. **Read the code at the named line** — not the code you assume is there.
4. **Form one hypothesis and test it cheaply.** Print the suspect value, or run
   the smallest fragment that would confirm it. Do not change several things at
   once; you will not know which one mattered.
5. **Fix the cause, not the symptom.** Wrapping a crash in try/except when the
   real problem is a None that should never have been None hides the defect and
   makes the next failure harder to find.
6. **Verify by re-running the original failing case**, then check you have not
   broken the cases that previously worked.

Common causes worth checking early, in rough order of likelihood: wrong path or
working directory; a stale cached/compiled artefact; an environment difference
(wrong interpreter, missing env var, different PATH); an off-by-one or empty
collection; an encoding mismatch; a permission problem.

State honestly if you could not reproduce it. "I could not reproduce this" is a
useful finding; a speculative fix presented as a solution is not.
