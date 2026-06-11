import os

try:
    from PySide2 import QtWidgets, QtGui, QtCore
except ImportError:
    from PySide6 import QtWidgets, QtGui, QtCore


def _read_mesh_stats(path):
    """Return (n_nodes, n_elements) by scanning .msh2 section headers.

    Only reads a few lines per section to stay fast on large meshes.
    Returns (None, None) on any failure.
    """
    try:
        n_nodes = None
        n_elements = None
        with open(path, "r") as fh:
            in_nodes = False
            in_elements = False
            for line in fh:
                line = line.strip()
                if line == "$Nodes":
                    in_nodes = True
                    continue
                if in_nodes:
                    n_nodes = int(line)
                    in_nodes = False
                if line == "$Elements":
                    in_elements = True
                    continue
                if in_elements:
                    n_elements = int(line)
                    in_elements = False
                if n_nodes is not None and n_elements is not None:
                    break
        return n_nodes, n_elements
    except Exception:
        return None, None


def _fmt_size(path):
    try:
        sz = os.path.getsize(path)
        if sz < 1024:
            return f"{sz} B"
        if sz < 1024 ** 2:
            return f"{sz / 1024:.1f} KB"
        return f"{sz / 1024 ** 2:.1f} MB"
    except OSError:
        return "—"


class MeshPanel:
    def __init__(self, obj):
        self.obj = obj
        self.form = self._build()
        self._populate()

    def _build(self):
        w = QtWidgets.QWidget()
        w.setWindowTitle("Palace Mesh")
        root = QtWidgets.QVBoxLayout(w)

        # --- Sizing group ---
        sizing = QtWidgets.QGroupBox("Element Sizing")
        fl = QtWidgets.QFormLayout(sizing)

        self.edit_cl_max = QtWidgets.QLineEdit()
        self.edit_cl_max.setToolTip(
            "Global maximum element size in model units (mm when L0=1e-3).\n"
            "Leave blank to use Gmsh's automatic sizing."
        )
        self.edit_cl_max.setPlaceholderText("Gmsh default")
        fl.addRow("Max element size:", self.edit_cl_max)

        self.edit_cl_min = QtWidgets.QLineEdit()
        self.edit_cl_min.setToolTip(
            "Global minimum element size in model units.\n"
            "Leave blank to use Gmsh's automatic sizing."
        )
        self.edit_cl_min.setPlaceholderText("Gmsh default")
        fl.addRow("Min element size:", self.edit_cl_min)

        self.edit_cond_size = QtWidgets.QLineEdit()
        self.edit_cond_size.setPlaceholderText("disabled")
        self.edit_cond_size.setToolTip(
            "Element size (mm) right at conductor and dielectric surfaces.\n"
            "Mesh grows smoothly from this value back to the global max size.\n"
            "Leave blank to use global sizing near conductors."
        )
        fl.addRow("Conductor size:", self.edit_cond_size)

        self.edit_port_size = QtWidgets.QLineEdit()
        self.edit_port_size.setPlaceholderText("disabled")
        self.edit_port_size.setToolTip(
            "Element size (mm) right at port faces and integration edges.\n"
            "Also controls the node density on integration edge curves.\n"
            "Leave blank to use global sizing near ports."
        )
        fl.addRow("Port size:", self.edit_port_size)

        self.edit_refine_dist = QtWidgets.QLineEdit()
        self.edit_refine_dist.setPlaceholderText("auto")
        self.edit_refine_dist.setToolTip(
            "Distance (mm) over which mesh transitions from the refined size\n"
            "back to the global max size. Leave blank for automatic\n"
            "(30% of the model bounding-box extent)."
        )
        fl.addRow("Refine distance:", self.edit_refine_dist)

        root.addWidget(sizing)

        # --- Status group ---
        status = QtWidgets.QGroupBox("Mesh Status")
        sl = QtWidgets.QFormLayout(status)

        self.lbl_file = QtWidgets.QLabel("—")
        self.lbl_file.setWordWrap(True)
        sl.addRow("Mesh file:", self.lbl_file)

        self.lbl_size = QtWidgets.QLabel("—")
        sl.addRow("File size:", self.lbl_size)

        self.lbl_stats = QtWidgets.QLabel("—")
        sl.addRow("Nodes / Elements:", self.lbl_stats)

        root.addWidget(status)

        # --- Attribute visibility group ---
        vis = QtWidgets.QGroupBox("Visible Attributes")
        vl = QtWidgets.QVBoxLayout(vis)
        self.attr_list = QtWidgets.QListWidget()
        self.attr_list.setMaximumHeight(160)
        self.attr_list.itemChanged.connect(self._on_attr_visibility_changed)
        vl.addWidget(self.attr_list)
        root.addWidget(vis)

        # --- Generate button ---
        self.btn_generate = QtWidgets.QPushButton("Generate Mesh")
        self.btn_generate.setToolTip("Generate the mesh now (equivalent to the toolbar button)")
        self.btn_generate.clicked.connect(self._generate)
        root.addWidget(self.btn_generate)

        root.addStretch()
        return w

    def _populate(self):
        o = self.obj
        cl_max = getattr(o, "MeshCharacteristicLengthMax", 0.0)
        cl_min = getattr(o, "MeshCharacteristicLengthMin", 0.0)
        self.edit_cl_max.setText("" if cl_max == 0.0 else str(cl_max))
        self.edit_cl_min.setText("" if cl_min == 0.0 else str(cl_min))
        cond_size    = getattr(o, "MeshConductorSize", 0.0)
        port_size    = getattr(o, "MeshPortSize", 0.0)
        refine_dist  = getattr(o, "MeshRefineDistance", 0.0)
        self.edit_cond_size.setText("" if cond_size == 0.0 else str(cond_size))
        self.edit_port_size.setText("" if port_size == 0.0 else str(port_size))
        self.edit_refine_dist.setText("" if refine_dist == 0.0 else str(refine_dist))
        self._refresh_status()
        self._populate_attrs()

    def _refresh_status(self):
        mesh_file = getattr(self.obj, "MeshFile", "")
        if mesh_file and os.path.isfile(mesh_file):
            self.lbl_file.setText(mesh_file)
            self.lbl_size.setText(_fmt_size(mesh_file))
            n_nodes, n_elements = _read_mesh_stats(mesh_file)
            if n_nodes is not None:
                self.lbl_stats.setText(f"{n_nodes:,} / {n_elements:,}")
            else:
                self.lbl_stats.setText("—")
        else:
            self.lbl_file.setText("Not generated")
            self.lbl_size.setText("—")
            self.lbl_stats.setText("—")

    def _populate_attrs(self):
        self.attr_list.blockSignals(True)
        self.attr_list.clear()
        try:
            doc = self.obj.Document
            vp = getattr(getattr(self.obj, "ViewObject", None), "Proxy", None)
            known_surf = getattr(vp, "_known_surf_attrs", None)
            known_vol  = getattr(vp, "_known_vol_attrs",  None)
            if known_surf is None or known_vol is None:
                mesh_file = getattr(self.obj, "MeshFile", "")
                if not mesh_file or not os.path.isfile(mesh_file):
                    return
                from features.mesh import _parse_msh2
                _, _st, surf_groups, _vt, vol_groups = _parse_msh2(mesh_file)
                known_surf = sorted(set(surf_groups))
                known_vol  = sorted(set(vol_groups))
            from features.mesh import (_build_surf_attr_labels, _build_vol_attr_labels,
                                        _build_surf_color_map, _build_vol_color_map)
            surf_labels = _build_surf_attr_labels(doc)
            vol_labels  = _build_vol_attr_labels(doc)
            surf_map    = _build_surf_color_map(doc)
            vol_map     = _build_vol_color_map(doc)
            hidden_surf = set(getattr(self.obj, "HiddenSurfAttributes", []) or [])
            hidden_vol  = set(getattr(self.obj, "HiddenVolAttributes",  []) or [])

            def _separator(text):
                sep = QtWidgets.QListWidgetItem(text)
                sep.setFlags(QtCore.Qt.NoItemFlags)
                font = sep.font()
                font.setItalic(True)
                sep.setFont(font)
                self.attr_list.addItem(sep)

            def _attr_item(dim, attr, label, color_map, hidden_set):
                item = QtWidgets.QListWidgetItem(f"{label}  (attr {attr})")
                item.setData(QtCore.Qt.UserRole, (dim, attr))
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(
                    QtCore.Qt.Unchecked if attr in hidden_set else QtCore.Qt.Checked
                )
                r, g, b = color_map.get(attr, (0.5, 0.5, 0.5))
                px = QtGui.QPixmap(14, 14)
                px.fill(QtGui.QColor(int(r * 255), int(g * 255), int(b * 255)))
                item.setIcon(QtGui.QIcon(px))
                self.attr_list.addItem(item)

            if known_surf:
                _separator("── Surface Boundaries ──")
                for attr in known_surf:
                    _attr_item("surf", attr,
                               surf_labels.get(attr, f"Surface attr {attr}"),
                               surf_map, hidden_surf)
            if known_vol:
                _separator("── Material Domains ──")
                for attr in known_vol:
                    _attr_item("vol", attr,
                               vol_labels.get(attr, f"Volume attr {attr}"),
                               vol_map, hidden_vol)
        except Exception:
            pass
        finally:
            self.attr_list.blockSignals(False)

    def _on_attr_visibility_changed(self, _item):
        hidden_surf, hidden_vol = [], []
        for i in range(self.attr_list.count()):
            it = self.attr_list.item(i)
            role = it.data(QtCore.Qt.UserRole)
            if role is None:
                continue   # separator item
            if it.checkState() == QtCore.Qt.Unchecked:
                dim, attr = role
                if dim == "surf":
                    hidden_surf.append(attr)
                else:
                    hidden_vol.append(attr)
        self.obj.HiddenSurfAttributes = hidden_surf
        self.obj.HiddenVolAttributes  = hidden_vol
        vp = getattr(getattr(self.obj, "ViewObject", None), "Proxy", None)
        if vp is not None:
            try:
                vp._update_colors()
            except Exception:
                pass

    def _float_or_zero(self, widget):
        txt = widget.text().strip()
        if not txt:
            return 0.0
        try:
            return float(txt)
        except ValueError:
            return 0.0

    def _generate(self):
        # Save settings first, then trigger the mesh command.
        self.accept()
        try:
            import FreeCADGui
            FreeCADGui.runCommand("Palace_Mesh")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(
                self.form, "Generate Mesh",
                f"Could not start mesh generation:\n{exc}"
            )

    def accept(self):
        o = self.obj
        o.MeshCharacteristicLengthMax = self._float_or_zero(self.edit_cl_max)
        o.MeshCharacteristicLengthMin = self._float_or_zero(self.edit_cl_min)
        o.MeshConductorSize   = self._float_or_zero(self.edit_cond_size)
        o.MeshPortSize        = self._float_or_zero(self.edit_port_size)
        o.MeshRefineDistance  = self._float_or_zero(self.edit_refine_dist)
        o.Document.recompute()
        import FreeCADGui
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        import FreeCADGui
        FreeCADGui.Control.closeDialog()
        return True

    def getStandardButtons(self):
        try:
            return int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        except TypeError:
            return (QtWidgets.QDialogButtonBox.Ok.value
                    | QtWidgets.QDialogButtonBox.Cancel.value)
