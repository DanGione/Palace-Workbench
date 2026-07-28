import FreeCADGui
import Part

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets

from panels.unit_widgets import (
    R_UNITS, L_UNITS, C_UNITS,
    read_si_field, rescale_on_unit_change, make_unit_row, set_unit_field,
)
from palace.smd_geometry import EIA_SIZE_TABLE, build_smd_component

_COMPONENT_TYPES = ["Resistor", "Capacitor", "Inductor"]

# (display text, raw ImpedanceBoundaryPosition value)
_POSITION_MODES = [
    ("Flipped (film near pads — lower L)", "Flipped"),
    ("Conventional (film near top — higher L)", "Conventional"),
    ("Middle (film centered — for capacitors)", "Middle"),
]

# (units, default value, default unit) per _COMPONENT_TYPES index
_TYPE_DEFAULTS = [
    (R_UNITS, 100.0, "Ω"),
    (C_UNITS, 10.0, "pF"),
    (L_UNITS, 10.0, "nH"),
]


class SMDComponentPanel:
    def __init__(self, doc, ghost_obj, existing=None):
        self.doc = doc
        self.ghost = ghost_obj
        self.existing = existing   # the SMDComponent being re-edited, or None if creating
        self.form = self._build()

        # Reuse FreeCAD's own Attachment editor (the same panel Part/PartDesign
        # primitives use) rather than a hand-rolled placement widget -- gives
        # full reference-based attachment (planes, faces, edges, FlatFace,
        # Concentric, etc.), not just a fixed position/axis/angle.
        # take_selection pulls in whatever the user pre-selected before
        # invoking the command, exactly like a native primitive's creation
        # dialog -- skipped when re-editing an existing component, since the
        # ghost was already pre-loaded with its current attachment state (see
        # for_existing()) and references are non-empty by that point anyway
        # (take_selection only ever fires when References is still empty).
        # create_transaction=False matches the rest of this codebase's panels,
        # which don't use undo transactions.
        #
        # Its widget (.form) is embedded directly into this panel's own layout
        # (see _build(), added last) rather than passed to Control.showDialog
        # as a second stacked panel -- this keeps this panel in sole control
        # of the accept/reject/closeDialog lifecycle, avoiding any ambiguity
        # about call order between two independently-managed task panels
        # (e.g. this panel deleting the ghost object before the attachment
        # panel's own accept() has a chance to run). accept()/reject() below
        # call its writeParameters()/cleanUp() directly instead of its own
        # accept()/reject(), since those also call Gui.Control.closeDialog()
        # and manage transactions this panel already handles itself.
        from AttachmentEditor.TaskAttachmentEditor import AttachmentEditorTaskPanel
        self.attachment_panel = AttachmentEditorTaskPanel(
            ghost_obj, take_selection=(existing is None), create_transaction=False,
        )
        self.form.layout().addRow(self.attachment_panel.form)

        # AttachmentEditorTaskPanel.attachmentOffsetChanged() re-parses the
        # offset spin boxes' raw displayed text on every valueChanged signal
        # (rather than using the value the signal already carries), which can
        # throw "ValueError: Unit mismatch" if the text is momentarily
        # unparseable as a length mid-edit (e.g. the field briefly empty
        # during a fast drag/retype). That's FreeCAD's own installed Part
        # module code, not something in this repo to patch directly, so wrap
        # just this one bound method on our instance instead: the signal
        # connection made inside AttachmentEditorTaskPanel.__init__ calls
        # `self.attachmentOffsetChanged(...)` via a fresh attribute lookup on
        # every emission (not a cached reference), so replacing the instance
        # attribute here is enough to intercept it -- same "ignore transient
        # invalid input, keep the last-good state" convention this panel's
        # own _update_preview() already uses.
        _orig_offset_changed = self.attachment_panel.attachmentOffsetChanged

        def _safe_offset_changed(index, value):
            try:
                _orig_offset_changed(index, value)
            except ValueError:
                pass

        self.attachment_panel.attachmentOffsetChanged = _safe_offset_changed

    @classmethod
    def for_existing(cls, container):
        """Build a panel pre-populated to re-edit an existing SMDComponent.

        Creates a fresh temporary ghost carrying the container's current
        attachment state (MapMode/AttachmentSupport/AttachmentOffset/
        MapReversed/Placement), so the embedded Attachment editor opens
        showing the real current attachment instead of a blank one.
        """
        doc = container.Document
        ghost = doc.addObject("Part::Feature", "SMDComponentPreview")
        ghost.addExtension("Part::AttachExtensionPython")
        for prop in ("MapMode", "AttachmentSupport", "AttachmentOffset", "MapReversed"):
            if hasattr(container, prop):
                try:
                    setattr(ghost, prop, getattr(container, prop))
                except Exception:
                    pass
        ghost.Placement = container.Placement
        # Hide the existing shadow solids while editing so they don't
        # visually clash with the live-preview ghost -- accept() deletes them
        # outright; reject() restores their visibility (see reject() below).
        for shadow in getattr(container, "ShadowSolids", []):
            if shadow.ViewObject is not None:
                shadow.ViewObject.Visibility = False
        doc.recompute()
        return cls(doc, ghost, existing=container)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Create SMD Component")
        fl = QtWidgets.QFormLayout(w)

        self.combo_type = QtWidgets.QComboBox()
        self.combo_type.addItems(_COMPONENT_TYPES)
        fl.addRow("Component type:", self.combo_type)

        self.combo_size = QtWidgets.QComboBox()
        self.combo_size.addItems(sorted(EIA_SIZE_TABLE.keys()))
        self.combo_size.setCurrentText("0402")
        fl.addRow("Size:", self.combo_size)

        self.combo_position = QtWidgets.QComboBox()
        for label, _value in _POSITION_MODES:
            self.combo_position.addItem(label, _value)
        fl.addRow("Impedance boundary position:", self.combo_position)

        self.value_stack = QtWidgets.QStackedWidget()
        self.edit_r, self.combo_r = QtWidgets.QLineEdit(), QtWidgets.QComboBox()
        self.edit_c, self.combo_c = QtWidgets.QLineEdit(), QtWidgets.QComboBox()
        self.edit_l, self.combo_l = QtWidgets.QLineEdit(), QtWidgets.QComboBox()
        self.value_stack.addWidget(make_unit_row(self.edit_r, self.combo_r, R_UNITS))
        self.value_stack.addWidget(make_unit_row(self.edit_c, self.combo_c, C_UNITS))
        self.value_stack.addWidget(make_unit_row(self.edit_l, self.combo_l, L_UNITS))
        fl.addRow("Value:", self.value_stack)

        self.combo_r.currentIndexChanged.connect(
            lambda new: rescale_on_unit_change(self.edit_r, self.combo_r, R_UNITS, new))
        self.combo_c.currentIndexChanged.connect(
            lambda new: rescale_on_unit_change(self.edit_c, self.combo_c, C_UNITS, new))
        self.combo_l.currentIndexChanged.connect(
            lambda new: rescale_on_unit_change(self.edit_l, self.combo_l, L_UNITS, new))

        self.chk_pads = QtWidgets.QCheckBox("Generate pads")
        self.chk_pads.setChecked(True)
        fl.addRow("", self.chk_pads)

        self.chk_solder = QtWidgets.QCheckBox("Include solder joint")
        self.chk_solder.setChecked(True)
        fl.addRow("", self.chk_solder)

        self.spin_pad_thickness = QtWidgets.QDoubleSpinBox()
        self.spin_pad_thickness.setRange(0.001, 1.0)
        self.spin_pad_thickness.setDecimals(3)
        self.spin_pad_thickness.setSingleStep(0.005)
        self.spin_pad_thickness.setSuffix(" mm")
        self.spin_pad_thickness.setValue(0.035)
        self.spin_pad_thickness.setToolTip("PCB pad copper thickness (e.g. 0.035 mm = 1 oz)")
        fl.addRow("Pad thickness:", self.spin_pad_thickness)

        self.adv_group = QtWidgets.QGroupBox("Advanced")
        self.adv_group.setCheckable(True)
        self.adv_group.setChecked(False)
        adv_fl = QtWidgets.QFormLayout(self.adv_group)
        self.combo_pad_group = QtWidgets.QComboBox()
        self._populate_pad_group_combo()
        adv_fl.addRow("Pad conductor group:", self.combo_pad_group)
        fl.addRow(self.adv_group)

        self.combo_type.currentIndexChanged.connect(self._on_type_changed)
        self.combo_size.currentIndexChanged.connect(lambda _: self._update_preview())
        self.combo_position.currentIndexChanged.connect(lambda _: self._update_preview())
        self.chk_pads.toggled.connect(self._on_pads_toggled)
        self.chk_solder.toggled.connect(lambda _: self._update_preview())
        self.spin_pad_thickness.valueChanged.connect(lambda _: self._update_preview())

        if self.existing is not None:
            self._populate_from_existing()
        else:
            self._on_type_changed(0)   # seeds default value/unit (doesn't affect geometry)
            self._on_pads_toggled(self.chk_pads.isChecked())   # sync enabled state, builds preview
        return w

    def _populate_from_existing(self):
        e = self.existing
        idx = _COMPONENT_TYPES.index(e.ComponentType) if e.ComponentType in _COMPONENT_TYPES else 0
        self.combo_type.setCurrentIndex(idx)
        self._on_type_changed(idx)   # explicit call: guarantees value_stack/default unit
                                      # are set even if setCurrentIndex was a same-index no-op
        edit, combo, units = self._value_widgets()
        set_unit_field(edit, combo, e.ValueSI, units)   # overwrites the type default with the real value

        if e.SizeCode in EIA_SIZE_TABLE:
            self.combo_size.setCurrentText(e.SizeCode)

        # getattr default matters for re-editing a component saved before
        # this property existed -- it must show "Flipped" (today's only
        # historical behavior), not an arbitrary/blank selection.
        found_idx = self.combo_position.findData(getattr(e, "ImpedanceBoundaryPosition", "Flipped"))
        if found_idx >= 0:
            self.combo_position.setCurrentIndex(found_idx)

        self.chk_pads.setChecked(e.IncludePads)
        self._on_pads_toggled(e.IncludePads)
        self.chk_solder.setChecked(e.IncludeSolderJoint)
        self.spin_pad_thickness.setValue(e.PadThickness)

        override = getattr(e, "PadConductorGroupOverride", None)
        if override is not None:
            self.adv_group.setChecked(True)
            found_idx = self.combo_pad_group.findData(override.Name)
            if found_idx >= 0:
                self.combo_pad_group.setCurrentIndex(found_idx)

        self._update_preview()

    def _populate_pad_group_combo(self):
        from features import find_conductor_groups
        self.combo_pad_group.clear()
        self.combo_pad_group.addItem("(default shared group)", None)
        for g in find_conductor_groups(self.doc):
            self.combo_pad_group.addItem(g.Label, g.Name)

    # ------------------------------------------------------------------
    # Widget helpers
    # ------------------------------------------------------------------

    def _value_widgets(self):
        """Return (edit, combo, units) for the currently selected component type."""
        edit, combo = [(self.edit_r, self.combo_r),
                        (self.edit_c, self.combo_c),
                        (self.edit_l, self.combo_l)][self.combo_type.currentIndex()]
        units, _, _ = _TYPE_DEFAULTS[self.combo_type.currentIndex()]
        return edit, combo, units

    def _on_type_changed(self, idx):
        self.value_stack.setCurrentIndex(idx)
        units, default_val, default_unit = _TYPE_DEFAULTS[idx]
        edit, combo, _ = self._value_widgets()
        unit_names = [u[0] for u in units]
        unit_idx = unit_names.index(default_unit)
        combo.blockSignals(True)
        combo.setCurrentIndex(unit_idx)
        combo.setProperty("_prev_idx", unit_idx)
        combo.blockSignals(False)
        edit.setText(f"{default_val:g}")

    def _on_pads_toggled(self, checked):
        if not checked:
            self.chk_solder.setChecked(False)
        self.chk_solder.setEnabled(checked)
        self.spin_pad_thickness.setEnabled(checked)
        self._update_preview()

    # ------------------------------------------------------------------
    # Live preview
    # ------------------------------------------------------------------

    def _update_preview(self):
        # Geometry depends on size/pads/solder-joint/pad-thickness, never on
        # component type or value -- those only affect the ImpedanceBoundary
        # at accept(). Placement is owned entirely by the attachment panel
        # (self.ghost.Placement is kept live-updated by it), so this method
        # only ever touches Shape.
        try:
            parts = build_smd_component(
                self.combo_size.currentText(),
                include_pads=self.chk_pads.isChecked(),
                include_solder_joint=self.chk_solder.isChecked(),
                position_mode=self.combo_position.currentData(),
                pad_thickness=self.spin_pad_thickness.value(),
            )
        except (ValueError, KeyError, RuntimeError):
            return   # leave the last-good preview showing
        c = parts["contacts"]
        solids = [solid for _name, _material_key, solid in parts["dielectric_layers"]]
        solids += [c["left_lower"], c["left_upper"], c["right_lower"], c["right_upper"]]
        if parts["pads"]:
            solids += list(parts["pads"])
        if parts["solder"]:
            solids += list(parts["solder"])
        self.ghost.Shape = Part.makeCompound(solids)

    # ------------------------------------------------------------------
    # Task dialog protocol
    # ------------------------------------------------------------------

    def accept(self):
        from features.smd_component import create_smd_component, delete_smd_component

        # Finalize the attachment panel's state and detach its selection
        # observer/preview transparency (writeParameters()/cleanUp() rather
        # than its own accept(), which also calls Gui.Control.closeDialog()
        # and transaction handling this panel owns instead -- see __init__).
        self.attachment_panel.writeParameters()
        self.attachment_panel.cleanUp()

        edit, combo, units = self._value_widgets()
        value_si = read_si_field(edit, combo, units, 0.0)

        pad_group = None
        if self.adv_group.isChecked():
            group_name = self.combo_pad_group.currentData()
            if group_name:
                pad_group = self.doc.getObject(group_name)

        # Re-editing: regenerate the whole component from scratch rather than
        # patching properties in place (no per-field diffing needed). Reuse
        # the old PortIndex so port numbering doesn't shift.
        port_index = None
        if self.existing is not None:
            old_boundary = getattr(self.existing, "ImpedanceBoundaryObj", None)
            if old_boundary is not None:
                port_index = old_boundary.PortIndex
            delete_smd_component(self.doc, self.existing)

        new_container = create_smd_component(
            self.doc,
            self.combo_type.currentText(),
            self.combo_size.currentText(),
            value_si,
            include_pads=self.chk_pads.isChecked(),
            include_solder_joint=self.chk_solder.isChecked(),
            placement=self.ghost.Placement,
            pad_conductor_group=pad_group,
            pad_thickness=self.spin_pad_thickness.value(),
            port_index=port_index,
            position_mode=self.combo_position.currentData(),
        )

        # create_smd_component() only receives a plain resolved Placement, so
        # it has no way to record *how* that placement was derived. Copy the
        # ghost's actual attachment state (which face/plane, which mode, any
        # offset) onto the new container too -- otherwise a later re-edit
        # would see MapMode="Deactivated"/no Support, i.e. the attachment
        # info would appear lost even though the position itself carried over.
        for prop in ("MapMode", "AttachmentSupport", "AttachmentOffset", "MapReversed"):
            if hasattr(self.ghost, prop) and hasattr(new_container, prop):
                try:
                    setattr(new_container, prop, getattr(self.ghost, prop))
                except Exception:
                    pass
        new_container.Placement = self.ghost.Placement

        if self.ghost.Name in [o.Name for o in self.doc.Objects]:
            self.doc.removeObject(self.ghost.Name)
        self.doc.recompute()
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        self.attachment_panel.cleanUp()
        if self.existing is not None:
            # Leave the original component exactly as it was.
            for shadow in getattr(self.existing, "ShadowSolids", []):
                if shadow.ViewObject is not None:
                    shadow.ViewObject.Visibility = True
        if self.ghost.Name in [o.Name for o in self.doc.Objects]:
            self.doc.removeObject(self.ghost.Name)
        FreeCADGui.Control.closeDialog()
        return True

    def getStandardButtons(self):
        try:
            return int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        except TypeError:
            # PySide6's QDialogButtonBox.StandardButton is a real enum.Flag
            # and isn't int()-convertible directly (unlike PySide2's) --
            # same fallback used by every other panel in this codebase.
            return (QtWidgets.QDialogButtonBox.Ok.value
                    | QtWidgets.QDialogButtonBox.Cancel.value)
