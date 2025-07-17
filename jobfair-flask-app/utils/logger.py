import math

def is_real_company(val):
    """None / NaN / '自由訪問枠' を除いた企業名のみ対象"""
    return isinstance(val, str) and val != "自由訪問枠"

def check_schedule_violations(student_schedule, company_capacity=None):
    logs = []

    for sid, slots in student_schedule.items():
        # --- 重複企業チェック ---
        assigned = [v for v in slots if is_real_company(v)]
        duplicates = [v for v in set(assigned) if assigned.count(v) > 1]
        if duplicates:
            logs.append(f"⚠️ {sid}: 同一企業が複数スロットに割り当てられています → {assigned}")

        # --- 希望枠数の自動判定（3 or 4枠） ---
        slot_len = len(slots)
        is_mode3 = (slot_len == 4 and slots[3] == "自由訪問枠") or slot_len == 3
        required_slots = 3 if is_mode3 else 4

        # --- 希望枠に未割当があるかチェック ---
        has_empty = any(
            slots[i] is None or (isinstance(slots[i], float) and math.isnan(slots[i]))
            for i in range(required_slots)
        )
        if has_empty:
            logs.append(f"⚠️ {sid}: 希望枠（{required_slots}枠）がすべて埋まっていません → {slots}")

    return logs

# --- 会社側：空きスロット検出 ---------------------------------
def find_company_zero_slots(student_schedule, valid_companies, num_slots=4):
    """企業×時間帯で割当ゼロのスロットを返す [(company, slot), ...]"""
    count = {(c, s): 0 for c in valid_companies for s in range(num_slots)}
    for slots in student_schedule.values():
        for s, c in enumerate(slots):
            if c and c in valid_companies:
                count[(c, s)] += 1
    return [k for k, v in count.items() if v == 0]

# --- 会社側：割当数サマリ ---------------------------------------
def summarize_company_assignments(student_schedule, valid_companies, num_slots=4):
    """各企業・各スロットの割当人数を返す dict[c] -> [slot0, slot1, ...]"""
    summary = {c: [0] * num_slots for c in valid_companies}
    for slots in student_schedule.values():
        for s, c in enumerate(slots):
            if c and c in summary:
                summary[c][s] += 1
    return summary

# --- 学生側：0訪問検出 -----------------------------------------
def find_zero_visit_students(student_schedule):
    """全コマ None の学生IDを返す [sid, ...]"""
    return [sid for sid, slots in student_schedule.items() if all(v is None for v in slots)]

# --- 学生側：不足コマ検出（パターンB用） -------------------------
def find_underfilled_students(student_schedule, max_slots):
    """max_slots 未満しか割り当てられていない学生IDを返す [sid, ...]"""
    return [sid for sid, slots in student_schedule.items()
            if sum(v is not None for v in slots) < max_slots]

# --- 学生側：連続でない割当（“飛びコマ”）検出 -----------------
def find_discontinuous_students(student_schedule):
    """
    連続していないコマが含まれる学生IDを返す [sid, ...]
    例) max_slots=2 なら 1–3, 1–4, 2–4 などが NG
    """
    bad = []
    for sid, slots in student_schedule.items():
        filled_idx = [i for i, v in enumerate(slots) if v is not None]
        if len(filled_idx) <= 1:          # 0 コマ or 1 コマなら問題なし
            continue
        # 連続しているなら (max−min+1) == コマ数 になる
        if max(filled_idx) - min(filled_idx) + 1 != len(filled_idx):
            bad.append(sid)
    return bad