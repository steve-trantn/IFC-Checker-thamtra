"""Chạy file IDS (buildingSMART Information Delivery Specification) bằng ifctester."""

from __future__ import annotations

from .engine import Issue, RuleStat
from .model_loader import ModelInfo


def ids_available() -> bool:
    try:
        import ifctester  # noqa: F401
        return True
    except Exception:
        return False


def run_ids(ids_path: str, model: ModelInfo, skip_ids: set | None = None):
    """Trả về (issues, rule_stats, step_id các đối tượng không đạt)."""
    from ifctester import ids as ids_mod

    spec_doc = ids_mod.open(ids_path)
    from .model_loader import reopen_ifc
    spec_doc.validate(reopen_ifc(model))
    part_ids = skip_ids or set()
    rec_by_id = {r.step_id: r for r in model.records}

    issues, stats, failed_ids = [], [], set()
    for n, spec in enumerate(spec_doc.specifications, start=1):
        rid = f"IDS-{(spec.identifier or n)}"
        required = getattr(spec, "minOccurs", 0) != 0
        sev = "ERROR"
        applicable = [e for e in spec.applicable_entities if e.id() not in part_ids]
        failed = {e.id() for e in spec.failed_entities if e.id() not in part_ids}
        st = RuleStat(model.file_name, rid, f"[IDS] {spec.name}", sev,
                      applicable=len(applicable), passed=len(applicable) - len(failed))
        stats.append(st)
        if required and not spec.applicable_entities:
            issues.append(Issue(model.file_name, "", 0, "", "", rid, sev, "IDS",
                                f"Yêu cầu '{spec.name}' bắt buộc nhưng không có đối tượng nào áp dụng"))
        for facet in spec.requirements:
            try:
                req_txt = facet.to_string("requirement", spec, facet)
            except Exception:
                req_txt = type(facet).__name__
            for fail in getattr(facet, "failures", []):
                el = fail.get("element")
                if el is None or el.id() in part_ids:
                    continue
                rec = rec_by_id.get(el.id())
                failed_ids.add(el.id())
                issues.append(Issue(
                    file=model.file_name,
                    guid=getattr(el, "GlobalId", "") or "",
                    step_id=el.id(),
                    ifc_class=el.is_a(),
                    element_name=rec.display_name() if rec else (getattr(el, "Name", "") or ""),
                    rule_id=rid,
                    severity=sev,
                    category="IDS",
                    message=f"{req_txt}: {fail.get('reason', '')}",
                    expected=req_txt,
                    reference=spec.name,
                ))
    return issues, stats, failed_ids
