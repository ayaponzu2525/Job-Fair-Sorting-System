# routes/views.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

import pandas as pd
import os
from werkzeug.utils import secure_filename
from utils.data_loader import load_students, load_companies
from utils.assigner import assign_preferences, fill_with_industry_match, fill_zero_slots, run_pattern_a, rescue_zero_visits, assign_zero_slots_by_score_with_replace_safe_loop
from utils.strict_assigner import run_strict_scheduler, calc_score_from_assignment
import random
from flask import send_file
from utils.diagnoser import build_diagnosis
from pathlib import Path
from pandas.errors import EmptyDataError


views = Blueprint("views", __name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)   # ⑥
UPLOAD_PATH = Path(UPLOAD_FOLDER) / "students.csv"
COMPANY_PATH = Path(UPLOAD_FOLDER) / "companies.csv"
ALLOWED_EXTENSIONS = {"csv"}
STUDENTS_PATH  = Path("uploads/students.csv")
COMPANIES_PATH = Path("uploads/companies.csv")

NUM_SLOTS =4

@views.route("/admin/run", methods=["POST"])
def run_assignment():
    
    path_students  = STUDENTS_PATH  if STUDENTS_PATH.exists()  else "data/students.csv"
    path_companies = COMPANIES_PATH if COMPANIES_PATH.exists() else "data/companies.csv"
    
    df_preference, mode, student_dept_map = load_students(path_students)
    df_company = load_companies(path_companies)
    session["mode"] = mode
    NUM_SLOTS = mode
    student_ids = df_preference["student_id"].unique()
    session["shared_capacity"] = int(request.form.get("shared_capacity", 10))
    cap = session["shared_capacity"]

    # 全体の結果用辞書
    student_schedule = {}
    student_score = {}
    student_assigned_companies = {}
    all_reason_logs = []
    filled_step4_total = 0
    filled_step5_total = 0
    # 各学科ごとのログ用辞書
    dept_log_summary = {}  # {dept: {"step4": X, "step5": Y}}
    cross_total = 0


    # 学科ごとにグループ化
    from collections import defaultdict
    dept_to_students = defaultdict(list)
    for sid in student_ids:
        dept_to_students[ student_dept_map[sid] ].append(sid)
        
    
    # 学科ループに入る前に diagnosis.csv をリセット
    if os.path.exists("diagnosis.csv"):
        os.remove("diagnosis.csv")


    # 各学科ごとに処理
    for dept, sids in dept_to_students.items():
        # ① 学科対応企業だけを抽出
        df_dept_company = df_company[df_company["department_id"] == dept]
        company_count   = df_dept_company["company_name"].nunique()   # ← 重複行は1社扱い

        # ② 学科の学生希望を抽出（学科対応企業のみ）
        valid_companies = df_dept_company["company_name"].tolist()
        df_dept_pref    = df_preference[
            (df_preference["student_id"].isin(sids)) &
            (df_preference["company_name"].isin(valid_companies))
        ]

        # ③ キャパと需要
        total_capacity = cap  * company_count
        max_demand     = len(sids)

        # ④ 判定
        pattern = "A" if total_capacity > max_demand else "B"

        # ⑤ デバッグ出力
        print(f"[DEBUG] {dept: <15} 企業数={company_count:2d}  学生数={len(sids):3d}  "
            f"総キャパ={total_capacity}  需要={max_demand}  → パターン{pattern}")
                # 余裕ゼロ or 足りない

            

        if pattern == "A":
            print(f"=================================[{dept}] パターン A で割当実行=============================================")
            schedule, score, assigned, capacity, filled4, filled5, reasons = run_pattern_a(
                df_dept_pref, df_dept_company, sids, dept, student_dept_map, cap, NUM_SLOTS
            )
            filled_step4_total += filled4
            
            filled_step5_total += filled5
            


            # マージ
            student_schedule.update(schedule)
            student_score.update(score)
            student_assigned_companies.update(assigned)
            all_reason_logs.append(reasons)
            
            # 0人ブース補完
            filled_zero_slots, remaining_zero_slots = assign_zero_slots_by_score_with_replace_safe_loop(
                student_schedule, student_score, df_preference,
                capacity, valid_companies, NUM_SLOTS
            )
            if remaining_zero_slots:
                # 画面やログに警告を出す
                print("以下の企業・スロットはどうしても0人です：", remaining_zero_slots)

            
            matched_cnt = sum(
                1 for sid in sids
                if any(
                    c in df_dept_pref[df_dept_pref.student_id == sid].company_name.values
                    for c in schedule[sid]
                )
            )
            print(f"[{dept}] 希望一致学生数 = {matched_cnt} / {len(sids)}")
            # -----------------------------------------------
            dept_log_summary[dept] = {"step4": filled4, "step5": filled5}
            
            
            df_orig_pref_dept = df_preference[   # ← 学科で絞るだけ
                df_preference["student_id"].isin(sids)
            ]
            
            df_diag_dept, cross_pref_list, cross_assign_list = build_diagnosis(
                df_orig_pref_dept,   # ← フィルタしない元の希望 DF
                schedule,
                df_dept_company,      # 割当学科の企業 DF
                student_dept_map
            )


            # ログ出力や集計
            if cross_pref_list:
                print(f"⚠️ {dept}: [A]学科外を希望した件数 = {len(cross_pref_list)}")
            if cross_assign_list:
                print(f"❌ {dept}: [A]学科外割当 {cross_assign_list[:10]} ...")
            else:
                print(f"✅ {dept}: [A]学科外割当なし")

            cross_pref_sids = sorted(set(sid for sid, _ in cross_pref_list))
            print("cross_pref（学科外希望）の学生学籍番号:", cross_pref_sids)



            # ---- 集計 ----
            cross_pref_cnt   = len(cross_pref_list)
            cross_assign_cnt = len(cross_assign_list)

            dept_log_summary[dept].update({
                "step4"        : filled4,
                "step5"        : filled5,
                "cross_pref"   : cross_pref_cnt,
                "cross_assign" : cross_assign_cnt,
            })

            cross_total += cross_assign_cnt
            df_diag_dept.to_csv(
                "diagnosis.csv",
                mode="a",            # 追記
                index=False,
                header=not os.path.exists("diagnosis.csv")  # 最初の学科だけヘッダ
            )
            
            from utils.logger import (
                find_company_zero_slots, find_zero_visit_students, find_underfilled_students
            )

            # --- 会社側 0人スロット ------------------------------
            zero_slots = find_company_zero_slots(schedule, valid_companies, NUM_SLOTS)
            if zero_slots:
                print(f"❗ {dept}: 企業側 0人スロット {len(zero_slots)} 件 → {zero_slots[:10]}")
            else:
                print(f"✅ {dept}: 企業側 0人スロットなし")

            # --- 学生側 0訪問 -------------------------------
            zero_visit = find_zero_visit_students(schedule)
            if zero_visit:
                print(f"❗ {dept}: 0訪問学生 {len(zero_visit)} 人 → {zero_visit[:10]}")
            else:
                print(f"✅ {dept}: 0訪問学生なし")
            
            print(f"[DEBUG] cross_pref={cross_pref_cnt}, cross_assign={cross_assign_cnt}")

        if pattern == "B":
            print(f"=================================[{dept}] パターン B で割当実行=============================================")
            import random
            random.shuffle(sids)   # ← 早い者勝ち防止（方法 A）
            schedule, capacity, unassigned = run_strict_scheduler(
                df_dept_pref, df_dept_company, sids, dept, cap, NUM_SLOTS
            )
            
            rescue_zero_visits(schedule, capacity, valid_companies, NUM_SLOTS)

            # スコア計算
            student_score.update(calc_score_from_assignment(schedule, df_preference))
            
            # 統一の出力形式に合わせて辞書更新
            student_schedule.update(schedule)
            student_assigned_companies.update({
                sid: set(c for c in schedule[sid] if c not in [None, ""])
                for sid in sids
            })

            
            filled4, filled5, reasons = 0, 0, {}
            filled_step4_total += filled4
            filled_step5_total += filled5
            all_reason_logs.append(reasons)

            print(f"[{dept}] (strictB) 割当完了（未割当 {len(unassigned)}人）")

            matched_cnt = sum(
                1 for sid in sids
                if any(
                    c in df_dept_pref[df_dept_pref.student_id == sid].company_name.values
                    for c in schedule[sid]
                )
            )
            print(f"[{dept}] (B) 希望一致学生数 = {matched_cnt} / {len(sids)}")

            dept_log_summary[dept] = {"step4": filled4, "step5": filled5}

            df_orig_pref_dept = df_preference[df_preference["student_id"].isin(sids)]
            df_diag_dept, cross_pref_list, cross_assign_list = build_diagnosis(
                df_orig_pref_dept,
                schedule,
                df_dept_company,
                student_dept_map
            )

            cross_pref_cnt   = len(cross_pref_list)
            cross_assign_cnt = len(cross_assign_list)
            dept_log_summary[dept].update({
                "step4"        : filled4,
                "step5"        : filled5,
                "cross_pref"   : cross_pref_cnt,
                "cross_assign" : cross_assign_cnt,
            })

            cross_total += cross_assign_cnt
            df_diag_dept.to_csv(
                "diagnosis.csv",
                mode="a",
                index=False,
                header=not os.path.exists("diagnosis.csv")
            )
            
            from utils.logger import (
                find_company_zero_slots, find_zero_visit_students, find_underfilled_students
            )

            # --- 会社側 0人スロット ------------------------------
            zero_slots = find_company_zero_slots(schedule, valid_companies, NUM_SLOTS)
            if zero_slots:
                print(f"❗ {dept}: 企業側 0人スロット {len(zero_slots)} 件 → {zero_slots[:10]}")
            else:
                print(f"✅ {dept}: 企業側 0人スロットなし")

            # --- 学生側 0訪問 -------------------------------
            zero_visit = find_zero_visit_students(schedule)
            if zero_visit:
                print(f"❗ {dept}: 0訪問学生 {len(zero_visit)} 人 → {zero_visit[:10]}")
            else:
                print(f"✅ {dept}: 0訪問学生なし")

            # --- 学生側 max_slots 未満（パターンBのみ） ----------
            if pattern == "B":
                # strict_assigner と同じ式で再計算
                import math
                max_slots = min(4, math.floor(len(valid_companies) * cap * NUM_SLOTS / len(sids)))
                underfill = find_underfilled_students(schedule, max_slots)
                if underfill:
                    print(f"⚠️ {dept}: max_slots 未満 {len(underfill)} 人 → {underfill[:10]}")
                else:
                    print(f"✅ {dept}: 全員 max_slots（{max_slots} 枠）充足")

            
            from utils.logger import find_discontinuous_students

            disc = find_discontinuous_students(schedule)
            if disc:
                print(f"❌ {dept}: 飛びコマ学生 {len(disc)} 人 → {disc[:10]}")
            else:
                print(f"✅ {dept}: 連続枠制約 OK")

            # ログ出力や集計 (B版)
            if cross_pref_list:
                print(f"⚠️ {dept}: [B]学科外を希望した件数 = {len(cross_pref_list)}")
            if cross_assign_list:
                print(f"❌ {dept}: [B]学科外割当 {cross_assign_list[:10]} ...")
            else:
                print(f"✅ {dept}: [B]学科外割当なし")


            
        
    total_cross_assign = sum(dept_log_summary[d].get("cross_assign", 0)
                          for d in dept_log_summary)
    
    print(f"\n=== 全学科 cross_pref 合計: "
       f"{sum(d.get('cross_pref', 0) for d in dept_log_summary.values())} 件 ===")
    print(f"=== 全学科 cross_assign 合計: {total_cross_assign} 件 ===")



    # --- CSV出力 ---
    output_df = pd.DataFrame.from_dict(
        student_schedule, orient="index",
        columns=[f"slot_{i}" for i in range(NUM_SLOTS)]
    )
    output_df.reset_index(names="student_id", inplace=True)
    output_df["dept"] = output_df["student_id"].map(student_dept_map)
    output_df["score"] = output_df["student_id"].map(lambda sid: student_score.get(sid, 0))
    output_df.to_csv("schedule.csv", index=False)
    flash("割当を実行し、schedule.csvを更新しました。")

    # --- logs.txt 出力 ---
    with open("logs.txt", "w", encoding="utf-8") as logf:
        logf.write(f"STEP 4: 学科マッチ補完数（合計） = {filled_step4_total}\n")
        logf.write(f"STEP 5: 0人スロット補完数（合計） = {filled_step5_total}\n")
        logf.write("\n--- 学科別 補完内訳 ---\n")
        for dept, counts in dept_log_summary.items():
            logf.write(f"学科 {dept} → STEP4: {counts['step4']}件, STEP5: {counts['step5']}件\n")

        logf.write("\n--- 補完理由一覧 ---\n")
        for reason_logs in all_reason_logs:
            for sid, slot_reason in reason_logs.items():
                for slot, reason in slot_reason.items():
                    logf.write(f"{sid} の slot_{slot}：{reason}\n")

        logf.write("\n--- 学科外希望／割当件数 ---\n")
        for dept, counts in dept_log_summary.items():
            logf.write(
                f"学科 {dept} → cross_pref = {counts.get('cross_pref',0)} 件, "
                f"cross_assign = {counts.get('cross_assign',0)} 件\n"
            )
        logf.write(f"\n全学科合計 cross_assign = {cross_total} 件\n")

                    

    return redirect(url_for("views.admin"))



def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def load_schedule():
    return pd.read_csv("schedule.csv")

@views.route("/admin/download")
def download_schedule():
    path = "schedule.csv"
    if os.path.exists(path):
        return send_file(path, as_attachment=True)
    flash("スケジュールファイルが存在しません。")
    return redirect(url_for("views.admin"))

@views.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        student_id = request.form["student_id"]
        df = load_schedule()
        row = df[df["student_id"] == student_id]
        return render_template("result.html", row=row)
    return render_template("index.html")

@views.route("/admin")
def admin():
    # CSV が無くても落ちない safe loader
    from utils.data_loader import load_companies
    path_companies = (
        COMPANIES_PATH if COMPANIES_PATH.exists() else "data/companies.csv"
    )
    df_company = load_companies(path_companies)   # 空でも DataFrame が返る
    companies = df_company["company_name"].tolist()  # 使わなくても OK

    current_mode = session.get("mode", 1)
    shared_capacity = session.get("shared_capacity", 10)

    
    path_schedule = "schedule.csv"
    if os.path.exists(path_schedule) and os.path.getsize(path_schedule) > 0:
        try:
            df = pd.read_csv(path_schedule)
            table_html = df.to_html(classes="table table-bordered", index=False)
        except EmptyDataError:
            table_html = "<p>まだ割当が実行されていません</p>"
    else:
        table_html = "<p>まだ割当が実行されていません</p>"
 
    return render_template(
         "admin.html",
         table=table_html,
         current_mode=current_mode,
         shared_capacity=shared_capacity,
     )



@views.route("/admin/upload", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        students_file = request.files.get("students")
        companies_file = request.files.get("companies")

        # 保存先パス
        students_path = UPLOAD_PATH
        companies_path = COMPANY_PATH
        # エラーチェック
        if not students_file or not allowed_file(students_file.filename):
            flash("⚠️ 学生CSVファイルが正しく選択されていません")
            return redirect(request.url)

        if not companies_file or not allowed_file(companies_file.filename):
            flash("⚠️ 企業CSVファイルが正しく選択されていません")
            return redirect(request.url)

        # ファイル保存
        students_file.save(students_path)
        companies_file.save(companies_path)

        # クレンジング後の件数確認
        try:
            df_students, mode, _ = load_students(students_path)
            df_companies = load_companies(companies_path)

            df_companies = df_companies[df_companies["company_name"].str.strip() != ""]
            df_companies["company_name"] = df_companies["company_name"].str.strip()
            df_companies.to_csv(companies_path, index=False)

            # ✅ 学生数カウント（学籍番号または student_id）
            possible_keys = ["学籍番号", "student_id"]
            for key in possible_keys:
                if key in df_students.columns:
                    n_students = df_students[key].nunique()
                    break
            else:
                n_students = "不明（列名が見つかりません）"

            mode_msg = "3枠希望（＋自由訪問）" if mode == 3 else "4枠すべて希望"
            flash(f"✅ 学生データ：{n_students}人 ／ 企業データ：{len(df_companies)}社 をアップロードしました（モード：{mode_msg}）")

        except Exception as e:
            flash(f"⚠️ ファイル保存後の読み込みでエラーが発生しました：{str(e)}")


        return redirect(url_for("views.upload_file"))

    return render_template("upload.html")

# 企業名を番号付きリスト形式で整形
def prettify_with_number(companies):
    """
    リストを「1. 企業名<br>2. 企業名<br>...」形式で
    """
    if isinstance(companies, list):
        return "<br>".join(
            f"{i+1}. {c.replace('\u3000', ' ').strip()}" for i, c in enumerate(companies)
        )
    return str(companies).replace("\u3000", " ")

def prettify_with_slot_number_all(assigned_pairs, num_slots):
    """
    NUM_SLOTSぶん常に
      1. [割当企業 or 空]
      2. [割当企業 or 空]
      ...
    の形で <br>区切りで返す
    """
    # まず slot 番号→企業名 の辞書を作る
    slot_map = {idx: c for idx, c in assigned_pairs}
    lines = []
    for i in range(num_slots):
        company = slot_map.get(i, "")
        line = f"{i+1}" if not company else f"{i+1}.  {company.replace('\u3000',' ').strip()}"
        lines.append(line)
    return "<br>".join(lines)




# 統計
@views.route("/admin/stats")
def stats():
    try:
        # ---------- schedule.csv ----------
        df_schedule = pd.read_csv("schedule.csv", encoding="utf-8-sig")
        df_schedule.columns = (
            df_schedule.columns
            .str.replace("\ufeff", "", regex=False)  # BOM
            .str.replace("　", "", regex=False)      # 全角空白
            .str.strip()
        )
        if "student_id" not in df_schedule.columns:
            # 旧形式 (indexが学籍番号) に対応
            df_schedule.reset_index(inplace=True)
            df_schedule.rename(columns={"index": "student_id"}, inplace=True)

        # ---------- students.csv ----------
        df_pref_raw = pd.read_csv("uploads/students.csv", encoding="utf-8-sig")
        df_pref_raw.columns = (
            df_pref_raw.columns
            .str.replace("\ufeff", "", regex=False)
            .str.replace("　", "", regex=False)
            .str.strip()
        )
        df_pref_raw.rename(columns={
            "学籍番号": "student_id",
            "第一希望": "希望事業所1",
            "第二希望": "希望事業所2",
            "第三希望": "希望事業所3",
        }, inplace=True)

    except Exception as e:
        flash("必要なCSVファイルの読込に失敗しました：" + str(e))
        return redirect(url_for("views.admin"))

    # ---------- 希望リスト作成 ----------
    pref_rows = []
    for _, row in df_pref_raw.iterrows():
        for rank in (1, 2, 3):
            company = row.get(f"希望事業所{rank}")
            if pd.notna(company) and str(company).strip():
                pref_rows.append({
                    "student_id"  : row["student_id"],
                    "company_name": str(company).strip(),
                    "rank"        : rank,
                })
    df_preference = pd.DataFrame(pref_rows)

    # ---------- 反映率計算 ----------
    num_slots = sum(col.startswith("slot_") for col in df_schedule.columns)
    stats_data = []

    for _, row in df_schedule.iterrows():
        sid = row["student_id"]
        assigned_pairs = [
                            (i, row[f"slot_{i}"])
                            for i in range(num_slots)
                            if f"slot_{i}" in row
                            and pd.notna(row[f"slot_{i}"])
                            and row[f"slot_{i}"] != "自由訪問枠"
                        ]


        # 学生が出した希望企業（順番そのまま）
        preferred_rows = df_preference[df_preference.student_id == sid]
        original_pref_list = preferred_rows.sort_values("rank")["company_name"].tolist()

        if not original_pref_list:
            continue

        # （反映率は現状のままでOK）
        assigned_companies = [company for _, company in assigned_pairs]
        matched = [c for c in original_pref_list if c in assigned_companies]
        reflect_rate = 100 * len(matched) // len(original_pref_list)


        stats_data.append({
            "student_id": sid,
            "assigned"  : prettify_with_slot_number_all(assigned_pairs, num_slots),
            "matched": prettify_with_number(original_pref_list),
            "reflect_rate": f"{reflect_rate}%",
        })


    return render_template("stats.html", stats_data=stats_data)



@views.route("/admin/logs")
def logs():
    try:
        with open("logs.txt", "r", encoding="utf-8") as f:
            step_logs = f.read().splitlines()
    except FileNotFoundError:
        step_logs = ["ログファイルが存在しません"]

    # schedule.csv のチェック
    try:
        df_schedule = pd.read_csv("schedule.csv")
        from utils.logger import check_schedule_violations

        student_schedule = {
            row["student_id"]: [row.get(f"slot_{i}") for i in range(4)]
            for _, row in df_schedule.iterrows()
        }

        df_company = pd.read_csv("data/companies.csv")
        company_capacity = {
            row["company_name"]: [int(row.get(f"slot_{i}", 0)) for i in range(4)]
            for _, row in df_company.iterrows()
        }

        violation_logs = check_schedule_violations(student_schedule, company_capacity)

    except Exception:
        violation_logs = ["schedule.csv の読み込みまたは検査に失敗しました"]
        
    # ★★ ここから診断サマリを追加 ★★
    try:
        df_diag = pd.read_csv("diagnosis.csv")
        summary = df_diag.groupby(["dept", "result"]).size().unstack(fill_value=0)
        diag_table = summary.to_html(classes="table table-bordered")
    except Exception:
        diag_table = "<p>diagnosis.csv が存在しません</p>"

    # render_template に diag_table を渡す
    return render_template(
        "logs.html",
        step_logs=step_logs,
        violation_logs=violation_logs,
        diag_table=diag_table     # ← 追加
    )
