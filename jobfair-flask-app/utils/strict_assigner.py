import math
import itertools


def assign_one_student(student_id, preferences, company_capacity, valid_companies, num_slots, initial_max_slots):
    prefs = preferences[preferences["student_id"] == student_id].sort_values(by="rank")["company_name"].tolist()

    
    
    for max_slots in range(initial_max_slots, 0, -1):
        ranked_subset = prefs[:max_slots]      # 高順位だけに限定
        
        # ---- スロット混雑度を計算（残キャパ合計が少ない方が混雑） ----
        slot_load = [
            sum(cap[slot] for cap in company_capacity.values())
            for slot in range(num_slots)
        ]
        starts = sorted(                     # ← 空いている窓を優先
            range(num_slots - max_slots + 1),
            key=lambda s: sum(slot_load[s:s+max_slots])
        )
        for start_slot in starts:
            slots_to_try = list(range(start_slot, start_slot + max_slots))

            for companies in itertools.permutations(ranked_subset, max_slots):
                if len(set(companies)) < len(companies):
                    continue

                if all(company_capacity[company][slot] > 0 for company, slot in zip(companies, slots_to_try)):
                    for company, slot in zip(companies, slots_to_try):
                        company_capacity[company][slot] -= 1
                    return slots_to_try, companies

    return None

def run_strict_scheduler(df_preference, df_company, student_ids, dept_id, cap, num_slots=4):
    valid_companies = df_company[df_company["department_id"] == dept_id]["company_name"].tolist()
    company_capacity = { cname: [cap] * num_slots for cname in valid_companies }
    total_capacity = len(valid_companies) * cap * num_slots
    initial_max_slots = min(4, math.floor(total_capacity / len(student_ids)))

    student_schedule = {}
    unassigned_students = []

    for sid in student_ids:
        result = assign_one_student(
            sid, df_preference, company_capacity, valid_companies, num_slots, initial_max_slots
        )

        if result:
            slots, companies = result
            schedule = [None] * num_slots
            for s, c in zip(slots, companies):
                schedule[s] = c
            student_schedule[sid] = schedule
        else:
            student_schedule[sid] = [None] * num_slots
            unassigned_students.append(sid)

    print(f"✅ 完全空きコマゼロ割当完了 → 要手動救済: {len(unassigned_students)}人")
    return student_schedule, company_capacity, unassigned_students

def calc_score_from_assignment(student_schedule, df_preference):
    """
    各学生のスコアを計算する（割当企業と希望順位を照合）
    第1希望5点、第2希望4点、第3希望3点、第4希望2点、それ以外は0点など
    """
    student_score = {}
    pref_dict = df_preference.groupby("student_id").apply(
        lambda df: {row["company_name"]: row["rank"] for _, row in df.iterrows()}
    ).to_dict()

    for sid, slots in student_schedule.items():
        prefs = pref_dict.get(sid, {})
        score = 0
        for company in slots:
            if not company or company == "自由訪問枠":
                continue
            rank = prefs.get(company)
            if rank == 1:
                score += 3
            elif rank == 2:
                score += 2
            elif rank == 3:
                score += 1
            elif rank == 4:
                score += 1
            # else: 0点
        student_score[sid] = score
    return student_score

