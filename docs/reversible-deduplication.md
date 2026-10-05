# Reversible deduplication design

## Status and scope

This document specifies a future hard-link deduplication operation. It does not
authorize or implement filesystem mutation. The current scanner, index, and
reporting command remain read-only.

The first implementation should support regular files on one local macOS APFS
volume. Every path in an operation, its staging directory, and its recovery
snapshot must report the same `st_dev`. Cross-volume links, network filesystems,
cloud placeholders, symlinks, directories, device files, sockets, sparse-file
special handling, MacFUSE, and automatic background execution are out of scope.

Hard links make multiple paths share one inode. They therefore also share file
contents and inode metadata. The operation must reject a duplicate group when
its paths differ in mode, owner, group, flags, ACLs, extended attributes, or any
other metadata that would become shared. It must also reject protected or
immutable files and files whose link count cannot safely be increased.

## Reversibility boundary

Permanent reversibility requires retained original bytes. Although every member
of a verified duplicate group starts with identical bytes, any hard-linked path
can later change the shared inode. The operation must therefore create and
retain one independent recovery snapshot per content group before replacing any
path.

The operation is reversible while that snapshot and its journal remain intact.
Deleting them is a separate, explicit `finalize` action. After finalization, the
tool may be able to split current hard links into independent copies, but it
cannot promise restoration of the pre-operation bytes. Finalization must state
that distinction and require its own confirmation.

## Operation artifacts

Planning creates an immutable manifest with a canonical serialization and
SHA-256 plan digest. It records:

- operation ID, creation time, tool version, and plan digest;
- scan database identity and the indexed scan generation;
- canonical path and replacement paths for every group;
- SHA-256, size, device, inode, link count, nanosecond modification time, and
  nanosecond change time for every path;
- mode, owner, group, flags, ACL digest, and extended-attribute digest;
- recovery snapshot path and expected free-space requirement;
- estimated bytes saved after finalization; and
- state transitions and per-path results.

The SQLite operation journal is append-only at the event level. Its operation
state follows:

`planned -> prepared -> applying -> applied -> rolling_back -> rolled_back`

`applied -> finalized` is allowed only through the explicit finalization
command. A failure records `failed` plus the last durable path event, but does
not erase the prior state history.

## Preflight checks

Preflight is read-only and must complete immediately before confirmation:

1. Load one exact plan digest; never regenerate an approved plan implicitly.
2. Confirm all paths are absolute, within the approved scan root, distinct, and
   still regular files reached without following symlinks.
3. Confirm all paths and the staging directory are on the same supported local
   APFS device.
4. Re-read size, device, inode, link count, timestamps, permissions, flags,
   ACLs, and extended attributes and compare them with the plan.
5. Stream SHA-256 for every candidate again. Cached hashes are insufficient for
   a mutating operation.
6. Reject groups with differing bytes or inode-shared metadata.
7. Confirm write and rename permission on every parent directory and exclusive
   creation permission in staging and target directories.
8. Confirm no operation journal already owns a path and no unfinished operation
   requires recovery.
9. Confirm enough free space for one independent snapshot per group, temporary
   replacement links, the journal, and a safety margin.
10. Refuse execution if any path changes during preflight.

The MVP assumes a quiescent, non-adversarial source tree. Advisory locks cannot
prevent another process from replacing or editing a path, so the command must
warn the user to stop writers and must continue validating at each mutation
checkpoint.

## Explicit confirmation

Planning and applying are separate commands. `apply` accepts a saved manifest
and prints the operation ID, plan digest, path count, group count, byte total,
recovery location, and reversibility cost. It performs no mutation unless the
user enters an exact confirmation containing the plan digest. A `--yes` flag is
not part of the MVP.

Confirmation binds only the displayed plan. Any changed path, failed preflight,
or different digest invalidates approval and requires a new plan and new
confirmation.

## Prepare and apply protocol

Preparation is durable before the first replacement:

1. Create an operation-specific staging directory on the approved device with
   owner-only permissions.
2. Copy the canonical bytes for each group to a new, independent snapshot file;
   do not clone or hard-link it.
3. Verify each snapshot SHA-256 and size, write its metadata record, then `fsync`
   the file, manifest, journal, and staging directory.
4. Transition the journal to `prepared` only after every snapshot is durable.

For each replacement path, in deterministic order:

1. Re-open the canonical and target without following symlinks. Compare `fstat`
   results with the approved plan and stream both hashes again.
2. Create a hard link to the canonical inode at an exclusive temporary name in
   the target's parent directory.
3. Verify the temporary link's device and inode match the canonical and that the
   target has not changed since its checkpoint.
4. Atomically rename the temporary link over the target.
5. `fsync` the parent directory, verify the resulting path and hash, then append
   and durably sync the completed-path journal event.

The canonical path is never replaced. If a checkpoint fails, stop immediately,
retain all recovery artifacts, and direct the user to resume or roll back.
Temporary names include the operation ID and are never reused across operations.

## Race and interruption handling

No decision from the earlier indexing scan authorizes mutation. Apply uses fresh
file descriptors, metadata, and hashes. It validates before and after every
atomic replacement. A mismatch is a hard stop, not a warning or automatic plan
update.

On startup, the mutating command checks for unfinished journals. It must not
start a new overlapping operation. Recovery reads durable per-path events and
inspects actual path inodes, so these actions are idempotent:

- a target still on its original inode needs no rollback action;
- a target linked to the planned canonical is eligible for rollback;
- a target matching neither state is a conflict requiring manual review; and
- an operation may resume only after every completed and pending path matches a
  recognized state.

Signals and ordinary exceptions trigger an orderly stop after the current
atomic rename and journal sync. Power loss is handled by the same journal and
filesystem inspection on the next invocation.

## Rollback and recovery

Rollback requires the retained snapshot and journal. For each replaced path in
reverse order it:

1. verifies the snapshot and its recorded SHA-256;
2. creates a new independent temporary file in the target directory;
3. copies snapshot bytes, restores recorded metadata, and verifies the result;
4. `fsync`s the file, atomically renames it over the hard link, and `fsync`s the
   parent directory; and
5. durably records completion before continuing.

Rollback never overwrites an unrecognized path state. If a linked file changed
after apply, the snapshot still preserves the pre-operation bytes, but replacing
the changed content is consequential; recovery must stop and require a second
explicit confirmation that names the conflicting paths.

After all paths are independent and verified, the operation becomes
`rolled_back`. Recovery artifacts remain until an explicit cleanup command.

## Proposed implementation tests

Unit and integration coverage must include:

- rejection of symlinks, non-regular files, cross-device paths, unsupported
  filesystems, differing metadata, insufficient space, and changed hashes;
- confirmation bound to the exact plan digest, including cancellation and stale
  plan rejection;
- one snapshot per group that is neither a clone nor hard link and survives
  later mutation of a deduplicated path;
- successful replacement with expected inode equality and byte savings;
- source changes before temporary-link creation, before rename, and immediately
  after rename;
- injected failures at every durable journal boundary;
- termination and simulated power loss after each replacement count;
- idempotent resume and rollback from every recognized partial state;
- conflict handling when a path is externally replaced or modified;
- restoration of independent inodes, bytes, metadata, ACLs, flags, and extended
  attributes;
- finalization refusing unfinished operations and accurately warning that the
  historical-byte rollback guarantee will end; and
- invariant checks proving unrelated paths and filesystems are never modified.

Tests that exercise real hard links, APFS metadata, interruption, and recovery
must run on a disposable macOS volume. CI on Linux may test plan validation,
journal transitions, injected failures, and recovery state logic with a fake
filesystem boundary, but it is not evidence of APFS correctness.

## Implementation gate

Before mutation code is added, this design needs explicit acceptance and the
implementation should be split into reviewable stages: plan and preflight,
durable journal and snapshot preparation, apply, rollback and recovery, then
finalization. Hard-linking remains disabled until all stages and the macOS
integration suite are complete.
