from ortools.sat.python import cp_model
import math
import pandas as pd
from typing import Dict, List, Tuple


# ---------------------------------------------------------


# # ----
# git merge pr-1-----------------------------------------------------


# ---------------------------------------------------------


"""
CP‑SAT 版の“厳密割当”
----------------------------------
パターンB（学生数 > キャパ）で使用する CP-SAT 実装。

**現在の実装範囲**
  * 1スロット1社
  * 同一企業の重複禁止
  * 企業キャパ制約
  * 学生ごとの max_slots 制約
  * 学生の連続枠（飛びコマ禁止）
  * 企業側 0人ブース禁止（ハード）
  * 学生希望スコア最大化（第1〜第4希望を 5,4,3,2 点）

**未実装 / TODO**
  * 希望外を避けるペナルティ／公平性指標
  * Lexicographic 最適化 など上級設定

まず解を返すことを優先したミニマム構成。
"""


def _build_score_map(df_preference: pd.DataFrame) -> Dict[str, Dict[str, int]]:
    """score[sid][company] → 点数"""
    rank_points = {1: 5, 2: 4, 3: 3, 4: 2}
    score: Dict[str, Dict[str, int]] = {}
    for _, row in df_preference.iterrows():
        sid = row["student_id"]
        company = row["company_name"]
        rank = int(row["rank"])
        score.setdefault(sid, {})[company] = rank_points.get(rank, 0)
    return score


def run_strict_scheduler_cp(
    df_preference: pd.DataFrame,
    df_company: pd.DataFrame,
    student_ids: List[str],
    dept_id: str,
    cap: int,
    num_slots: int = 3,
    time_limit_sec: int = 30,
    max_slots: int | None = None,
):
    """CP‑SAT による割当

    戻り値:
        schedule: Dict[str, List[str]]  # sid -> [slot0, slot1, slot2]
        company_capacity: Dict[str, List[int]]  # 更新後のキャパ残
        unsat_students: List[str]  # 未割当 (max_slots=0) の学生ID
    """

    # ---------- データ整形 ----------
    S = list(student_ids)
    T = list(range(num_slots))
    C = df_company[df_company["department_id"] == dept_id]["company_name"].tolist()

    if not (S and C):
        raise ValueError("学生または企業が存在しません")

    # cap[c][t] を dict で作成
    company_capacity: Dict[str, List[int]] = {c: [cap] * num_slots for c in C}

    if max_slots is None:
        total_capacity = len(C) * cap * num_slots
        max_slots = min(num_slots, total_capacity // len(S))

    # ---------- CP‑SAT モデル ----------
    model = cp_model.CpModel()

    # --- 変数 x[s,t,c] ---
    x = {}
    for s in S:
        for t in T:
            for c in C:
                x[s, t, c] = model.NewBoolVar(f"x_{s}_{t}_{c}")

    # ---------- 制約 ----------

    # 1) 1スロット1社 (学生側)
    for s in S:
        for t in T:
            model.Add(sum(x[s, t, c] for c in C) <= 1)

    # 2) 同一企業重複禁止 (学生側)
    for s in S:
        for c in C:
            model.Add(sum(x[s, t, c] for t in T) <= 1)

    # 3) 企業キャパ
    for c in C:
        for t in T:
            model.Add(sum(x[s, t, c] for s in S) <= company_capacity[c][t])

    # 学生0訪問禁止
    for s in S:
        model.Add(sum(x[s, t, c] for t in T for c in C) >= 1)  # 全員 1 コマ以上

    # 4) 学生 max_slots
    for s in S:
        model.Add(sum(x[s, t, c] for t in T for c in C) <= max_slots)

    # === ★★ 追加：y と k の定義 ========================
    y = {(s, t): model.NewBoolVar(f"y_{s}_{t}") for s in S for t in T}
    k = {s: model.NewIntVar(0, num_slots, f"k_{s}") for s in S}
    for s in S:
        for t in T:
            model.Add(sum(x[s, t, c] for c in C) == y[s, t])
        model.Add(k[s] == sum(y[s, t] for t in T))

    # --- ① 連続枠（飛びコマ禁止） --------------------------
    for s in S:
        for t in range(num_slots - 2):  # num_slots=3 なら t=0 だけ
            # y[s,t] と y[s,t+2] が両方 1 なら 真ん中 y[s,t+1] も 1 にする
            model.Add(y[s, t] + y[s, t + 2] - y[s, t + 1] <= 1)
    # ------------------------------------------------------

    # --- ② 企業側 0人ブース禁止（ハード） -------------------
    for c in C:
        for t in T:
            if company_capacity[c][t] > 0:  # スロット営業している企業だけ
                model.Add(sum(x[s, t, c] for s in S) >= 1)

    # ---------- 目的関数設定 (2 段階最適化) ----------
    score_map = _build_score_map(df_preference)
    objective_terms = []
    for s in S:
        for t in T:
            for c in C:
                points = score_map.get(s, {}).get(c, 0)
                if points:
                    objective_terms.append(points * x[s, t, c])

    # --- Phase 1 : 割当コマ数の合計を最大化 ---
    model.Maximize(sum(k[s] for s in S))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("[CP‑SAT] FEASIBLE 解なし。status =", cp_model.CpSolver().StatusName(status))
        schedule = {s: [None] * num_slots for s in S}
        unsat_students = list(S)
        return schedule, company_capacity, unsat_students

    best_total = sum(solver.Value(k[s]) for s in S)

    # --- Phase 2 : 上記コマ数を固定して希望スコア最大化 ---
    model.ClearObjective()
    model.Add(sum(k[s] for s in S) == best_total)
    model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)

    # ---------- 結果取り出し ----------
    schedule: Dict[str, List[str]] = {s: [None] * num_slots for s in S}

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for s in S:
            for t in T:
                for c in C:
                    if solver.Value(x[s, t, c]):
                        schedule[s][t] = c
                        company_capacity[c][t] -= 1
        unsat_students = [s for s, slots in schedule.items() if all(v is None for v in slots)]
        return schedule, company_capacity, unsat_students

    else:
        print("[CP‑SAT] FEASIBLE 解なし。status =", cp_model.CpSolver().StatusName(status))
        # 全 None で返す
        schedule = {s: [None] * num_slots for s in S}
        unsat_students = list(S)
        return schedule, company_capacity, unsat_students
