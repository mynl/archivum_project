# Human Hints

Running notes from live Archivum use. Keep these practical and task-oriented.

## Common Tasks

### Replace Older Versions Of A Paper With A Newly Published PDF

Example blocked import:

```text
Import blocked: Merge/Warn; tag Guo2026; title {Dynamic capital allocation in general insurance}
```

What this means:

- The new PDF hash is not already in the library.
- The imported metadata has an exact normalized-title match against an existing reference.
- At least one matched existing reference already has a linked document.
- `Merge/Warn` is expected behavior; it prevents silently attaching a new physical PDF to an existing bibliographic record.

Example case:

- New file: `C:/Users/steve/Downloads/J of Risk   Insurance - 2026 - Guo - Dynamic capital allocation in general insurance.pdf`
- Existing title matches: `AnonReview2023` and `Guo2023`
- Desired outcome: remove the older review/preprint records, then import the 2026 published version as `Guo2026`.

Recommended workflow:

1. Do not bypass the web ingest block while the duplicate-title rows remain.
2. Open the Uber shell:

   ```powershell
   uv run archivum uber -l uber-library
   ```

3. Inspect the old records:

   ```text
   tag AnonReview2023 -v
   tag Guo2023 -v
   ```

4. Delete the old reference records only after confirming they are no longer wanted:

   ```text
   delete-tag AnonReview2023 -x
   delete-tag Guo2023 -x
   ```

   `delete-tag` prompts before executing. It removes the reference row and its `ref-doc` links, then saves. It does not delete the physical PDFs from the sharded document store.

5. Return to web Ingest and import the new PDF normally. The preview should change from `Merge/Warn` to `Import`, with final tag likely `Guo2026`.
6. Verify:

   ```text
   tag Guo2026 -v
   library-audit -v
   ```

7. Orphan document warnings for the old PDFs may remain. Treat orphan cleanup as a separate task after the new published version is safely imported and verified.

