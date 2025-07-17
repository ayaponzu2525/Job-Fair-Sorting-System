import random
import re
from typing import Dict, List


def _norm_company(name: str) -> str:
    """Normalize company name for grouping"""
    if name is None:
        return ""
    name = str(name).strip()
    # remove any department style suffix
    name = re.sub(r"(科|学科).*", "", name)
    return name


def build_company_slot_map(student_schedule: Dict[str, List[str]],
                           num_slots: int) -> Dict[str, Dict[int, List[str]]]:
    """Return assignments grouped by company->slot->student list"""
    comp_map: Dict[str, Dict[int, List[str]]] = {}
    for sid, slots in student_schedule.items():
        for slot, cname in enumerate(slots):
            if not cname or cname == "自由訪問枠":
                continue
            cname_norm = _norm_company(cname)
            comp_map.setdefault(cname_norm, {}).setdefault(slot, []).append(sid)
    return comp_map


def adjust_overflow_assignments(student_schedule: Dict[str, List[str]],
                                student_score: Dict[str, int],
                                student_dept_map: Dict[str, str],
                                df_company,
                                cap: int,
                                num_slots: int,
                                pattern_by_dept: Dict[str, str]):
    """Resolve slot overflow across departments."""
    unique_companies = [_norm_company(c) for c in df_company["company_name"].unique()]
    capacity_map = {c: [cap] * num_slots for c in unique_companies}

    comp_map = build_company_slot_map(student_schedule, num_slots)

    drops: List[tuple[str, int]] = []  # (sid, slot)
    for cname, slot_dict in comp_map.items():
        for slot, sids in slot_dict.items():
            cap_left = capacity_map[cname][slot]
            if len(sids) <= cap_left:
                continue
            overflow = len(sids) - cap_left
            dept_counts: Dict[str, int] = {}
            for sid in sids:
                dept = student_dept_map.get(sid, "")
                dept_counts[dept] = dept_counts.get(dept, 0) + 1
            candidates = sids.copy()
            selected: List[str] = []
            while overflow > 0 and candidates:
                prefer = [sid for sid in candidates if dept_counts[student_dept_map.get(sid, "")] > 1]
                pool = prefer if prefer else candidates
                max_score = max(student_score.get(sid, 0) for sid in pool)
                top = [sid for sid in pool if student_score.get(sid, 0) == max_score]
                sid = random.choice(top)
                selected.append(sid)
                candidates.remove(sid)
                dept = student_dept_map.get(sid, "")
                dept_counts[dept] -= 1
                overflow -= 1
            for sid in selected:
                student_schedule[sid][slot] = None
                drops.append((sid, slot))

    if not drops:
        return

    companies_by_dept: Dict[str, List[str]] = {}
    for _, row in df_company.iterrows():
        dept = row["department_id"]
        companies_by_dept.setdefault(dept, []).append(row["company_name"])

    comp_map = build_company_slot_map(student_schedule, num_slots)

    for sid, dropped_slot in drops:
        dept = student_dept_map.get(sid, "")
        pattern = pattern_by_dept.get(dept, "A")
        candidates = companies_by_dept.get(dept, [])
        random.shuffle(candidates)
        assigned = False
        if pattern == "A":
            for t in range(num_slots):
                if student_schedule[sid][t] is not None:
                    continue
                for cname in candidates:
                    cname_norm = _norm_company(cname)
                    if len(comp_map.get(cname_norm, {}).get(t, [])) >= capacity_map[cname_norm][t]:
                        continue
                    if cname in student_schedule[sid]:
                        continue
                    student_schedule[sid][t] = cname
                    comp_map.setdefault(cname_norm, {}).setdefault(t, []).append(sid)
                    assigned = True
                    break
                if assigned:
                    break
        else:  # Pattern B
            filled = [i for i, v in enumerate(student_schedule[sid]) if v is not None]
            for t in range(num_slots):
                if student_schedule[sid][t] is not None:
                    continue
                idx = filled + [t]
                if idx and max(idx) - min(idx) + 1 != len(idx):
                    continue
                for cname in candidates:
                    cname_norm = _norm_company(cname)
                    if len(comp_map.get(cname_norm, {}).get(t, [])) >= capacity_map[cname_norm][t]:
                        continue
                    if cname in student_schedule[sid]:
                        continue
                    student_schedule[sid][t] = cname
                    comp_map.setdefault(cname_norm, {}).setdefault(t, []).append(sid)
                    assigned = True
                    break
                if assigned:
                    break
        if not assigned:
            print(f"[ADJUST] {sid} remains unassigned after overflow fix")
