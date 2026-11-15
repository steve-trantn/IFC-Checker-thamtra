"""Đọc file IFC và trích xuất thông tin phi hình học của từng đối tượng."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.util.classification as ucls
import ifcopenshell.util.element as ue

from .textnorm import norm, value_to_str

EXCLUDED_ELEMENT_CLASSES = ("IfcFeatureElement", "IfcVirtualElement")


@dataclass
class ElementRecord:
    file: str
    step_id: int
    guid: str
    ifc_class: str
    kind: str  # "element" | "spatial"
    predefined_type: str
    name: str
    object_type: str
    description: str
    tag: str
    long_name: str
    type_name: str
    type_class: str
    container_name: str
    container_class: str
    parent_guid: str
    is_part: bool
    has_geometry: bool
    materials: list[str]
    classifications: list[str]  # "Hệ thống: Mã - Tên"
    psets: dict[str, dict[str, object]]
    entity: object = field(repr=False, default=None)
    _index: dict | None = field(repr=False, default=None)

    # --- tra cứu thuộc tính theo tên đã chuẩn hoá ---
    def index(self) -> dict:
        """{norm(pset): (raw_pset, {norm(prop): (raw_prop, value)})}"""
        if self._index is None:
            idx = {}
            for pset, props in self.psets.items():
                idx[norm(pset)] = (pset, {norm(k): (k, v) for k, v in props.items()})
            self._index = idx
        return self._index

    def is_a(self, cls: str) -> bool:
        if self.entity is not None:
            try:
                return bool(self.entity.is_a(cls))
            except Exception:
                pass
        return norm(self.ifc_class) == norm(cls)

    def attribute(self, name: str):
        n = norm(name)
        mapping = {
            "name": self.name,
            "description": self.description,
            "objecttype": self.object_type,
            "tag": self.tag,
            "predefinedtype": self.predefined_type,
            "globalid": self.guid,
            "longname": self.long_name,
            "typename": self.type_name,
            "type": self.type_name,
            "class": self.ifc_class,
            "ifcclass": self.ifc_class,
        }
        if n in mapping:
            return mapping[n]
        if self.entity is not None:
            try:
                v = getattr(self.entity, name.strip())
                if isinstance(v, ifcopenshell.entity_instance):
                    return str(v)
                return v
            except Exception:
                return None
        return None

    def search_text(self) -> str:
        return norm(" ".join(x for x in (self.name, self.object_type, self.type_name) if x))

    def display_name(self) -> str:
        return self.name or self.type_name or self.object_type or f"#{self.step_id}"


@dataclass
class ModelInfo:
    path: str
    schema: str = ""
    project_name: str = ""
    application: str = ""
    records: list[ElementRecord] = field(default_factory=list)
    error: str = ""
    warnings: list[str] = field(default_factory=list)   # lỗi chất lượng file (không chặn kiểm tra)
    load_seconds: float = 0.0
    ifc: object = field(repr=False, default=None)

    @property
    def file_name(self) -> str:
        return os.path.basename(self.path)

    @property
    def elements(self) -> list[ElementRecord]:
        return [r for r in self.records if r.kind == "element"]

    @property
    def part_count(self) -> int:
        return sum(1 for r in self.records if r.is_part)


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _clean_psets(raw: dict) -> dict[str, dict[str, object]]:
    out = {}
    for pset, props in (raw or {}).items():
        if not isinstance(props, dict):
            continue
        clean = {}
        for k, v in props.items():
            if k == "id":
                continue
            if isinstance(v, dict):  # complex property
                v = "; ".join(f"{a}={value_to_str(b)}" for a, b in v.items() if a != "id")
            elif isinstance(v, ifcopenshell.entity_instance):
                v = str(v)
            clean[k] = v
        out[pset] = clean
    return out


def _classifications(e) -> list[str]:
    out = []
    try:
        for ref in ucls.get_references(e):
            code = getattr(ref, "Identification", None) or getattr(ref, "ItemReference", None) or ""
            system = ""
            try:
                src = ucls.get_classification(ref)
                system = _s(getattr(src, "Name", ""))
            except Exception:
                pass
            label = " - ".join(x for x in (_s(code), _s(ref.Name)) if x)
            out.append(f"{system}: {label}" if system else label)
    except Exception:
        pass
    return out


def _materials(e) -> list[str]:
    try:
        mats = ue.get_materials(e, should_inherit=True)
        return [_s(m.Name) for m in mats if _s(getattr(m, "Name", ""))]
    except Exception:
        return []


def _container(e):
    """Không gian chứa đối tượng; nếu là phần tử con thì lấy theo đối tượng cha."""
    c = ue.get_container(e)
    cur = e
    for _ in range(10):
        if c is not None:
            break
        parent = ue.get_aggregate(cur)
        if parent is None and hasattr(ue, "get_nest"):
            parent = ue.get_nest(cur)
        if parent is None:
            break
        if not parent.is_a("IfcElement"):  # cha là không gian (Site/Storey...)
            return parent
        c = ue.get_container(parent)
        cur = parent
    return c


def build_record(path: str, e, kind: str) -> ElementRecord:
    etype = ue.get_type(e) if kind == "element" else None
    parent = ue.get_aggregate(e)
    is_part = bool(parent is not None and kind == "element" and parent.is_a("IfcElement"))
    if kind == "element":
        cont = _container(e)
    else:
        cont = parent
    try:
        ptype = ue.get_predefined_type(e)
    except Exception:
        ptype = getattr(e, "PredefinedType", None)
    try:
        psets = _clean_psets(ue.get_psets(e))
    except Exception:
        psets = {}
    return ElementRecord(
        file=path,
        step_id=e.id(),
        guid=_s(getattr(e, "GlobalId", "")),
        ifc_class=e.is_a(),
        kind=kind,
        predefined_type=_s(ptype),
        name=_s(getattr(e, "Name", "")),
        object_type=_s(getattr(e, "ObjectType", "")),
        description=_s(getattr(e, "Description", "")),
        tag=_s(getattr(e, "Tag", "")),
        long_name=_s(getattr(e, "LongName", "")),
        type_name=_s(etype.Name) if etype is not None else "",
        type_class=etype.is_a() if etype is not None else "",
        container_name=_s(getattr(cont, "Name", "")) if cont is not None else "",
        container_class=cont.is_a() if cont is not None else "",
        parent_guid=_s(getattr(parent, "GlobalId", "")) if parent is not None else "",
        is_part=is_part,
        has_geometry=bool(getattr(e, "Representation", None)),
        materials=_materials(e) if kind == "element" else [],
        classifications=_classifications(e),
        psets=psets,
        entity=e,
    )


def load_model(path: str, progress=None, cancel=None) -> ModelInfo:
    """Đọc 1 file IFC. progress(done, total, msg) được gọi định kỳ."""
    info = ModelInfo(path=path)
    t0 = time.time()
    try:
        if progress:
            progress(0, 1, f"Đang mở {os.path.basename(path)} ...")
        f = ifcopenshell.open(path)
    except Exception as ex:  # file hỏng / không phải IFC
        info.error = f"Không đọc được file IFC: {ex}"
        return info
    info.ifc = f
    info.schema = f.schema_identifier if hasattr(f, "schema_identifier") else f.schema
    try:
        proj = f.by_type("IfcProject")
        info.project_name = _s(proj[0].Name) if proj else ""
    except Exception:
        pass
    try:
        apps = f.by_type("IfcApplication")
        info.application = " / ".join(sorted({_s(a.ApplicationFullName) for a in apps if a.ApplicationFullName}))
    except Exception:
        pass

    try:
        bad = sum(1 for r in f.by_type("IfcShapeRepresentation")
                  if any(i.is_a("IfcStyledItem") for i in (r.Items or ())))
        if bad:
            info.warnings.append(f"{bad} biểu diễn hình học chứa IfcStyledItem trong danh sách Items (lỗi xuất "
                                 "IFC - nhiều phần mềm sẽ không hiển thị được các đối tượng này)")
    except Exception:
        pass

    elements = [
        e for e in f.by_type("IfcElement")
        if not any(e.is_a(x) for x in EXCLUDED_ELEMENT_CLASSES)
    ]
    spatial_root = "IfcSpatialStructureElement" if f.schema == "IFC2X3" else "IfcSpatialElement"
    try:
        spatial = list(f.by_type(spatial_root))
    except Exception:
        spatial = []

    todo = [(e, "element") for e in elements] + [(s, "spatial") for s in spatial]
    total = len(todo)
    for i, (e, kind) in enumerate(todo):
        if cancel and cancel():
            info.error = "Đã huỷ"
            break
        try:
            info.records.append(build_record(path, e, kind))
        except Exception as ex:
            info.records.append(ElementRecord(
                file=path, step_id=e.id(), guid=_s(getattr(e, "GlobalId", "")), ifc_class=e.is_a(),
                kind=kind, predefined_type="", name=_s(getattr(e, "Name", "")), object_type="",
                description=f"Lỗi đọc: {ex}", tag="", long_name="", type_name="", type_class="",
                container_name="", container_class="", parent_guid="", is_part=False,
                has_geometry=False, materials=[], classifications=[], psets={}, entity=e))
        if progress and (i % 200 == 0 or i == total - 1):
            progress(i + 1, total, f"{os.path.basename(path)}: {i + 1}/{total} đối tượng")
    info.load_seconds = time.time() - t0
    return info
