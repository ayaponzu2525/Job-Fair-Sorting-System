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

def redistribute_zero_slots_B(student_schedule, company_capacity,
                              df_preference,     # 希望順位→点数用
                              valid_companies, max_slots, num_slots=3):
    """
    0人ブースを埋めながら、max_slots 未満の学生を優先的に充足。
    ・学生側は「連続枠」制約を死守（飛びコマ禁止）
    ・同一企業の重複禁止
    戻り値:  (補完した件数, 残った0人ブース list)
    """
    from utils.logger import find_company_zero_slots, find_underfilled_students
    filled_total = 0

    # 希望辞書 (sid -> {company: rank})
    pref_dict = (df_preference.groupby("student_id")
                              .apply(lambda df: {r["company_name"]: r["rank"] for _, r in df.iterrows()})
                              .to_dict())

    MAX_ITER = 1000           # 充分に大きい値
    loop_cnt = 0

    while True:
        zero_slots = find_company_zero_slots(student_schedule, valid_companies, num_slots)
        underfilled = find_underfilled_students(student_schedule, max_slots)
        if not zero_slots or not underfilled:
            break

        progress = 0
        # --- 「埋まり具合が少ない学生」→「sid昇順」の安定ソート ---
        underfilled.sort(key=lambda sid: (
            sum(v is not None for v in student_schedule[sid]), sid))

        for cname, slot in zero_slots:
            for sid in underfilled:
                slots = student_schedule[sid]
                # すでにそのコマに何かある / 同一企業重複は不可
                if slots[slot] is not None or cname in slots:
                    continue

                # ★ 連続枠になるか判定
                idx = [i for i,v in enumerate(slots) if v is not None] + [slot]
                if max(idx) - min(idx) + 1 != len(idx):
                    continue

                # 割当可能
                student_schedule[sid][slot] = cname
                company_capacity[cname][slot] -= 1
                filled_total += 1
                progress += 1

                # スコア即時加点（希望順位で決める）
                rank = pref_dict.get(sid, {}).get(cname)
                if   rank == 1: delta = 3
                elif rank == 2: delta = 2
                elif rank in (3,4): delta = 1
                else:           delta = 0
                # 後でまとめて再計算するならここはスキップしても OK
                # student_score[sid] += delta

                # max_slots 達したら underfilled から除外
                if sum(v is not None for v in slots) >= num_slots:
                    underfilled.remove(sid)
                break   # 次の zero-slot へ

        if progress == 0:      # これ以上は埋まらない
            break
        
        loop_cnt += 1
        if loop_cnt > MAX_ITER:
            print("⚠️ redistribute_zero_slots_B: 安全弁で強制終了")
            break

    # 残った 0 人ブースを返す
    remaining = find_company_zero_slots(student_schedule, valid_companies, num_slots)
    return filled_total, remaining


def assign_zero_slots_hiScore_B(student_schedule, student_score,
                                company_capacity, valid_companies,
                                df_preference, num_slots=3):
    """残った 0 人ブースをスコア高い学生で“置換あり”補完。
       ・連続枠を壊さない
       ・置換で新たな 0 人ブースを生まない
       ・capacity ±1 を厳密更新
       返り値: (補完数, 最終的に残った0人ブースlist)
    """
    from utils.logger import find_company_zero_slots
    from utils.logger import find_discontinuous_students
    pref_dict = (df_preference.groupby("student_id")
                 .apply(lambda df: {r["company_name"]: r["rank"] for _, r in df.iterrows()})
                 .to_dict())

    total_filled = 0
    while True:
        zero_slots = find_company_zero_slots(student_schedule, valid_companies, num_slots)
        if not zero_slots:
            break

        progress = 0
        for cname, slot in zero_slots:
            # スコア降順
            for sid, _ in sorted(student_score.items(), key=lambda x: -x[1]):
                slots = student_schedule[sid]
                # --- ① 空きがあればそのまま割当 -----------------
                if slots[slot] is None and cname not in slots:
                    # 連続枠になるか
                    idx = [i for i,v in enumerate(slots) if v is not None] + [slot]
                    if max(idx)-min(idx)+1 != len(idx):
                        continue
                    # 割当
                    student_schedule[sid][slot] = cname
                    company_capacity[cname][slot] -= 1
                    progress += 1; total_filled += 1
                # --- ② 全枠埋まり → 置換 -----------------------
                elif all(v is not None for v in slots) and cname not in slots:
                    # 置換対象＝希望外(99) → 低順位
                    ranks = [pref_dict.get(sid, {}).get(c, 99) if c else 99 for c in slots]
                    idx_replace = ranks.index(max(ranks))
                    old_c = slots[idx_replace]

                    # old_c の残人数チェック
                    if old_c and old_c in valid_companies:
                        others = sum(old_c in sc for s2,sc in student_schedule.items() if s2!=sid)
                        if others == 0:            # 0人ブース化するならNG
                            continue

                    # 連続枠が維持できるか
                    test = slots.copy()
                    test[idx_replace] = None
                    test[slot] = cname
                    idx = [i for i,v in enumerate(test) if v is not None]
                    if max(idx)-min(idx)+1 != len(idx):
                        continue

                    # capacity 戻す / 減らす
                    if old_c and old_c in valid_companies:
                        company_capacity[old_c][idx_replace] += 1
                    student_schedule[sid][idx_replace] = None
                    student_schedule[sid][slot] = cname
                    company_capacity[cname][slot] -= 1
                    progress += 1; total_filled += 1

                if progress:  # スコア再計算
                    score = 0
                    for c in student_schedule[sid]:
                        r = pref_dict.get(sid, {}).get(c)
                        score += (5-r) if r and 1<=r<=4 else 0
                    student_score[sid] = score
                    break        # 次の zero_slot へ

        if progress == 0:
            break               # これ以上動かせない

    remaining = find_company_zero_slots(student_schedule, valid_companies, num_slots)
    return total_filled, remaining

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

