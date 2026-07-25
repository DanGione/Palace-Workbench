"""Embed generated simulation artifacts inside the .FCStd file itself.

Wraps FreeCAD's native ``App::PropertyFileIncluded`` property type, which packs
an arbitrary file into the document's zip archive on save and transparently
extracts it back into the document's transient directory on restore. Every
read/write in this module deals in real filesystem paths -- xarray/netCDF4,
gmsh, and Part.export() all require real files, never in-memory buffers, so
callers keep using their existing path-based APIs unchanged; only how a path
is obtained changes.

Rules enforced by FreeCAD itself (see PropertyFileIncluded C++ docs):
  - Never write directly into the path returned by ``resolve()`` -- only
    ``embed()`` (a property assignment) registers new content.
  - Assigning from a path outside the document's own transient directory
    copies the source (it is preserved).
  - Assigning from a path obtained via ``new_scratch_file`` on the *same*
    document (a flat file directly at the transient directory's root) is
    consumed/removed as part of the assignment.
  - CAVEAT, confirmed empirically (not documented by FreeCAD): assigning from
    a path INSIDE a subdirectory of the transient directory -- i.e. anything
    under a ``new_scratch_dir()`` result, such as generate_mesh()'s mesh.msh/
    geometry.step -- is always COPIED, never consumed, regardless of which
    document it belongs to. The source subdirectory and its contents are left
    behind and must be removed explicitly (``shutil.rmtree``) after a
    successful ``embed()`` call, or every mesh/geometry generation leaks a
    scratch directory for the lifetime of the document. See
    commands/cmd_run.py's ``CmdRun.Activated`` and commands/cmd_mesh.py's
    ``CmdMesh.Activated`` for the required cleanup pattern.
"""

import os
import shutil
import stat


def new_scratch_file(doc, prefix):
    """Return a fresh, unique, not-yet-existing path in *doc*'s transient dir.

    Safe to call on a document that has never been saved -- FreeCAD creates a
    document's transient directory when the Document object is constructed,
    not when it's first saved to disk.
    """
    return doc.getTempFileName(prefix)


def new_scratch_dir(doc, prefix):
    """Return a fresh, empty, already-created directory in *doc*'s transient dir.

    Use for writers that produce more than one file per artifact (e.g.
    generate_mesh(), which writes both geometry.step and mesh.msh).

    IMPORTANT: unlike new_scratch_file(), embed()-ing a file that lives inside
    this directory only ever COPIES it -- the directory and its contents are
    never consumed/removed as a side effect. Callers MUST explicitly
    shutil.rmtree() this directory once they're done embedding everything
    they need out of it, or it leaks for the lifetime of the document.
    """
    path = doc.getTempFileName(prefix)
    os.makedirs(path, exist_ok=True)
    return path


def copy_for_rewrite(source_path, dest_path):
    """Copy *source_path* to *dest_path*, restoring owner write permission.

    A path resolved via ``resolve()`` is stored read-only (FreeCAD marks
    embedded/transient content 0400 to keep it from being edited in place).
    A plain shutil.copy2() preserves that read-only bit onto the copy, which
    then fails the moment a caller tries to write into it (e.g. xarray's
    to_netcdf()). Use this instead of shutil.copy2() whenever seeding a new
    scratch file from a previously resolved/embedded path that the caller
    intends to modify.
    """
    shutil.copy2(source_path, dest_path)
    os.chmod(dest_path, stat.S_IRUSR | stat.S_IWUSR)


def embed(obj, prop_name, source_path, archive_name):
    """Register *source_path* as the current content of *obj.prop_name*.

    Packed into the .FCStd zip under *archive_name* on the next document save
    (auto or manual). Re-embedding under the same archive_name reuses the same
    resolved path, so repeated calls (e.g. once per sweep iteration) don't
    accumulate distinct on-disk copies.
    """
    setattr(obj, prop_name, (str(source_path), archive_name))


def resolve(obj, prop_name):
    """Return the current real filesystem path for *obj.prop_name*, or "" if unset."""
    return getattr(obj, prop_name, "") or ""


def clear(obj, prop_name):
    """Reset *obj.prop_name* to empty, discarding any currently embedded content.

    Assigning an empty string is a no-op against App::PropertyFileIncluded (it
    does not clear a previously-resolved path) -- the property must be removed
    and re-added to genuinely return to an empty state. Use this before
    starting a fresh accumulation (e.g. a new sweep run) so a failure on the
    first point can't leave stale content from an unrelated prior run for a
    later point to silently build on.
    """
    if not hasattr(obj, prop_name):
        return
    group = obj.getGroupOfProperty(prop_name)
    tip = obj.getDocumentationOfProperty(prop_name)
    obj.removeProperty(prop_name)
    obj.addProperty("App::PropertyFileIncluded", prop_name, group, tip)


def ensure_property(obj, name, group, tip):
    """Idempotently add an App::PropertyFileIncluded property to *obj*."""
    if not hasattr(obj, name):
        obj.addProperty("App::PropertyFileIncluded", name, group, tip)


def legacy_sibling_path(doc, filename):
    """Pre-refactor convention: <dirname(doc.FileName)>/<stem>/<filename>.

    Returns None for a document that has never been saved (no sibling-folder
    convention could exist for it).
    """
    if not doc.FileName:
        return None
    stem = os.path.splitext(os.path.basename(doc.FileName))[0]
    return os.path.join(os.path.dirname(doc.FileName), stem, filename)


def migrate_legacy_string_property(obj, name, group, tip, legacy_hint=None):
    """One-time, idempotent upgrade of a legacy PropertyString path field.

    If *name* is still a plain App::PropertyString pointing at a file that
    exists on disk, convert the property to App::PropertyFileIncluded and
    embed that file. Falls back to *legacy_hint* (a sibling-folder convention
    path) when the stored value is empty or stale. Never deletes the original
    on-disk file.

    Safe to call every time a document is restored: callers typically run
    this right after a property-declaring _init_properties() pass, which
    means *name* may already exist as an (empty) App::PropertyFileIncluded on
    a document that never had the legacy field at all -- that is NOT treated
    as "already migrated". Only a property that already resolves to real
    embedded content is left untouched, so this never clobbers data embedded
    in a prior session.
    """
    prop_type = obj.getTypeIdOfProperty(name) if hasattr(obj, name) else None

    legacy_path = None
    if prop_type == "App::PropertyFileIncluded":
        if getattr(obj, name, ""):
            return  # already has real embedded content -- nothing to migrate
    elif prop_type == "App::PropertyString":
        candidate = getattr(obj, name, "")
        if candidate and os.path.isfile(candidate):
            legacy_path = candidate
        obj.removeProperty(name)

    if legacy_path is None and legacy_hint and os.path.isfile(legacy_hint):
        legacy_path = legacy_hint

    ensure_property(obj, name, group, tip)
    if legacy_path is not None:
        embed(obj, name, legacy_path, os.path.basename(legacy_path))
        import FreeCAD
        FreeCAD.Console.PrintMessage(
            f"Palace: migrated legacy '{legacy_path}' into embedded "
            f"{obj.Name}.{name}\n"
        )
