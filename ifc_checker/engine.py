"""Bộ máy kiểm tra: áp quy tắc BEP lên các đối tượng IFC và thu thập lỗi."""

from __future__ import annotations

import fnmatch
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .model_loader import ElementRecord, ModelInfo
from .rules import Rule, RuleSet
from .textnorm import is_empty, norm, parse_number, split_list, strip_accents, value_to_str

SEV_ORDER = {"ERROR": 0, "WARNING": 1, "INFO": 2}


@dataclass
class Issue:
    file: str
    guid: str
    step_id: int
    ifc_class: str
    element_name: str
    rule_id: str
    severity: str
    category: str  # PROPERTY / ATTRIBUTE / CLASS / ... / IDS / CLASSIFY
    message: str
    pset: str = ""
    prop: str = ""
    actual: str = ""
    expected: str = ""
    reference: str = ""


@dataclass
class RuleStat:
    file: str
    rule_id: str
    description: str
    severity: str
    applicable: int = 0
    passed: int = 0

    @property
    def failed(self) -> int:
        return self.applicable - self.passed

    @property
    def rate(self) -> float:
        return 100.0 * self.passed / self.applicable if self.applicable else 100.0


@dataclass
class FileSummary:
    file: str
    path: str
    schema: str
    project: str
    application: str
    total_elements: int
    checked: int
    parts_skipped: int
    proxies: int
    passed_elements: int
    errors: int
    warnings: int
    infos: int
    error: str = ""

    @property
    def compliance(self) -> float:
        return 100.0 * self.passed_elements / self.checked if self.checked else 0.0


@dataclass
class CheckOptions:
    # Cách xử lý cụm đối tượng (cha - con):
    #  "auto"    : tự nhận biết - cha không có thuộc tính (chỉ là nhóm) -> kiểm tra các con;
    #              cha có thuộc tính -> kiểm tra cha, bỏ các con (khối hình học tách nhỏ)
    #  "parents" : chỉ kiểm tra đối tượng cha / độc lập
    #  "all"     : kiểm tra tất cả
    part_mode: str = "auto"
    skip_parts: bool | None = None   # tương thích cũ: True -> "parents", False -> "all"
    check_classification: bool = True  # so khớp tên <-> lớp IFC theo bảng từ khoá
    empty_tokens: list[str] | None = None


@dataclass
class CheckResult:
    started: str = ""
    ruleset_source: str = ""
    files: list[FileSummary] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    rule_stats: list[RuleStat] = field(default_factory=list)
    class_counts: list[tuple] = field(default_factory=list)   # (file, class, predefined, count, parts, proxies?)
    inventory: list[tuple] = field(default_factory=list)      # (file, class, pset, prop, present, filled, total, samples)
    ai_suggestions: list[dict] = field(default_factory=list)
    ai_summary: str = ""
    clashes: list = field(default_factory=list)          # list[clash.Clash]
    clash_messages: list[str] = field(default_factory=list)

    def totals(self) -> dict:
        c = Counter(i.severity for i in self.issues)
        checked = sum(f.checked for f in self.files)
        passed = sum(f.passed_elements for f in self.files)
        return {
            "files": len(self.files),
            "checked": checked,
            "passed": passed,
            "compliance": 100.0 * passed / checked if checked else 0.0,
            "errors": c.get("ERROR", 0),
            "warnings": c.get("WARNING", 0),
            "infos": c.get("INFO", 0),
            "proxies": sum(f.proxies for f in self.files),
        }


# ---------------------------------------------------------------- Áp dụng

class _Applicability:
    def __init__(self, rule: Rule):
        items = split_list(rule.ifc_class, allow_comma=True) or ["*"]
        self.include = [c for c in items if not c.startswith("!")]
        self.exclude = [c[1:].strip() for c in items if c.startswith("!")]
        self.any = (not self.include) or "*" in self.include
        self.include = [c for c in self.include if c != "*"]
        self.ptypes = {x.upper() for x in split_list(rule.predefined_type, allow_comma=True)}
        self.name_re = re.compile(strip_accents(rule.name_filter), re.IGNORECASE) if rule.name_filter else None
        self.file_re = re.compile(strip_accents(rule.file_filter), re.IGNORECASE) if rule.file_filter else None
        self._file_cache: dict[str, bool] = {}

    def applies(self, rec: ElementRecord) -> bool:
        if self.any:
            if rec.kind != "element":
                return False
        elif not any(rec.is_a(c) for c in self.include):
            return False
        if self.exclude and any(rec.is_a(c) for c in self.exclude):
            return False
        if self.ptypes and rec.predefined_type.upper() not in self.ptypes:
            return False
        if self.name_re and not self.name_re.search(rec.search_text()):
            return False
        if self.file_re:
            ok = self._file_cache.get(rec.file)
            if ok is None:
                ok = self._file_cache[rec.file] = bool(self.file_re.search(strip_accents(os.path.basename(rec.file))))
            if not ok:
                return False
        return True


def _find_property(rec: ElementRecord, pset: str, prop: str):
    """Trả về (raw_pset, raw_prop, value, found_pset). value=None nếu không có."""
    idx = rec.index()
    np_ = norm(prop)
    ps = norm(pset)
    candidates = []
    if not ps or ps == "*":
        candidates = list(idx.values())
    elif any(ch in ps for ch in "*?["):
        candidates = [v for k, v in idx.items() if fnmatch.fnmatchcase(k, ps)]
    elif ps in idx:
        candidates = [idx[ps]]
    found_pset = bool(candidates)
    first_hit = None
    for raw_pset, props in candidates:
        if np_ in props:
            raw_prop, val = props[np_]
            if first_hit is None:
                first_hit = (raw_pset, raw_prop, val)
            if not is_empty(val):
                return raw_pset, raw_prop, val, True
    if first_hit:
        return first_hit[0], first_hit[1], first_hit[2], True
    # Một số phần mềm "làm phẳng" tên: Pset rỗng, thuộc tính tên "Tham số định danh.Tên nhóm"
    if ps and ps != "*" and not any(ch in ps for ch in "*?["):
        flat = f"{ps}.{np_}"
        for raw_pset, props in idx.values():
            if flat in props:
                raw_prop, val = props[flat]
                return raw_pset, raw_prop, val, True
    return "", "", None, found_pset


def _test_value(rule: Rule, val, empty_tokens) -> tuple[bool, str]:
    """Kiểm tra 1 giá trị theo điều kiện. Trả về (đạt, mô tả lỗi)."""
    cond = rule.condition
    if is_empty(val, empty_tokens):
        return False, "để trống"
    s = value_to_str(val)
    if cond == "EXISTS":
        return True, ""
    if cond == "EQUALS":
        return norm(s) == norm(rule.value), f"phải bằng '{rule.value}'"
    if cond == "NOT_EQUALS":
        return norm(s) != norm(rule.value), f"không được bằng '{rule.value}'"
    if cond in ("IN", "NOT_IN"):
        opts = {norm(x) for x in split_list(rule.value)}
        ok = norm(s) in opts
        if cond == "IN":
            return ok, f"phải thuộc [{rule.value}]"
        return not ok, f"không được thuộc [{rule.value}]"
    if cond == "REGEX":
        try:
            return bool(re.search(rule.value, s)), f"sai định dạng (yêu cầu {rule.value})"
        except re.error:
            return False, f"regex lỗi: {rule.value}"
    if cond in ("NUMBER", "RANGE"):
        n = parse_number(val)
        if n is None:
            return False, "không phải giá trị số"
        if cond == "NUMBER":
            return True, ""
        lo, _, hi = rule.value.partition("..")
        lo_n = parse_number(lo) if lo.strip() else None
        hi_n = parse_number(hi) if hi.strip() else None
        if lo_n is not None and n < lo_n:
            return False, f"nhỏ hơn {lo_n:g} (yêu cầu {rule.value})"
        if hi_n is not None and n > hi_n:
            return False, f"lớn hơn {hi_n:g} (yêu cầu {rule.value})"
        return True, ""
    return False, f"điều kiện không hỗ trợ: {cond}"


def _test_list(rule: Rule, values: list[str], label: str, empty_tokens) -> tuple[bool, str]:
    """Cho MATERIAL / CLASSIFICATION: đạt nếu có ít nhất 1 giá trị thoả (NOT_* thì tất cả phải thoả)."""
    values = [v for v in values if not is_empty(v, empty_tokens)]
    if not values:
        return False, f"chưa có {label}"
    if rule.condition == "EXISTS":
        return True, ""
    results = [_test_value(rule, v, empty_tokens) for v in values]
    if rule.condition.startswith("NOT_"):
        bad = [r for r in results if not r[0]]
        return (not bad), (bad[0][1] if bad else "")
    good = any(r[0] for r in results)
    return good, ("" if good else results[0][1])


def evaluate(rule: Rule, rec: ElementRecord, empty_tokens=None) -> Issue | None:
    t = rule.target
    actual = ""
    pset = prop = ""
    if t == "PROPERTY":
        raw_pset, raw_prop, val, found_pset = _find_property(rec, rule.pset, rule.name)
        pset, prop = raw_pset or rule.pset, raw_prop or rule.name
        if val is None and not raw_prop:
            if not found_pset and rule.pset not in ("", "*"):
                msg = f"Thiếu Property Set '{rule.pset}'"
            else:
                msg = f"Thiếu thuộc tính '{rule.name}'" + (f" trong '{rule.pset}'" if rule.pset not in ("", "*") else "")
            return _issue(rule, rec, msg, pset, prop, "")
        ok, why = _test_value(rule, val, empty_tokens)
        actual = value_to_str(val)
        if ok:
            return None
        return _issue(rule, rec, f"'{prop}' {why}", pset, prop, actual)
    if t == "ATTRIBUTE":
        val = rec.attribute(rule.name)
        ok, why = _test_value(rule, val, empty_tokens)
        if ok:
            return None
        return _issue(rule, rec, f"Thuộc tính {rule.name} {why}", "", rule.name, value_to_str(val))
    if t == "CLASS":
        opts = split_list(rule.value, allow_comma=True)
        c = rule.condition
        if c in ("IN", "EQUALS"):
            ok = any(rec.is_a(x) for x in opts)
            why = f"Lớp IFC phải là {rule.value}"
        elif c in ("NOT_IN", "NOT_EQUALS"):
            ok = not any(rec.is_a(x) for x in opts)
            why = f"Không được dùng lớp {rec.ifc_class}"
        elif c == "REGEX":
            ok = bool(re.search(rule.value, rec.ifc_class))
            why = f"Lớp IFC không khớp {rule.value}"
        else:
            ok, why = True, ""
        return None if ok else _issue(rule, rec, why, "", "", rec.ifc_class)
    if t == "CONTAINER":
        if not rec.container_class:
            return _issue(rule, rec, "Không thuộc cấu trúc không gian nào", "", "", "")
        c = rule.condition
        if c in ("IN", "EQUALS") and rule.value:
            opts = split_list(rule.value, allow_comma=True)
            if not any(norm(rec.container_class) == norm(x) for x in opts):
                return _issue(rule, rec, f"Phải nằm trong {rule.value}", "", "", rec.container_class)
        elif c == "REGEX" and rule.value:
            if not re.search(rule.value, rec.container_name or ""):
                return _issue(rule, rec, f"Tên không gian chứa không khớp {rule.value}", "", "",
                              rec.container_name)
        return None
    if t == "MATERIAL":
        ok, why = _test_list(rule, rec.materials, "vật liệu", empty_tokens)
        return None if ok else _issue(rule, rec, f"Vật liệu: {why}", "", "", "; ".join(rec.materials))
    if t == "CLASSIFICATION":
        codes = []
        for c in rec.classifications:  # "Hệ: Mã - Tên" -> thử cả chuỗi đầy đủ và phần mã
            codes.append(c)
            body = c.split(": ", 1)[-1]
            codes.append(body.split(" - ", 1)[0])
        ok, why = _test_list(rule, codes, "mã phân loại", empty_tokens)
        return None if ok else _issue(rule, rec, f"Phân loại: {why}", "", "", "; ".join(rec.classifications))
    return None


def _issue(rule: Rule, rec: ElementRecord, msg, pset, prop, actual) -> Issue:
    return Issue(
        file=rec.file, guid=rec.guid, step_id=rec.step_id, ifc_class=rec.ifc_class,
        element_name=rec.display_name(), rule_id=rule.id, severity=rule.severity,
        category=rule.target, message=msg, pset=pset, prop=prop, actual=actual,
        expected=(f"{rule.condition} {rule.value}".strip()), reference=rule.reference,
    )


# ---------------------------------------------------------------- Phân loại

class _Classifier:
    def __init__(self, mappings):
        self.items = []
        for m in mappings:
            try:
                rx = re.compile(strip_accents(m.keyword), re.IGNORECASE)
            except re.error:
                continue
            self.items.append((rx, split_list(m.expected, allow_comma=True), m))

    def check(self, rec: ElementRecord) -> Issue | None:
        text = rec.search_text()
        if not text:
            return None
        for rx, expected, m in self.items:
            if rx.search(text):
                if any(rec.is_a(c) for c in expected):
                    return None
                return Issue(
                    file=rec.file, guid=rec.guid, step_id=rec.step_id, ifc_class=rec.ifc_class,
                    element_name=rec.display_name(), rule_id="CLS", severity="WARNING",
                    category="CLASSIFY",
                    message=f"Tên gợi ý '{m.note or m.keyword}' -> nên là {expected[0]}, đang là {rec.ifc_class}",
                    actual=rec.ifc_class, expected=" / ".join(expected),
                    reference="Bảng từ khoá phân loại",
                )
        return None


# ---------------------------------------------------------------- Chọn đối tượng

def _mode(options: CheckOptions) -> str:
    if options.skip_parts is not None:
        return "parents" if options.skip_parts else "all"
    return options.part_mode if options.part_mode in ("auto", "parents", "all") else "auto"


def _has_info(rec: ElementRecord) -> bool:
    return any(props for props in rec.psets.values())


def select_records(model: ModelInfo, mode: str = "auto") -> tuple[list[ElementRecord], int]:
    """Chọn đối tượng cần kiểm tra. Trả về (danh sách, số đối tượng bỏ qua)."""
    recs = model.records
    if mode == "all":
        return list(recs), 0
    if mode == "parents":
        out = [r for r in recs if not r.is_part]
        return out, len(recs) - len(out)
    by_guid = {r.guid: r for r in recs if r.guid}
    children: dict[str, list] = {}
    for r in recs:
        if r.is_part and r.parent_guid:
            children.setdefault(r.parent_guid, []).append(r)
    out = []
    for r in recs:
        if r.kind != "element":
            out.append(r)
            continue
        if r.is_part:
            parent = by_guid.get(r.parent_guid)
            # cha là nhóm thuần (không có thuộc tính) -> con là đối tượng thật
            if parent is None or not _has_info(parent):
                out.append(r)
            continue
        kids = children.get(r.guid)
        if kids and not _has_info(r):
            continue  # cha chỉ là nhóm chứa
        out.append(r)
    return out, len(recs) - len(out)


# ---------------------------------------------------------------- Chạy

def run_checks(models: list[ModelInfo], ruleset: RuleSet, options: CheckOptions | None = None,
               progress=None, cancel=None) -> CheckResult:
    options = options or CheckOptions()
    res = CheckResult(started=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ruleset_source=ruleset.source)
    rules = [r for r in ruleset.rules if r.enabled]
    apps = [(r, _Applicability(r)) for r in rules]
    classifier = _Classifier(ruleset.mappings) if options.check_classification else None
    empty = options.empty_tokens

    for mi, model in enumerate(models):
        if model.error and not model.records:
            res.files.append(FileSummary(model.file_name, model.path, model.schema, model.project_name,
                                         model.application, 0, 0, 0, 0, 0, 0, 0, 0, error=model.error))
            continue
        stats = {r.id: RuleStat(model.file_name, r.id, r.summary(), r.severity) for r in rules}
        file_issues: list[Issue] = []
        recs, skipped = select_records(model, _mode(options))
        elements_failed = set()
        checked_elements = [r for r in recs if r.kind == "element"]
        total = len(recs)
        for i, rec in enumerate(recs):
            if cancel and cancel():
                break
            for rule, app in apps:
                if not app.applies(rec):
                    continue
                st = stats[rule.id]
                st.applicable += 1
                iss = evaluate(rule, rec, empty)
                if iss is None:
                    st.passed += 1
                else:
                    iss.file = model.file_name
                    file_issues.append(iss)
                    if iss.severity == "ERROR":
                        elements_failed.add(rec.step_id)
            if classifier and rec.kind == "element":
                iss = classifier.check(rec)
                if iss:
                    iss.file = model.file_name
                    file_issues.append(iss)
            if progress and (i % 300 == 0 or i == total - 1):
                progress(i + 1, total, f"Kiểm tra {model.file_name}: {i + 1}/{total}")

        # Chạy IDS nếu có
        if ruleset.ids_path and model.ifc is not None:
            from .ids_runner import run_ids
            try:
                skip_ids = {r.step_id for r in model.records if r.kind == "element"} - {r.step_id for r in recs}
                ids_issues, ids_stats, failed_ids = run_ids(ruleset.ids_path, model, skip_ids)
                file_issues.extend(ids_issues)
                res.rule_stats.extend(ids_stats)
                elements_failed |= failed_ids
            except Exception as ex:
                file_issues.append(Issue(model.file_name, "", 0, "", "", "IDS", "ERROR", "IDS",
                                         f"Không chạy được IDS: {ex}"))

        c = Counter(i.severity for i in file_issues)
        checked_ids = {r.step_id for r in checked_elements}
        res.files.append(FileSummary(
            file=model.file_name, path=model.path, schema=model.schema, project=model.project_name,
            application=model.application, total_elements=len(model.elements),
            checked=len(checked_elements), parts_skipped=skipped,
            proxies=sum(1 for r in checked_elements if r.ifc_class == "IfcBuildingElementProxy"),
            passed_elements=len(checked_ids - elements_failed),
            errors=c.get("ERROR", 0), warnings=c.get("WARNING", 0), infos=c.get("INFO", 0),
            error="; ".join(x for x in [model.error] + model.warnings if x),
        ))
        res.issues.extend(file_issues)
        res.rule_stats.extend(stats.values())
        res.class_counts.extend(class_counts(model))
        res.inventory.extend(property_inventory(model, recs))
        if progress:
            progress(mi + 1, len(models), f"Xong {model.file_name}")

    res.issues.sort(key=lambda i: (i.file, SEV_ORDER.get(i.severity, 9), i.rule_id, i.ifc_class))
    return res


def class_counts(model: ModelInfo) -> list[tuple]:
    c = Counter()
    parts = Counter()
    for r in model.records:
        if r.kind != "element":
            continue
        key = (r.ifc_class, r.predefined_type)
        if r.is_part:
            parts[key] += 1
        else:
            c[key] += 1
    keys = sorted(set(c) | set(parts), key=lambda k: (-(c[k] + parts[k]), k))
    return [(model.file_name, k[0], k[1], c[k], parts[k]) for k in keys]


def property_inventory(model: ModelInfo, recs: list[ElementRecord]) -> list[tuple]:
    """Thống kê mức độ điền thông tin của từng thuộc tính theo lớp IFC."""
    total_by_class = Counter(r.ifc_class for r in recs if r.kind == "element")
    present = Counter()
    filled = Counter()
    samples: dict[tuple, list] = defaultdict(list)
    for r in recs:
        if r.kind != "element":
            continue
        for pset, props in r.psets.items():
            for k, v in props.items():
                key = (r.ifc_class, pset.strip(), k.strip())
                present[key] += 1
                if not is_empty(v):
                    filled[key] += 1
                    s = value_to_str(v)
                    if len(samples[key]) < 5 and s not in samples[key]:
                        samples[key].append(s[:60])
    rows = []
    for key in sorted(present):
        cls, pset, prop = key
        rows.append((model.file_name, cls, pset, prop, present[key], filled[key], total_by_class[cls],
                     " | ".join(samples[key])))
    return rows
