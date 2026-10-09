## What this changes

<!-- And why. The commit history is used as documentation here, so the reasoning matters more
     than the summary. -->

## How you verified it

<!-- The commands you actually ran, with their result. "Tests pass" is less useful than
     "ctest --preset debug: 164/164; pytest: 538 passed". If you could not verify something
     (no physical CAN adapter, for instance), say so. -->

## Checklist

- [ ] `cd boat-platform && ctest --preset debug` passes
- [ ] `pytest boat-platform/sdk/python/tests boat-platform/cli/tests ui/tests tools/tests` passes (from the repo root)
- [ ] New source files carry the two-line SPDX header
- [ ] If I edited a `.proto`, I ran `boat-platform/sdk/python/boat/stubs/generate_stubs.sh` and committed the result
- [ ] If I added a dependency, I added it to `THIRD_PARTY_NOTICES.md`
- [ ] **If I changed a fact stated in `CLAUDE.md` or `AGENTS.md`, I changed it in BOTH.** They
      describe the same repository for different audiences and must never disagree — nothing
      fails when they do, which is why this is on the list.
- [ ] I did not introduce a wall clock, unseeded randomness, or nondeterministic ordering into
      core, scheduling or replay code
- [ ] I did not edit verdicts in `test/*.md` (the hand-verified sign-off record)

## Anything left undone

<!-- Deliberate omissions, follow-ups, or things you think are wrong but out of scope. -->
