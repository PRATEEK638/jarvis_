---
name: file-work
description: Organise, find, move, rename or clean up files and folders safely
triggers: [file, files, folder, directory, organise, organize, rename, move, copy, sort, cleanup, duplicate, backup]
---
File operations are easy to do and hard to undo. Bias every decision toward
reversibility.

1. **Look before you act.** List the directory and confirm what is actually
   there. The user's description of their own folder is frequently out of date.
2. **Confirm the target is unambiguous.** "the report" is not a path. If more
   than one file could match, ask which - do not pick the newest and hope.
3. **Never overwrite silently.** If the destination exists, stop and say so.
   The user did not mention that file, so they have not agreed to lose it.
4. **Prefer move over delete, and copy over move** when the intent is unclear.
   An archive folder is almost always the right answer to "get rid of these".
5. **Verify afterwards** by re-reading the destination, not by assuming the
   operation returned success.

For bulk operations: report exactly what you are about to touch and how many
items, then do it. A wrong bulk operation is the most expensive mistake
available in this domain.

Deleting the user's files is never permitted, under any phrasing.
