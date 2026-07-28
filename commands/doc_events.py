"""Shared Qt signal for FreeCAD active-document changes.

Palace result-viewer panels (SParamPanel, SweepResultsPanel) connect to
`doc_emitter.active_document_changed` to refresh their plotted data whenever
the user switches the active FreeCAD document (or closes the last one).
Mirrors the `_iter_emitter` pattern in cmd_sweep.py.
"""

try:
    from PySide2.QtCore import QObject, Signal
except ImportError:
    from PySide6.QtCore import QObject, Signal


class _DocumentEventEmitter(QObject):
    active_document_changed = Signal()


doc_emitter = _DocumentEventEmitter()


class _PalaceDocumentObserver:
    """FreeCAD document-observer protocol is duck-typed; only slotX methods
    that are defined here get invoked."""

    def slotActivateDocument(self, doc):
        doc_emitter.active_document_changed.emit()

    def slotDeletedDocument(self, doc):
        doc_emitter.active_document_changed.emit()


_observer_instance = None


def register_once():
    """Register the document observer exactly once per FreeCAD session."""
    global _observer_instance
    if _observer_instance is not None:
        return
    import FreeCADGui
    _observer_instance = _PalaceDocumentObserver()
    FreeCADGui.addDocumentObserver(_observer_instance)
