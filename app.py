import csv
import io
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from bson import ObjectId
from flask import Flask, jsonify, render_template, request, session
from pymongo import MongoClient

DEFAULT_URI = (
    "mongodb+srv://admin:admin123@edunidhi-finance.xrpoceh.mongodb.net/edunidhi_exams"
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")
app.config["MONGO_URI"] = os.environ.get("MONGO_URI", DEFAULT_URI)

client = MongoClient(app.config["MONGO_URI"])
db = client["edunidhi_exams"]


def ensure_default_admin() -> None:
    """Seed a default admin as a smart default."""
    if db.admins.count_documents({}) == 0:
        db.admins.insert_one(
            {"name": "Admin", "mobile": "9999999999", "password": "admin123"}
        )


def reset_demo_data() -> None:
    """Wipe non-admin data and reseed a clean demo set."""
    for name in [
        "teachers",
        "students",
        "tests_master",
        "tests_live",
        "responses",
        "schools",
        "classes",
        "sections",
    ]:
        db[name].delete_many({})
    ensure_default_admin()
    seed_minimal()


def seed_minimal():
    """Create demo data if none exists."""
    if db.schools.count_documents({}) > 0:
        return

    # Schools, classes, sections, students
    schools = []
    for sname in ["Greenwood", "Riverside"]:
        sid = db.schools.insert_one({"name": sname}).inserted_id
        schools.append(sid)
    for sid in schools:
        for cls_num in range(6, 11):
            cid = db.classes.insert_one({"name": f"Class {cls_num}", "school_id": str(sid)}).inserted_id
            for sname in ["A", "B"]:
                sec_id = db.sections.insert_one({"name": sname, "class_id": str(cid)}).inserted_id
                for i in range(2):  # two students per section
                    mobile = f"9{cls_num}{'1' if sname=='A' else '2'}{i:04d}"
                    db.students.insert_one(
                        {
                            "name": f"Student {cls_num}{sname}{i+1}",
                            "mobile": mobile,
                            "class": f"Class {cls_num}",
                            "section": sname,
                            "school": db.schools.find_one({"_id": sid})["name"],
                            "school_id": str(sid),
                            "class_id": str(cid),
                            "section_id": str(sec_id),
                            "archived": False,
                        }
                    )

    # Teachers
    teacher_ids = []
    for idx in range(3):
        mobile = f"800000000{idx}"
        t = db.teachers.find_one({"mobile": mobile})
        if t:
            teacher_ids.append(str(t["_id"]))
        else:
            teacher_ids.append(
                str(
                    db.teachers.insert_one(
                        {"name": f"Demo Teacher {idx+1}", "mobile": mobile, "archived": False}
                    ).inserted_id
                )
            )

    # Tests 10 single + 5 multi
    single = [
        {"q": "2+2=?", "opts": ["4", "3", "2", "5"], "correct_idx": 0},
        {"q": "Capital of France?", "opts": ["Paris", "Berlin", "Rome", "Madrid"], "correct_idx": 0},
        {"q": "5-3=?", "opts": ["1", "2", "3", "4"], "correct_idx": 1},
        {"q": "Sun rises in?", "opts": ["East", "West", "North", "South"], "correct_idx": 0},
        {"q": "Water formula?", "opts": ["H2O", "CO2", "O2", "NaCl"], "correct_idx": 0},
        {"q": "7+3=?", "opts": ["9", "10", "11", "8"], "correct_idx": 1},
        {"q": "Largest planet?", "opts": ["Earth", "Mars", "Jupiter", "Venus"], "correct_idx": 2},
        {"q": "Binary of 2?", "opts": ["10", "11", "01", "00"], "correct_idx": 0},
        {"q": "Prime number?", "opts": ["4", "6", "9", "11"], "correct_idx": 3},
        {"q": "Color of sky?", "opts": ["Blue", "Green", "Red", "Yellow"], "correct_idx": 0},
    ]
    multi = [
        {"q": "Select vowels", "opts": ["A", "B", "E", "G"], "correct_indexes": [0, 2]},
        {"q": "Select even numbers", "opts": ["1", "2", "3", "4"], "correct_indexes": [1, 3]},
        {"q": "Select fruits", "opts": ["Apple", "Car", "Banana", "Dog"], "correct_indexes": [0, 2]},
        {"q": "Select colors", "opts": ["Blue", "Chair", "Red", "Table"], "correct_indexes": [0, 2]},
        {"q": "Select mammals", "opts": ["Lion", "Snake", "Whale", "Eagle"], "correct_indexes": [0, 2]},
    ]
    live_id = db.tests_master.insert_one(
        {
            "title": "Demo Test Live",
            "teacher_id": teacher_ids[0],
            "single_correct": single,
            "multi_correct": multi,
            "created_at": now_iso(),
            "archived": False,
        }
    ).inserted_id
    past_id = db.tests_master.insert_one(
        {
            "title": "Demo Test Past",
            "teacher_id": teacher_ids[1] if len(teacher_ids) > 1 else teacher_ids[0],
            "single_correct": single,
            "multi_correct": multi,
            "created_at": now_iso(),
            "archived": False,
        }
    ).inserted_id

    # Live instance
    db.tests_live.insert_one(
        {
            "test_id": str(live_id),
            "class": "Class 6",
            "section": "A",
            "duration": 10,
            "start_time": now_iso(),
            "stopped": False,
        }
    )
    past_live = db.tests_live.insert_one(
        {
            "test_id": str(past_id),
            "class": "Class 6",
            "section": "A",
            "duration": 10,
            "start_time": now_iso(),
            "stopped": True,
        }
    ).inserted_id
    for stu in db.students.find({"class": "Class 6"}):
        db.responses.insert_one(
            {
                "test_live_id": str(past_live),
                "student_id": str(stu["_id"]),
                "answers_single": [0] * len(single),
                "answers_multi": [[0, 2]] * len(multi),
                "score": 5,
                "submitted_at": now_iso(),
            }
        )


def get_json() -> dict:
    return request.get_json(silent=True) or {}


def oid(value):
    try:
        return ObjectId(value)
    except Exception:
        return None


def require_role(roles: List[str]):
    role = session.get("role")
    if role not in roles:
        return False, jsonify({"error": "unauthorized"}), 401
    return True, None, None


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def score_submission(test_doc: dict, answers_single: List, answers_multi: List) -> Tuple[int, list, list]:
    """Score answers and return score plus solution breakdown. Coerces inputs to ints to avoid under-scoring."""

    def clean_single(idx: int, opts: list) -> Optional[int]:
        if idx >= len(answers_single):
            return None
        raw = answers_single[idx]
        try:
            val = int(raw)
        except Exception:
            return None
        return val if 0 <= val < len(opts) else None

    def clean_multi(idx: int, opts: list) -> set:
        if idx >= len(answers_multi):
            return set()
        raw_list = answers_multi[idx] or []
        cleaned = set()
        for raw in raw_list:
            try:
                val = int(raw)
            except Exception:
                continue
            if 0 <= val < len(opts):
                cleaned.add(val)
        return cleaned

    score = 0
    sol_single = []
    for idx, q in enumerate(test_doc.get("single_correct", [])):
        opts = q.get("opts", [])
        submitted = clean_single(idx, opts)
        try:
            correct = int(q.get("correct_idx"))
        except Exception:
            correct = None
        is_ok = submitted is not None and correct is not None and submitted == correct
        if is_ok:
            score += 1
        sol_single.append(
            {
                "q": q.get("q"),
                "options": opts,
                "correct": correct,
                "submitted": submitted,
                "is_correct": is_ok,
            }
        )

    sol_multi = []
    for idx, q in enumerate(test_doc.get("multi_correct", [])):
        opts = q.get("opts", [])
        submitted_set = clean_multi(idx, opts)
        correct_set = set()
        for raw in q.get("correct_indexes", []):
            try:
                val = int(raw)
            except Exception:
                continue
            if 0 <= val < len(opts):
                correct_set.add(val)
        is_ok = submitted_set == correct_set and len(submitted_set) > 0
        if is_ok:
            score += 2
        sol_multi.append(
            {
                "q": q.get("q"),
                "options": opts,
                "correct": list(correct_set),
                "submitted": list(submitted_set),
                "is_correct": is_ok,
            }
        )
    return score, sol_single, sol_multi


def max_score(test_doc: dict) -> int:
    """Return total possible marks (1 per single, 2 per multi)."""
    return len(test_doc.get("single_correct", [])) + 2 * len(test_doc.get("multi_correct", []))


def recalc_scores_for_test(test_id: str) -> None:
    """Recalculate scores for all responses of a given test id."""
    test_doc = db.tests_master.find_one({"_id": oid(test_id)})
    if not test_doc:
        return
    live_ids = [str(t["_id"]) for t in db.tests_live.find({"test_id": str(test_id)})]
    for r in db.responses.find({"test_live_id": {"$in": live_ids}}):
        score, sol_single, sol_multi = score_submission(
            test_doc, r.get("answers_single", []), r.get("answers_multi", [])
        )
        db.responses.update_one(
            {"_id": r["_id"]},
            {"$set": {"score": score, "solutions": {"single": sol_single, "multi": sol_multi}}},
        )


@app.route("/")
def index():
    # Start fresh to avoid stale sessions showing data without login
    session.clear()
    ensure_default_admin()
    seed_minimal()
    return render_template("frontend.html")


# -------- Auth (single login) -------- #
@app.post("/login")
def login():
    ensure_default_admin()
    data = get_json()
    mobile = data.get("mobile")
    if not mobile:
        return jsonify({"error": "Mobile required"}), 400

    # Clear any previous session to prevent stale admin access
    session.clear()

    admin = db.admins.find_one({"mobile": mobile})
    teacher = db.teachers.find_one({"mobile": mobile, "archived": {"$ne": True}})
    student = db.students.find_one({"mobile": mobile, "archived": {"$ne": True}})

    if admin:
        return jsonify(
            {
                "status": "password_required",
                "role": "admin",
                "name": admin.get("name"),
                "mobile": mobile,
            }
        )
    if teacher:
        session.clear()
        session.update(
            {"role": "teacher", "user_id": str(teacher["_id"]), "name": teacher["name"]}
        )
        return jsonify(
            {"status": "ok", "role": "teacher", "name": teacher.get("name", "")}
        )
    if student:
        session.clear()
        session.update(
            {
                "role": "student",
                "user_id": str(student["_id"]),
                "name": student.get("name"),
                "class": student.get("class"),
                "section": student.get("section"),
            }
        )
        return jsonify(
            {
                "status": "ok",
                "role": "student",
                "name": student.get("name", ""),
                "class": student.get("class"),
                "section": student.get("section"),
            }
        )
    return jsonify({"error": "User not found"}), 404


@app.post("/admin-password")
def admin_password():
    ensure_default_admin()
    data = get_json()
    mobile = data.get("mobile")
    password = data.get("password")
    if not mobile or not password:
        return jsonify({"error": "Invalid password"}), 401
    admin = db.admins.find_one({"mobile": mobile, "password": password})
    if not admin:
        return jsonify({"error": "Invalid password"}), 401
    session.clear()
    session.update(
        {
            "role": "admin",
            "user_id": str(admin["_id"]),
            "name": admin.get("name"),
        }
    )
    return jsonify({"status": "ok", "role": "admin", "name": admin.get("name", "")})


@app.get("/auth/me")
def auth_me():
    role = session.get("role")
    if not role:
        return jsonify({"role": None})
    return jsonify({"role": role, "name": session.get("name"), "class": session.get("class"), "section": session.get("section")})


@app.post("/logout")
def logout():
    session.clear()
    return jsonify({"status": "ok"})


@app.post("/admin/reset-demo")
def admin_reset_demo():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    reset_demo_data()
    return jsonify({"status": "ok", "message": "Demo data reset"})


# -------- Admin: Student management -------- #
@app.post("/admin/student/add")
def admin_student_add():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    name = data.get("student_name") or data.get("name")
    mobile = data.get("mobile")
    school_id = data.get("school_id")
    class_id = data.get("class_id")
    section_id = data.get("section_id")
    if not all([name, mobile, school_id, class_id, section_id]):
        return jsonify({"error": "Missing fields"}), 400
    school_doc = db.schools.find_one({"_id": oid(school_id)})
    class_doc = db.classes.find_one({"_id": oid(class_id), "school_id": school_id})
    sec_doc = db.sections.find_one({"_id": oid(section_id), "class_id": class_id})
    if not school_doc or not class_doc or not sec_doc:
        return jsonify({"error": "Invalid school/class/section"}), 400
    if db.students.find_one({"mobile": mobile}):
        return jsonify({"error": "Student exists"}), 409
    inserted = db.students.insert_one(
        {
            "name": name,
            "mobile": mobile,
            "class": class_doc.get("name"),
            "section": sec_doc.get("name"),
            "school": school_doc.get("name"),
            "school_id": school_id,
            "class_id": class_id,
            "section_id": section_id,
        }
    )
    return jsonify({"status": "ok", "student_id": str(inserted.inserted_id)})


@app.post("/admin/student/edit")
def admin_student_edit():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    if not sid:
        return jsonify({"error": "id required"}), 400
    updates = {}
    # Basic fields
    if data.get("name") or data.get("student_name"):
        updates["name"] = data.get("name") or data.get("student_name")
    if data.get("mobile"):
        updates["mobile"] = data.get("mobile")

    # Optional reassignment of school/class/section by IDs
    school_id = data.get("school_id")
    class_id = data.get("class_id")
    section_id = data.get("section_id")
    if school_id and class_id and section_id:
        school_doc = db.schools.find_one({"_id": oid(school_id)})
        class_doc = db.classes.find_one({"_id": oid(class_id), "school_id": school_id})
        sec_doc = db.sections.find_one({"_id": oid(section_id), "class_id": class_id})
        if not school_doc or not class_doc or not sec_doc:
            return jsonify({"error": "Invalid school/class/section"}), 400
        updates.update(
            {
                "school_id": school_id,
                "class_id": class_id,
                "section_id": section_id,
                "school": school_doc.get("name"),
                "class": class_doc.get("name"),
                "section": sec_doc.get("name"),
            }
        )

    if not updates:
        return jsonify({"error": "No updates"}), 400
    db.students.update_one({"_id": oid(sid)}, {"$set": updates})
    return jsonify({"status": "ok"})


@app.post("/admin/student/delete")
def admin_student_delete():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    if not sid:
        return jsonify({"error": "id required"}), 400
    db.students.delete_one({"_id": oid(sid)})
    return jsonify({"status": "ok"})


@app.post("/admin/student/bulk")
def admin_student_bulk():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    school_id = request.form.get("school_id")
    class_id = request.form.get("class_id")
    section_id = request.form.get("section_id")
    if not all([school_id, class_id, section_id]):
        return jsonify({"error": "school_id, class_id, section_id required"}), 400
    school_doc = db.schools.find_one({"_id": oid(school_id)})
    class_doc = db.classes.find_one({"_id": oid(class_id), "school_id": school_id})
    sec_doc = db.sections.find_one({"_id": oid(section_id), "class_id": class_id})
    if not school_doc or not class_doc or not sec_doc:
        return jsonify({"error": "Invalid school/class/section"}), 400
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"error": "No file"}), 400
    content = uploaded.stream.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    added = 0
    for row in reader:
        name = row.get("student_name") or row.get("name")
        mobile = row.get("mobile")
        if not mobile or db.students.find_one({"mobile": mobile}):
            continue
        db.students.insert_one(
            {
                "name": name,
                "mobile": mobile,
                "class": class_doc.get("name"),
                "section": sec_doc.get("name"),
                "school": school_doc.get("name"),
                "school_id": school_id,
                "class_id": class_id,
                "section_id": section_id,
            }
        )
        added += 1
    return jsonify({"status": "ok", "added": added})


@app.get("/admin/students")
def admin_students_list():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    students = []
    for s in db.students.find().sort("name", 1):
        students.append(
            {
                "id": str(s["_id"]),
                "name": s.get("name"),
                "mobile": s.get("mobile"),
                "class": s.get("class"),
                "section": s.get("section"),
                "school": s.get("school"),
                "school_id": s.get("school_id"),
                "class_id": s.get("class_id"),
                "section_id": s.get("section_id"),
                "archived": s.get("archived", False),
            }
        )
    return jsonify({"students": students})


@app.post("/admin/student/archive")
def admin_student_archive():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    archived = data.get("archived", True)
    if not sid:
        return jsonify({"error": "id required"}), 400
    db.students.update_one({"_id": oid(sid)}, {"$set": {"archived": bool(archived)}})
    return jsonify({"status": "ok"})


# -------- Admin: School / Class / Section management -------- #
@app.get("/admin/schools/tree")
def admin_schools_tree():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    schools = []
    class_map = {}
    sec_map = {}
    for c in db.classes.find():
        class_map.setdefault(c.get("school_id"), []).append(
            {"id": str(c["_id"]), "name": c.get("name")}
        )
    for s in db.sections.find():
        sec_map.setdefault(s.get("class_id"), []).append(
            {"id": str(s["_id"]), "name": s.get("name")}
        )
    for sch in db.schools.find().sort("name", 1):
        classes = []
        for c in class_map.get(str(sch["_id"]), []):
            classes.append(
                {
                    "id": c["id"],
                    "name": c["name"],
                    "sections": sec_map.get(c["id"], []),
                }
            )
        schools.append({"id": str(sch["_id"]), "name": sch.get("name"), "classes": classes})
    return jsonify({"schools": schools})


@app.post("/admin/school/add")
def admin_school_add():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    name = data.get("name")
    if not name:
        return jsonify({"error": "name required"}), 400
    inserted = db.schools.insert_one({"name": name})
    return jsonify({"status": "ok", "school_id": str(inserted.inserted_id)})


@app.post("/admin/school/edit")
def admin_school_edit():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    name = data.get("name")
    if not sid or not name:
        return jsonify({"error": "id and name required"}), 400
    db.schools.update_one({"_id": oid(sid)}, {"$set": {"name": name}})
    return jsonify({"status": "ok"})


@app.post("/admin/school/delete")
def admin_school_delete():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    if not sid:
        return jsonify({"error": "id required"}), 400
    db.sections.delete_many({"class_id": {"$in": [str(c["_id"]) for c in db.classes.find({"school_id": sid})]}})
    db.classes.delete_many({"school_id": sid})
    db.schools.delete_one({"_id": oid(sid)})
    return jsonify({"status": "ok"})


@app.post("/admin/class/add")
def admin_class_add():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    name = data.get("name")
    school_id = data.get("school_id")
    if not name or not school_id:
        return jsonify({"error": "name and school_id required"}), 400
    inserted = db.classes.insert_one({"name": name, "school_id": school_id})
    return jsonify({"status": "ok", "class_id": str(inserted.inserted_id)})


@app.post("/admin/class/edit")
def admin_class_edit():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    cid = data.get("id")
    name = data.get("name")
    if not cid or not name:
        return jsonify({"error": "id and name required"}), 400
    db.classes.update_one({"_id": oid(cid)}, {"$set": {"name": name}})
    return jsonify({"status": "ok"})


@app.post("/admin/class/delete")
def admin_class_delete():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    cid = data.get("id")
    if not cid:
        return jsonify({"error": "id required"}), 400
    db.sections.delete_many({"class_id": cid})
    db.classes.delete_one({"_id": oid(cid)})
    return jsonify({"status": "ok"})


@app.post("/admin/section/add")
def admin_section_add():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    name = data.get("name")
    class_id = data.get("class_id")
    if not name or not class_id:
        return jsonify({"error": "name and class_id required"}), 400
    inserted = db.sections.insert_one({"name": name, "class_id": class_id})
    return jsonify({"status": "ok", "section_id": str(inserted.inserted_id)})


@app.post("/admin/section/edit")
def admin_section_edit():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    name = data.get("name")
    if not sid or not name:
        return jsonify({"error": "id and name required"}), 400
    db.sections.update_one({"_id": oid(sid)}, {"$set": {"name": name}})
    return jsonify({"status": "ok"})


@app.post("/admin/section/delete")
def admin_section_delete():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    if not sid:
        return jsonify({"error": "id required"}), 400
    db.sections.delete_one({"_id": oid(sid)})
    return jsonify({"status": "ok"})


# -------- Admin: Teacher management -------- #
@app.post("/admin/teacher/add")
def admin_teacher_add():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    name = data.get("name")
    mobile = data.get("mobile")
    if not name or not mobile:
        return jsonify({"error": "Missing fields"}), 400
    if db.teachers.find_one({"mobile": mobile}):
        return jsonify({"error": "Teacher exists"}), 409
    inserted = db.teachers.insert_one({"name": name, "mobile": mobile})
    return jsonify({"status": "ok", "teacher_id": str(inserted.inserted_id)})


@app.post("/admin/teacher/edit")
def admin_teacher_edit():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    tid = data.get("id")
    if not tid:
        return jsonify({"error": "id required"}), 400
    updates = {k: v for k, v in data.items() if k in ["name", "mobile"] and v}
    if not updates:
        return jsonify({"error": "No updates"}), 400
    db.teachers.update_one({"_id": oid(tid)}, {"$set": updates})
    return jsonify({"status": "ok"})


@app.post("/admin/teacher/archive")
def admin_teacher_archive():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    tid = data.get("id")
    archived = data.get("archived", True)
    if not tid:
        return jsonify({"error": "id required"}), 400
    db.teachers.update_one({"_id": oid(tid)}, {"$set": {"archived": bool(archived)}})
    return jsonify({"status": "ok"})


@app.post("/admin/teacher/delete")
def admin_teacher_delete():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    tid = data.get("id")
    if not tid:
        return jsonify({"error": "id required"}), 400
    db.teachers.delete_one({"_id": oid(tid)})
    return jsonify({"status": "ok"})


@app.get("/admin/teachers")
def admin_teachers_list():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    teachers = []
    for t in db.teachers.find().sort("name", 1):
        teachers.append(
            {"id": str(t["_id"]), "name": t.get("name"), "mobile": t.get("mobile"), "archived": t.get("archived", False)}
        )
    return jsonify({"teachers": teachers})


# -------- Admin: Tests and analytics -------- #
def teacher_name(tid):
    if not tid:
        return "Unknown"
    t = db.teachers.find_one({"_id": oid(tid)})
    return t["name"] if t else "Unknown"


# -------- Subjects master -------- #
@app.get("/subjects")
def list_subjects():
    ok, resp, code = require_role(["admin", "teacher", "student"])
    if not ok:
        return resp, code
    subjects = []
    for s in db.subjects.find().sort("name", 1):
        subjects.append({"id": str(s["_id"]), "name": s.get("name")})
    if not subjects:
        subjects = [{"id": "", "name": "General"}]
    return jsonify({"subjects": subjects})


@app.post("/admin/subject/add")
def admin_subject_add():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    existing = db.subjects.find_one({"name": name})
    if existing:
        return jsonify({"error": "Subject exists"}), 409
    db.subjects.insert_one({"name": name})
    return jsonify({"status": "ok"})


@app.post("/admin/subject/edit")
def admin_subject_edit():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    name = (data.get("name") or "").strip()
    if not sid or not name:
        return jsonify({"error": "id and name required"}), 400
    db.subjects.update_one({"_id": oid(sid)}, {"$set": {"name": name}})
    return jsonify({"status": "ok"})


@app.post("/admin/subject/delete")
def admin_subject_delete():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    sid = data.get("id")
    if not sid:
        return jsonify({"error": "id required"}), 400
    db.subjects.delete_one({"_id": oid(sid)})
    return jsonify({"status": "ok"})


@app.get("/admin/tests")
def admin_tests():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    teacher_filter = request.args.get("teacher_id")
    query = {}
    if teacher_filter:
        query["teacher_id"] = teacher_filter
    tests = []
    for t in db.tests_master.find(query).sort("created_at", -1):
        tests.append(
            {
                "id": str(t["_id"]),
                "title": t.get("title"),
                "teacher_id": t.get("teacher_id"),
                "teacher_name": teacher_name(t.get("teacher_id")),
                "archived": t.get("archived", False),
                "subject": t.get("subject", "General"),
                "created_at": t.get("created_at"),
            }
        )
    return jsonify({"tests": tests})


@app.post("/admin/test/live")
def admin_test_live():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    test_id = data.get("test_id")
    class_name = data.get("class") or data.get("class_name")
    section = data.get("section")
    duration = int(
        data.get("duration")
        or data.get("duration_minutes")
        or data.get("duration_min")
        or 0
    )
    if duration <= 0:
        duration = 10
    start_time = data.get("start_time")
    try:
        start_dt = datetime.fromisoformat(start_time) if start_time else datetime.utcnow()
    except Exception:
        return jsonify({"error": "Invalid start_time"}), 400
    if not test_id or not class_name or not section:
        return jsonify({"error": "Missing fields"}), 400
    found = db.tests_master.find_one({"_id": oid(test_id)})
    if not found or found.get("archived"):
        return jsonify({"error": "Test not found"}), 404
    inserted = db.tests_live.insert_one(
        {
            "test_id": str(test_id),
            "class": class_name,
            "section": section,
            "duration": duration,
            "start_time": start_dt.isoformat(),
        }
    )
    return jsonify({"status": "ok", "test_live_id": str(inserted.inserted_id)})


@app.get("/admin/live-tests")
def admin_live_tests():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    live = []
    for tl in db.tests_live.find({"stopped": {"$ne": True}}):
        test_doc = db.tests_master.find_one({"_id": oid(tl.get("test_id"))})
        if not test_doc or test_doc.get("archived"):
            continue
        live.append(
            {
                "id": str(tl["_id"]),
                "title": test_doc.get("title"),
                "class": tl.get("class"),
                "section": tl.get("section"),
                "duration": int(tl.get("duration") or 10),
                "start_time": tl.get("start_time"),
            }
        )
    return jsonify({"live": live})


@app.post("/admin/test/stop")
def admin_test_stop():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    tlid = data.get("test_live_id")
    if not tlid:
        return jsonify({"error": "test_live_id required"}), 400
    res = db.tests_live.update_one({"_id": oid(tlid)}, {"$set": {"stopped": True}})
    if res.matched_count == 0:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"status": "ok", "message": "Test stopped"})


@app.post("/admin/test/archive")
def admin_test_archive():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    tid = data.get("test_id")
    if not tid:
        return jsonify({"error": "test_id required"}), 400
    db.tests_master.update_one({"_id": oid(tid)}, {"$set": {"archived": True}})
    db.tests_live.update_many({"test_id": str(tid)}, {"$set": {"stopped": True}})
    return jsonify({"status": "ok"})


@app.post("/admin/test/unarchive")
def admin_test_unarchive():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    tid = data.get("test_id")
    if not tid:
        return jsonify({"error": "test_id required"}), 400
    db.tests_master.update_one({"_id": oid(tid)}, {"$set": {"archived": False}})
    return jsonify({"status": "ok"})


def collect_responses_for_test(test_id: str) -> Tuple[list, dict, list]:
    live_ids = [str(t["_id"]) for t in db.tests_live.find({"test_id": str(test_id)})]
    responses = list(
        db.responses.find({"test_live_id": {"$in": live_ids}, "submitted_at": {"$exists": True}})
    )
    students = {
        str(s["_id"]): s
        for s in db.students.find({"_id": {"$in": [oid(r["student_id"]) for r in responses if r.get("student_id")]}})
    }
    return responses, students, live_ids


def build_analytics(test_doc: dict, responses: list, students: dict) -> dict:
    scores = [r.get("score", 0) for r in responses]
    avg = round(sum(scores) / len(scores), 2) if scores else 0
    top = max(scores) if scores else 0
    total_responses = len(responses)
    per_student = [
        {
            "student_id": r.get("student_id"),
            "name": students.get(r.get("student_id"), {}).get("name", "Student"),
            "score": r.get("score", 0),
        }
        for r in responses
    ]

    single = []
    for idx, q in enumerate(test_doc.get("single_correct", [])):
        counts = [{"count": 0, "students": []} for _ in q.get("opts", [])]
        attempted = 0
        for r in responses:
            ans = r.get("answers_single", [])
            if idx < len(ans) and ans[idx] is not None and ans[idx] < len(counts):
                attempted += 1
                counts[ans[idx]]["count"] += 1
                sid = r.get("student_id")
                if sid in students:
                    counts[ans[idx]]["students"].append(students[sid]["name"])
        single.append(
            {
                "q": q.get("q"),
                "opts": q.get("opts", []),
                "correct_idx": q.get("correct_idx"),
                "counts": counts,
                "attempted": attempted,
                "total": total_responses,
            }
        )

    multi = []
    for idx, q in enumerate(test_doc.get("multi_correct", [])):
        counts = [{"count": 0, "students": []} for _ in q.get("opts", [])]
        attempted = 0
        for r in responses:
            ans = r.get("answers_multi", [])
            if idx < len(ans):
                if ans[idx]:
                    attempted += 1
                for opt in ans[idx] or []:
                    if opt is not None and opt < len(counts):
                        counts[opt]["count"] += 1
                        sid = r.get("student_id")
                        if sid in students:
                            counts[opt]["students"].append(students[sid]["name"])
        multi.append(
            {
                "q": q.get("q"),
                "opts": q.get("opts", []),
                "correct_indexes": q.get("correct_indexes", []),
                "counts": counts,
                "attempted": attempted,
                "total": total_responses,
            }
        )

    return {
        "average": avg,
        "top": top,
        "total_responses": total_responses,
        "per_student": per_student,
        "single": single,
        "multi": multi,
    }


@app.get("/admin/analytics/<test_id>")
def admin_analytics(test_id):
    ok, resp, code = require_role(["admin", "teacher"])
    if not ok:
        return resp, code
    test_doc = db.tests_master.find_one({"_id": oid(test_id)})
    if not test_doc or test_doc.get("archived"):
        return jsonify({"error": "Test not found"}), 404
    responses, students, _ = collect_responses_for_test(test_id)
    data = build_analytics(test_doc, responses, students)
    return jsonify(data)


# -------- Teacher -------- #
@app.post("/teacher/test/create")
def teacher_test_create():
    ok, resp, code = require_role(["teacher"])
    if not ok:
        return resp, code
    data = get_json()
    title = data.get("title")
    single = data.get("single_correct", [])
    multi = data.get("multi_correct", [])
    subject = data.get("subject") or "General"
    if not title:
        return jsonify({"error": "Title required"}), 400
    if len(single) != 10 or len(multi) != 5:
        return jsonify({"error": "Need 10 single and 5 multi questions"}), 400
    doc = {
        "title": title,
        "subject": subject,
        "teacher_id": session.get("user_id"),
        "single_correct": single,
        "multi_correct": multi,
        "created_at": now_iso(),
        "archived": False,
    }
    inserted = db.tests_master.insert_one(doc)
    return jsonify({"status": "ok", "test_id": str(inserted.inserted_id)})


@app.post("/teacher/test/edit")
def teacher_test_edit():
    ok, resp, code = require_role(["teacher"])
    if not ok:
        return resp, code
    data = get_json()
    test_id = data.get("test_id")
    if not test_id:
        return jsonify({"error": "test_id required"}), 400
    doc = db.tests_master.find_one({"_id": oid(test_id)})
    if not doc or doc.get("archived") or str(doc.get("teacher_id")) != session.get("user_id"):
        return jsonify({"error": "Test not found"}), 404
    updates = {}
    if "title" in data:
        updates["title"] = data["title"]
    if "single_correct" in data:
        updates["single_correct"] = data["single_correct"]
    if "multi_correct" in data:
        updates["multi_correct"] = data["multi_correct"]
    if "subject" in data:
        updates["subject"] = data.get("subject") or "General"
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400
    db.tests_master.update_one({"_id": oid(test_id)}, {"$set": updates})
    recalc_scores_for_test(test_id)
    return jsonify({"status": "ok"})


@app.post("/admin/test/edit")
def admin_test_edit():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    test_id = data.get("test_id")
    if not test_id:
        return jsonify({"error": "test_id required"}), 400
    doc = db.tests_master.find_one({"_id": oid(test_id)})
    if not doc:
        return jsonify({"error": "Not found"}), 404
    updates = {}
    if "title" in data:
        updates["title"] = data["title"]
    if "single_correct" in data:
        updates["single_correct"] = data["single_correct"]
    if "multi_correct" in data:
        updates["multi_correct"] = data["multi_correct"]
    if "subject" in data:
        updates["subject"] = data.get("subject") or "General"
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400
    db.tests_master.update_one({"_id": oid(test_id)}, {"$set": updates})
    recalc_scores_for_test(test_id)
    return jsonify({"status": "ok"})


@app.post("/admin/test/delete")
def admin_test_delete():
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    data = get_json()
    test_id = data.get("test_id")
    if not test_id:
        return jsonify({"error": "test_id required"}), 400
    live_ids = [str(t["_id"]) for t in db.tests_live.find({"test_id": str(test_id)})]
    if live_ids:
        db.responses.delete_many({"test_live_id": {"$in": live_ids}})
        live_oids = [oid(tid) for tid in live_ids if oid(tid)]
        if live_oids:
            db.tests_live.delete_many({"_id": {"$in": live_oids}})
    deleted = db.tests_master.delete_one({"_id": oid(test_id)})
    if deleted.deleted_count == 0:
        return jsonify({"error": "Test not found"}), 404
    return jsonify({"status": "ok", "message": "Test deleted"})


@app.get("/admin/test/<test_id>")
def admin_test_details(test_id):
    ok, resp, code = require_role(["admin"])
    if not ok:
        return resp, code
    doc = db.tests_master.find_one({"_id": oid(test_id)})
    if not doc:
        return jsonify({"error": "Test not found"}), 404
    return jsonify(
        {
            "id": str(doc["_id"]),
            "title": doc.get("title"),
            "subject": doc.get("subject", "General"),
            "created_at": doc.get("created_at"),
            "single_correct": doc.get("single_correct", []),
            "multi_correct": doc.get("multi_correct", []),
            "archived": doc.get("archived", False),
        }
    )


@app.post("/admin/test/recalc")
def admin_test_recalc():
    ok, resp, code = require_role(["admin", "teacher"])
    if not ok:
        return resp, code
    data = get_json()
    test_id = data.get("test_id")
    if not test_id:
        return jsonify({"error": "test_id required"}), 400
    doc = db.tests_master.find_one({"_id": oid(test_id)})
    if not doc:
        return jsonify({"error": "Test not found"}), 404
    if session.get("role") == "teacher" and str(doc.get("teacher_id")) != session.get("user_id"):
        return jsonify({"error": "unauthorized"}), 401
    recalc_scores_for_test(test_id)
    return jsonify({"status": "ok", "message": "Scores recalculated"})


@app.get("/teacher/tests")
def teacher_tests():
    ok, resp, code = require_role(["teacher"])
    if not ok:
        return resp, code
    tid = session.get("user_id")
    tests = []
    for t in db.tests_master.find({"teacher_id": tid, "archived": {"$ne": True}}).sort(
        "created_at", -1
    ):
        tests.append({"id": str(t["_id"]), "title": t.get("title")})
    return jsonify({"tests": tests})


@app.get("/teacher/analytics/<test_id>")
def teacher_analytics(test_id):
    ok, resp, code = require_role(["teacher"])
    if not ok:
        return resp, code
    doc = db.tests_master.find_one({"_id": oid(test_id)})
    if not doc or doc.get("archived") or str(doc.get("teacher_id")) != session.get("user_id"):
        return jsonify({"error": "Test not found"}), 404
    responses, students, _ = collect_responses_for_test(test_id)
    data = build_analytics(doc, responses, students)
    return jsonify(data)


# -------- Student -------- #
def live_now(live_doc: dict) -> bool:
    """Return whether a live test is currently within its scheduled window."""
    start = live_doc.get("start_time")
    duration = int(live_doc.get("duration", 0) or 0)
    try:
        start_dt = datetime.fromisoformat(start) if start else None
    except Exception:
        start_dt = None
    if not start_dt:
        start_dt = datetime.utcnow()
    if duration <= 0:
        return False
    end_dt = start_dt + timedelta(minutes=duration)
    now = datetime.utcnow()
    return start_dt <= now <= end_dt


def is_time_over(tl: dict, started_at: Optional[str]) -> bool:
    """Check if a student's attempt window is over based on their start time or the live start time."""
    duration = int(tl.get("duration", 0) or 0)
    if duration <= 0:
        return False
    try:
        base = datetime.fromisoformat(started_at) if started_at else None
    except Exception:
        base = None
    if not base:
        try:
            base = datetime.fromisoformat(tl.get("start_time"))
        except Exception:
            base = None
    if not base:
        return False
    return datetime.utcnow() > base + timedelta(minutes=duration)


@app.get("/student/live")
def student_live():
    ok, resp, code = require_role(["student"])
    if not ok:
        return resp, code
    cls = session.get("class")
    sec = session.get("section")
    live = []
    sid = session.get("user_id")
    for tl in db.tests_live.find({"stopped": {"$ne": True}}).sort("start_time", -1):
        test_doc = db.tests_master.find_one({"_id": oid(tl.get("test_id"))})
        if not test_doc or test_doc.get("archived"):
            continue
        # enforce class/section targeting
        if cls and tl.get("class") and tl.get("class") != cls:
            continue
        if sec and tl.get("section") and tl.get("section") != sec:
            continue
        # enforce schedule window
        if not live_now(tl):
            continue
        # skip if student already submitted
        attempted = db.responses.find_one(
            {"test_live_id": str(tl["_id"]), "student_id": sid, "submitted_at": {"$exists": True}}
        )
        if attempted:
            continue
        live.append(
            {
                "test_live_id": str(tl["_id"]),
                "title": test_doc.get("title"),
                "duration": int(tl.get("duration") or 10),
                "start_time": tl.get("start_time"),
                "teacher_name": teacher_name(test_doc.get("teacher_id")),
                "class": tl.get("class"),
                "section": tl.get("section"),
                "match": True,
            }
        )
    return jsonify({"tests": live})


@app.get("/student/test/<test_live_id>")
def student_test(test_live_id):
    ok, resp, code = require_role(["student"])
    if not ok:
        return resp, code
    tl = db.tests_live.find_one({"_id": oid(test_live_id)})
    if not tl or tl.get("stopped"):
        return jsonify({"error": "Test not active"}), 404
    # enforce class/section targeting
    if session.get("class") and tl.get("class") and tl.get("class") != session.get("class"):
        return jsonify({"error": "unauthorized"}), 401
    if session.get("section") and tl.get("section") and tl.get("section") != session.get("section"):
        return jsonify({"error": "unauthorized"}), 401
    if not live_now(tl):
        return jsonify({"error": "Test not active"}), 404
    test_doc = db.tests_master.find_one({"_id": oid(tl.get("test_id"))})
    if not test_doc or test_doc.get("archived"):
        return jsonify({"error": "Test not found"}), 404
    # Prevent re-attempts
    sid = session.get("user_id")
    existing_response = db.responses.find_one(
        {"test_live_id": test_live_id, "student_id": sid, "submitted_at": {"$exists": True}}
    )
    if existing_response:
        return jsonify({"error": "Already attempted"}), 400
    # set per-student start time (always from now for a fresh window)
    start_time = now_iso()
    db.responses.update_one(
        {"test_live_id": test_live_id, "student_id": sid},
        {"$set": {"started_at": start_time}},
        upsert=True,
    )
    questions = []
    for idx, q in enumerate(test_doc.get("single_correct", [])):
        questions.append(
            {"id": f"s-{idx}", "type": "single", "q": q.get("q"), "opts": q.get("opts", [])}
        )
    for idx, q in enumerate(test_doc.get("multi_correct", [])):
        questions.append(
            {"id": f"m-{idx}", "type": "multi", "q": q.get("q"), "opts": q.get("opts", [])}
        )
    return jsonify(
        {
            "title": test_doc.get("title"),
            "test_live_id": test_live_id,
            "duration": int(tl.get("duration") or 10),
            "stopped": tl.get("stopped", False),
            "started_at": start_time,
            "questions": questions,
        }
    )


@app.post("/student/submit")
def student_submit():
    ok, resp, code = require_role(["student"])
    if not ok:
        return resp, code
    data = get_json()
    test_live_id = data.get("test_live_id")
    answers = data.get("answers", [])
    sid = session.get("user_id")
    tl = db.tests_live.find_one({"_id": oid(test_live_id)})
    if not tl:
        return jsonify({"error": "Test not found"}), 404
    if tl.get("stopped"):
        return jsonify({"error": "Test stopped"}), 400
    if session.get("class") and tl.get("class") and tl.get("class") != session.get("class"):
        return jsonify({"error": "unauthorized"}), 401
    if session.get("section") and tl.get("section") and tl.get("section") != session.get("section"):
        return jsonify({"error": "unauthorized"}), 401
    if not live_now(tl):
        return jsonify({"error": "Test not active"}), 404
    test_doc = db.tests_master.find_one({"_id": oid(tl.get("test_id"))})
    if not test_doc or test_doc.get("archived"):
        return jsonify({"error": "Test not found"}), 404
    # Prevent reattempts after submit
    attempted = db.responses.find_one(
        {"test_live_id": test_live_id, "student_id": sid, "submitted_at": {"$exists": True}}
    )
    if attempted:
        return jsonify({"error": "Already attempted"}), 400

    single_ans = [None] * len(test_doc.get("single_correct", []))
    multi_ans = [[] for _ in range(len(test_doc.get("multi_correct", [])))]
    single_map: Dict[int, Optional[int]] = {}
    multi_map: Dict[int, List[int]] = {}
    for entry in answers:
        qid = entry.get("id")
        selected = entry.get("selected", [])
        if qid and qid.startswith("s-"):
            try:
                idx = int(qid.split("-")[1])
            except Exception:
                continue
            if 0 <= idx < len(single_ans):
                sel_list = selected if isinstance(selected, list) else [selected]
                if sel_list:
                    try:
                        single_map[idx] = int(sel_list[-1])
                    except Exception:
                        continue
        elif qid and qid.startswith("m-"):
            try:
                idx = int(qid.split("-")[1])
            except Exception:
                continue
            if 0 <= idx < len(multi_ans):
                if isinstance(selected, str):
                    selected = [x for x in selected.replace(" ", "").split(",") if x]
                elif isinstance(selected, (int, float)):
                    selected = [selected]
                elif not isinstance(selected, list):
                    try:
                        selected = list(selected)
                    except Exception:
                        selected = []
                cleaned = []
                for x in selected:
                    try:
                        cleaned.append(int(x))
                    except Exception:
                        continue
                multi_map[idx] = cleaned
    for idx, val in single_map.items():
        single_ans[idx] = val
    for idx, vals in multi_map.items():
        # keep unique choices in order
        seen = []
        for v in vals:
            if v not in seen:
                seen.append(v)
        multi_ans[idx] = seen

    # enforce per-student timer
    sid = session.get("user_id")
    started_at = (
        db.responses.find_one({"test_live_id": test_live_id, "student_id": sid}) or {}
    ).get("started_at")
    if is_time_over(tl, started_at):
        return jsonify({"error": "time_over"}), 400
    auto = False

    score, sol_single, sol_multi = score_submission(test_doc, single_ans, multi_ans)
    total_marks = max_score(test_doc)
    sid = session.get("user_id")
    db.responses.update_one(
        {"test_live_id": test_live_id, "student_id": sid},
        {
            "$set": {
                "test_live_id": test_live_id,
                "student_id": sid,
                "answers_single": single_ans,
                "answers_multi": multi_ans,
                "score": score,
                "submitted_at": now_iso(),
                "auto_submitted": auto,
            }
        },
        upsert=True,
    )

    all_res = list(
        db.responses.find({"test_live_id": test_live_id, "submitted_at": {"$exists": True}})
    )
    scores = [r.get("score", 0) for r in all_res]
    class_avg = round(sum(scores) / len(scores), 2) if scores else 0
    sorted_scores = sorted(
        all_res, key=lambda r: (-r.get("score", 0), r.get("submitted_at", ""))
    )
    rank = next(
        (i + 1 for i, r in enumerate(sorted_scores) if r.get("student_id") == sid), None
    )

    return jsonify(
        {
            "status": "ok",
            "score": score,
            "max_score": total_marks,
            "rank": rank,
            "total": len(sorted_scores),
            "class_average": class_avg,
            "solutions": {"single": sol_single, "multi": sol_multi},
        }
    )


@app.get("/student/history")
def student_history():
    ok, resp, code = require_role(["student"])
    if not ok:
        return resp, code
    sid = session.get("user_id")
    results = []
    for r in db.responses.find({"student_id": sid, "submitted_at": {"$exists": True}}).sort(
        "submitted_at", -1
    ):
        tl = db.tests_live.find_one({"_id": oid(r.get("test_live_id"))})
        test_doc = (
            db.tests_master.find_one({"_id": oid(tl.get("test_id"))}) if tl else None
        )
        if test_doc and test_doc.get("archived"):
            continue
        results.append(
            {
                "test_live_id": r.get("test_live_id"),
                "title": test_doc.get("title") if test_doc else "Test",
                "subject": test_doc.get("subject", "General") if test_doc else "",
                "score": r.get("score", 0),
                "submitted_at": r.get("submitted_at"),
            }
        )
    return jsonify({"results": results})


@app.get("/student/result/<test_live_id>")
def student_result(test_live_id):
    ok, resp, code = require_role(["student"])
    if not ok:
        return resp, code
    sid = session.get("user_id")
    r = db.responses.find_one(
        {"test_live_id": test_live_id, "student_id": sid, "submitted_at": {"$exists": True}}
    )
    if not r:
        return jsonify({"error": "Result not found"}), 404
    tl = db.tests_live.find_one({"_id": oid(test_live_id)})
    test_doc = db.tests_master.find_one({"_id": oid(tl.get("test_id"))}) if tl else None
    if not test_doc or test_doc.get("archived"):
        return jsonify({"error": "Test not found"}), 404
    score, sol_single, sol_multi = score_submission(
        test_doc, r.get("answers_single", []), r.get("answers_multi", [])
    )
    all_res = list(db.responses.find({"test_live_id": test_live_id}))
    scores = [item.get("score", 0) for item in all_res]
    class_avg = round(sum(scores) / len(scores), 2) if scores else 0
    sorted_scores = sorted(
        all_res, key=lambda x: (-x.get("score", 0), x.get("submitted_at", ""))
    )
    rank = next(
        (i + 1 for i, item in enumerate(sorted_scores) if item.get("student_id") == sid),
        None,
    )
    total_marks = max_score(test_doc)
    return jsonify(
        {
            "score": score,
            "max_score": total_marks,
            "rank": rank,
            "total": len(sorted_scores),
            "class_average": class_avg,
            "solutions": {"single": sol_single, "multi": sol_multi},
        }
    )


# -------- Reports / Leaderboards -------- #
def date_filter_query(month: str = None, week: int = None, date_str: str = None):
    if date_str:
        try:
            dt = datetime.fromisoformat(date_str)
            start = datetime(dt.year, dt.month, dt.day)
            end = start + timedelta(days=1)
            return {"$gte": start.isoformat(), "$lt": end.isoformat()}
        except Exception:
            return None
    if month:
        try:
            dt = datetime.strptime(month + "-01", "%Y-%m-%d")
            start = datetime(dt.year, dt.month, 1)
            if dt.month == 12:
                end = datetime(dt.year + 1, 1, 1)
            else:
                end = datetime(dt.year, dt.month + 1, 1)
            if week:
                # simple week buckets inside month
                start = start + timedelta(days=7 * (week - 1))
                end = start + timedelta(days=7)
            return {"$gte": start.isoformat(), "$lt": end.isoformat()}
        except Exception:
            return None
    return None


@app.get("/reports/leaderboard")
def leaderboard():
    role = session.get("role")
    if role == "student":
        # Reuse the student summary for backward compatibility
        return student_ranks()
    if role not in ["admin", "teacher"]:
        return jsonify({"error": "unauthorized"}), 401

    # collect submitted responses
    responses = list(db.responses.find({"submitted_at": {"$exists": True}}))
    if not responses:
        return jsonify({"top": {}, "search": None})

    tests_live_map = {str(t["_id"]): t for t in db.tests_live.find({})}
    tests_map = {str(t["_id"]): t for t in db.tests_master.find({"archived": {"$ne": True}})}
    students_map = {str(s["_id"]): s for s in db.students.find({"archived": {"$ne": True}})}
    teachers_map = {str(t["_id"]): t for t in db.teachers.find({"archived": {"$ne": True}})}

    def allow_response(r):
        tl = tests_live_map.get(r.get("test_live_id"))
        if not tl:
            return False
        test_doc = tests_map.get(tl.get("test_id"))
        if not test_doc:
            return False
        if role == "teacher" and str(test_doc.get("teacher_id")) != session.get("user_id"):
            return False
        stu = students_map.get(r.get("student_id"))
        if not stu:
            return False
        return True

    filtered = [r for r in responses if allow_response(r)]
    if not filtered:
        return jsonify({"top": {}, "search": None})

    def build_top(key_fn, name_fn):
        agg = {}
        for r in filtered:
            key = key_fn(r)
            name = name_fn(r, key)
            if not key or name is None:
                continue
            st = agg.setdefault(key, {"name": name, "score_sum": 0, "count": 0})
            st["score_sum"] += r.get("score", 0)
            st["count"] += 1
        rows = []
        for key, st in agg.items():
            avg = st["score_sum"] / st["count"] if st["count"] else 0
            rows.append({"id": key, "name": st["name"], "avg": round(avg, 2), "tests": st["count"]})
        rows.sort(key=lambda x: (-x["avg"], -x["tests"], x["name"]))
        return rows[:3], rows  # return top3 and full list for search

    def key_school(r):
        stu = students_map.get(r.get("student_id"), {})
        return stu.get("school")

    def key_class(r):
        stu = students_map.get(r.get("student_id"), {})
        return stu.get("class")

    def key_section(r):
        stu = students_map.get(r.get("student_id"), {})
        return f"{stu.get('class')}-{stu.get('section')}"

    def key_student(r):
        return r.get("student_id")

    def key_teacher(r):
        tl = tests_live_map.get(r.get("test_live_id"))
        test_doc = tests_map.get(tl.get("test_id")) if tl else None
        return str(test_doc.get("teacher_id")) if test_doc else None

    top_school, all_school = build_top(key_school, lambda r, k: students_map.get(r.get("student_id"), {}).get("school"))
    top_class, all_class = build_top(key_class, lambda r, k: k)
    top_section, all_section = build_top(key_section, lambda r, k: k)
    top_student, all_student = build_top(key_student, lambda r, k: students_map.get(k, {}).get("name", "Student"))
    top_teacher, all_teacher = build_top(key_teacher, lambda r, k: teachers_map.get(k, {}).get("name", "Teacher"))

    search_q = (request.args.get("q") or "").strip().lower()
    search_res = None
    if search_q:
        def find_rank(rows, key):
            for idx, item in enumerate(rows, 1):
                if search_q in (item.get("name", "").lower()):
                    return {"type": key, "rank": idx, "total": len(rows), **item}
            return None

        search_res = (
            find_rank(all_student, "student")
            or find_rank(all_teacher, "teacher")
            or find_rank(all_class, "class")
            or find_rank(all_section, "section")
            or find_rank(all_school, "school")
        )

    return jsonify(
        {
            "top": {
                "school": top_school,
                "class": top_class,
                "section": top_section,
                "student": top_student,
                "teacher": top_teacher,
            },
            "search": search_res,
        }
    )


def _aggregate_student_avgs(responses: list, students_map: dict, student_id: str):
    stats = {}
    for r in responses:
        sid = r.get("student_id")
        if sid not in students_map:
            continue
        st = stats.setdefault(sid, {"score_sum": 0, "count": 0})
        st["score_sum"] += r.get("score", 0)
        st["count"] += 1
    rows = []
    for sid, st in stats.items():
        if st["count"] == 0:
            continue
        avg = st["score_sum"] / st["count"]
        rows.append((sid, avg, st["count"]))
    rows.sort(
        key=lambda x: (
            -x[1],
            -x[2],
            students_map.get(x[0], {}).get("name", ""),
        )
    )
    total = len(rows)
    scope_avg = round(sum(r[1] for r in rows) / total, 2) if total else 0
    top_avg = round(rows[0][1], 2) if rows else 0
    rank = None
    my_avg = None
    for idx, (sid, avg, _) in enumerate(rows, 1):
        if sid == student_id:
            rank = idx
            my_avg = avg
            break
    return rows, rank, my_avg, total, scope_avg, top_avg


def _rank_delta(responses_scope: list, students_map: dict, student_id: str, latest_ts: str):
    """Compute delta as previous rank (before latest submission) minus current rank."""
    rows, curr_rank, _, _, _, _ = _aggregate_student_avgs(responses_scope, students_map, student_id)
    if curr_rank is None:
        return None, curr_rank
    # remove student's latest submissions to simulate previous state
    prev_scope = []
    removed_any = False
    for r in responses_scope:
        if r.get("student_id") == student_id and r.get("submitted_at") == latest_ts:
            removed_any = True
            continue
        prev_scope.append(r)
    if not removed_any:
        return None, curr_rank
    _, prev_rank, _, _, _, _ = _aggregate_student_avgs(prev_scope, students_map, student_id)
    if prev_rank is None:
        return None, curr_rank
    return prev_rank - curr_rank, curr_rank


@app.get("/student/ranks")
def student_ranks():
    ok, resp, code = require_role(["student"])
    if not ok:
        return resp, code
    sid = session.get("user_id")
    # load maps with minimal fields to speed up rendering
    students_map = {
        str(s["_id"]): s
        for s in db.students.find(
            {"archived": {"$ne": True}},
            {"name": 1, "school": 1, "class": 1, "section": 1},
        )
    }
    me = students_map.get(sid)
    if not me:
        return jsonify({"error": "student_not_found"}), 404

    # pull only needed fields from responses to reduce payload
    responses_raw = list(
        db.responses.find(
            {"submitted_at": {"$exists": True}},
            {"student_id": 1, "score": 1, "submitted_at": 1, "test_live_id": 1},
        )
    )

    # prefetch tests_live and tests_master once to avoid per-row queries
    live_ids = {r.get("test_live_id") for r in responses_raw if r.get("test_live_id")}
    live_oid_list = [oid(x) for x in live_ids if oid(x)]
    tests_live_map = {
        str(t["_id"]): t
        for t in db.tests_live.find(
            {"_id": {"$in": live_oid_list}}, {"test_id": 1, "class": 1, "section": 1}
        )
    }
    test_ids = {tl.get("test_id") for tl in tests_live_map.values() if tl.get("test_id")}
    test_oid_list = [oid(x) for x in test_ids if oid(x)]
    tests_map = {
        str(t["_id"]): t
        for t in db.tests_master.find(
            {"_id": {"$in": test_oid_list}, "archived": {"$ne": True}}
        )
    }

    responses = []
    my_responses = []
    for r in responses_raw:
        stid = r.get("student_id")
        if stid not in students_map:
            continue
        tl = tests_live_map.get(r.get("test_live_id"))
        test_doc = tests_map.get(tl.get("test_id")) if tl else None
        if not test_doc:
            continue
        r["_test_id"] = str(test_doc["_id"])
        responses.append(r)
        if stid == sid:
            my_responses.append(r)
    if not my_responses:
        latest_ts = None
    else:
        latest_ts = max(r.get("submitted_at", "") for r in my_responses)

    # scope filters
    def scope_filter(predicate):
        return [r for r in responses if predicate(r)]

    # precompute student attributes for filters
    def matches_school(r):
        stu = students_map.get(r.get("student_id"), {})
        return stu.get("school") == me.get("school")

    def matches_class(r):
        stu = students_map.get(r.get("student_id"), {})
        return stu.get("class") == me.get("class")

    def matches_section(r):
        stu = students_map.get(r.get("student_id"), {})
        return stu.get("class") == me.get("class") and stu.get("section") == me.get("section")

    scopes = {
        "overall": responses,
        "school": scope_filter(matches_school),
        "class": scope_filter(matches_class),
        "section": scope_filter(matches_section),
    }

    payload = {}
    for name, res_list in scopes.items():
        rows, curr_rank, my_avg, total, scope_avg, top_avg = _aggregate_student_avgs(
            res_list, students_map, sid
        )
        delta = None
        if latest_ts:
            delta, curr_rank = _rank_delta(res_list, students_map, sid, latest_ts)
        payload[name] = {
            "rank": curr_rank,
            "total": total,
            "average": round(my_avg, 2) if my_avg is not None else None,
            "scope_average": scope_avg,
            "top_average": top_avg,
            "delta": delta,
        }

    # Group standings: how the student's school/class/section rank overall
    def _group_rank(responses_list, students_lookup, target_key_fn):
        agg = {}
        for r in responses_list:
            stu = students_lookup.get(r.get("student_id"), {})
            key = target_key_fn(stu)
            if not key:
                continue
            st = agg.setdefault(key, {"score_sum": 0, "count": 0})
            st["score_sum"] += r.get("score", 0)
            st["count"] += 1
        rows = []
        for key, st in agg.items():
            if st["count"] == 0:
                continue
            rows.append((key, st["score_sum"] / st["count"], st["count"]))
        rows.sort(key=lambda x: (-x[1], -x[2], x[0]))
        total = len(rows)
        rank = None
        for idx, (key, _, _) in enumerate(rows, 1):
            if key == target_key_fn(students_lookup.get(sid, {})):
                rank = idx
                break
        return {"rank": rank, "total": total}

    payload["relative_positions"] = {
        "school": _group_rank(responses, students_map, lambda stu: stu.get("school")),
        "class": _group_rank(responses, students_map, lambda stu: stu.get("class")),
        "section": _group_rank(
            responses,
            students_map,
            lambda stu: f"{stu.get('class')}-{stu.get('section')}" if stu.get("class") and stu.get("section") else None,
        ),
    }

    my_avg = (
        round(sum(r.get("score", 0) for r in my_responses) / len(my_responses), 2)
        if my_responses
        else None
    )
    my_tests = []
    for r in sorted(my_responses, key=lambda x: x.get("submitted_at", ""), reverse=True):
        tl = db.tests_live.find_one({"_id": oid(r.get("test_live_id"))})
        test_doc = tests_map.get(tl.get("test_id")) if tl else None
        if not test_doc:
            continue
        my_tests.append(
            {
                "test_live_id": r.get("test_live_id"),
                "title": test_doc.get("title"),
                "score": r.get("score", 0),
                "max_score": max_score(test_doc),
                "submitted_at": r.get("submitted_at"),
            }
        )

    payload["my_average"] = my_avg
    payload["my_tests"] = my_tests
    payload["summary_table"] = [
        {
            "level": "Overall",
            "rank": payload["overall"]["rank"],
            "total": payload["overall"]["total"],
            "delta": payload["overall"]["delta"],
            "group_average": payload["overall"]["scope_average"],
            "my_average": payload["overall"]["average"],
        },
        {
            "level": "School",
            "rank": payload["school"]["rank"],
            "total": payload["school"]["total"],
            "delta": payload["school"]["delta"],
            "group_average": payload["school"]["scope_average"],
            "my_average": payload["school"]["average"],
        },
        {
            "level": "Class",
            "rank": payload["class"]["rank"],
            "total": payload["class"]["total"],
            "delta": payload["class"]["delta"],
            "group_average": payload["class"]["scope_average"],
            "my_average": payload["class"]["average"],
        },
        {
            "level": "Section",
            "rank": payload["section"]["rank"],
            "total": payload["section"]["total"],
            "delta": payload["section"]["delta"],
            "group_average": payload["section"]["scope_average"],
            "my_average": payload["section"]["average"],
        },
    ]
    return jsonify(payload)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
