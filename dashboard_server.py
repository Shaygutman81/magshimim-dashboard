# -*- coding: utf-8 -*-
"""
Sales War Room – שרת דשבורד מכירות חי
=======================================
הרצה: python dashboard_server.py
פתח: http://localhost:5002
"""
import os, sys, json
from datetime import datetime, date

sys.stdout.reconfigure(encoding='utf-8')

try:
    from flask import Flask, jsonify, send_from_directory, request
    from flask_cors import CORS
    import requests as http
    import anthropic
except ImportError:
    print("נא להריץ: pip install flask flask-cors requests anthropic")
    sys.exit(1)

app = Flask(__name__, static_folder=os.path.dirname(os.path.abspath(__file__)))
CORS(app)

BOARD_ID     = "18409244173"
BOARD_URL    = f"https://cyber798195.monday.com/boards/{BOARD_ID}"
MONDAY_GQL   = "https://api.monday.com/v2"
MONDAY_HDRS  = lambda tok: {"Authorization": tok, "Content-Type": "application/json", "API-Version": "2024-01"}

# ── Column IDs ───────────────────────────────────────────────────────────────
C_MANAGER    = "multiple_person_mm2hrns1"
C_CALL1      = "color_mm3eb8a6"    # שיחה ראשונית
C_CALLDONE   = "color_mm3en084"    # בוצעה שיחה
C_FOLLOWUP   = "color_mm3e1d81"    # מצב מעקב
C_PLANNED    = "boolean_mm3e33d8"  # בתכנון כיתות
C_FU_DATE    = "date_mm3ekdyg"     # תאריך מעקב
C_CALL_DATE  = "date_mm2hs0wy"     # תאריך שיחה
C_PROGRAMS   = "dropdown_mm2k4at5" # תכניות מוצעות
C_ST_HTB     = "color_mm2jenj1"    # סטטוס מדמ"ח חט"ב
C_ST_CS      = "color_mm2jq07z"    # סטטוס מדעי המחשב
C_ST_KADM    = "color_mm2jv4g"     # סטטוס קדם מגשימים
C_ST_MMR     = "color_mm2jraw6"    # סטטוס קדם ממריאות
C_ST_HIND    = "color_mm2j9dd"     # סטטוס הנדסת תוכנה
C_CONTACT    = "board_relation_mm2tfr1e"  # שם איש קשר
C_ROLE       = "lookup_mm2tr48t"          # תפקיד (mirror)
C_PHONE      = "lookup_mm2tp1wh"          # טלפון (mirror)
C_PROG_NOTES = "text_mm33nm6c"            # מילוי לאחר שיח מנהל תכנית
C_EXPOSURE   = "date_mm33ezhk"            # תאריך חשיפה בבית הספר
C_CALL_NOTES = "long_text_mm2hd4ws"       # הערות מהשיחה
C_CLS_HTB    = "numeric_mm3rr0g9"         # כיתות מדמ"ח חט"ב
C_CLS_KADM   = "numeric_mm3rd56z"         # כיתות קדם מגשימים
C_CLS_MMR    = "numeric_mm3r5za5"         # כיתות קדם ממריאות
C_CLS_CS     = "numeric_mm3syahg"         # כיתות מגמת מדעי המחשב (עמודה שנוספה ידנית)

ALL_COLS = [C_MANAGER, C_CALL1, C_CALLDONE, C_FOLLOWUP, C_PLANNED,
            C_FU_DATE, C_CALL_DATE, C_PROGRAMS,
            C_ST_HTB, C_ST_CS, C_ST_KADM, C_ST_MMR, C_ST_HIND,
            C_CONTACT, C_ROLE, C_PHONE, C_PROG_NOTES, C_EXPOSURE, C_CALL_NOTES,
            C_CLS_HTB, C_CLS_KADM, C_CLS_MMR, C_CLS_CS]

PROG_COLS = {
    'קדם מגשימים':        C_ST_KADM,
    'קדם ממריאות':        C_ST_MMR,
    'מגמת מדעי המחשב':   C_ST_CS,   # עמודת סטטוס נפרדת
    'מדמ"ח חט"ב':         C_ST_HTB,
}

PROG_CLASS_COLS = {
    'קדם מגשימים':       C_CLS_KADM,
    'קדם ממריאות':       C_CLS_MMR,
    'מדמ"ח חט"ב':        C_CLS_HTB,
    'מגמת מדעי המחשב':   C_CLS_CS,
}

# ── Monday fetch ─────────────────────────────────────────────────────────────
def _gql(token, query):
    r = http.post(MONDAY_GQL, json={"query": query}, headers=MONDAY_HDRS(token), timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_all_items(token):
    cols_str = " ".join(f'"{c}"' for c in ALL_COLS)
    item_fields = f'id name column_values(ids: [{cols_str}]) {{ id text value }}'

    first_q = f"""query {{
      boards(ids:[{BOARD_ID}]) {{
        items_page(limit:200) {{
          cursor
          items {{ {item_fields} }}
        }}
      }}
    }}"""

    data = _gql(token, first_q)
    page = data["data"]["boards"][0]["items_page"]
    items = list(page["items"])
    cursor = page.get("cursor")

    while cursor:
        next_q = f"""query {{
          next_items_page(limit:200, cursor:"{cursor}") {{
            cursor
            items {{ {item_fields} }}
          }}
        }}"""
        page = _gql(token, next_q)["data"]["next_items_page"]
        items += page["items"]
        cursor = page.get("cursor")

    return items

# ── Parse ─────────────────────────────────────────────────────────────────────
def parse(items):
    today = date.today()

    stats = dict(total=len(items), contacted=0, call_done=0,
                 interested=0, approved=0, not_interested=0,
                 waiting=0, followup_done=0,
                 planned_total=0, planned_not_contacted=0)

    alerts = dict(overdue_followup=[], planned_not_contacted=[], waiting_7days=[], not_interested=[],
                  waiting_answer=[], future_followup=[])
    top_notes = []   # הערות מהשיחה לתובנות AI
    managers = {}
    PROG_STATUSES = ["בוצעה שיחה", "תואמה פגישה", "התקיימה פגישה", "בית ספר מעוניין", "פעיל"]
    programs = {n: dict(proposed=0, interested=0, approved=0, waiting=0, classes=0,
                        statuses={s: 0 for s in PROG_STATUSES}) for n in PROG_COLS}
    by_program = {n: {"waiting": [], "future_followup": []} for n in PROG_COLS}
    by_program["אחר"] = {"waiting": [], "future_followup": []}  # בתי ספר ללא תכנית מוגדרת

    for item in items:
        cvs = {cv["id"]: cv for cv in item.get("column_values", [])}

        def txt(cid):  return (cvs.get(cid) or {}).get("text",  "") or ""
        def val(cid):  return (cvs.get(cid) or {}).get("value", "") or ""

        iid  = item["id"]
        name = item["name"]
        url  = f"{BOARD_URL}/pulses/{iid}"

        call1      = txt(C_CALL1)
        calldone   = txt(C_CALLDONE)
        fu_status  = txt(C_FOLLOWUP)
        planned    = "true" in val(C_PLANNED).lower()
        fu_date    = txt(C_FU_DATE)
        call_date  = txt(C_CALL_DATE)
        prog_notes = txt(C_PROG_NOTES)
        exposure   = txt(C_EXPOSURE)
        call_notes = txt(C_CALL_NOTES)
        if call_notes and len(top_notes) < 3:
            top_notes.append({"school": name, "note": call_notes[:200]})
        manager   = txt(C_MANAGER).strip() or "לא משוייך"

        # ── counts ──
        if planned:
            stats["planned_total"] += 1

        is_contacted = (call1 == "כן")
        if is_contacted:
            stats["contacted"] += 1

        if planned and not is_contacted:
            stats["planned_not_contacted"] += 1
            alerts["planned_not_contacted"].append({"name": name, "url": url})

        if calldone and calldone != "ממתין":
            stats["call_done"] += 1

        if "יש עניין" in calldone:
            stats["interested"] += 1

        if   fu_status == "אישר ✓":        stats["approved"] += 1
        elif fu_status == "לא מעוניין":
            stats["not_interested"] += 1
            prog_text = txt(C_PROGRAMS)
            progs = [p for p in PROG_COLS if p in prog_text]
            alerts["not_interested"].append({"name": name, "url": url,
                                             "programs": ", ".join(progs) if progs else ""})
        elif fu_status == "ממתין לתשובה":
            stats["waiting"] += 1
            prog_text = txt(C_PROGRAMS)
            progs = [p for p in PROG_COLS if p in prog_text]
            alerts["waiting_answer"].append({"name": name, "url": url, "date": call_date,
                                             "programs": ", ".join(progs) if progs else ""})
        elif fu_status == "בוצע פולו-אפ":  stats["followup_done"] += 1

        # ── future followup ──
        if fu_date and fu_status not in ("אישר ✓", "לא מעוניין", ""):
            try:
                fd = date.fromisoformat(fu_date)
                if fd >= today:
                    prog_text = txt(C_PROGRAMS)
                    progs = [p for p in PROG_COLS if p in prog_text]
                    alerts["future_followup"].append(
                        {"name": name, "url": url, "date": fu_date, "days": (fd - today).days,
                         "programs": ", ".join(progs) if progs else ""})
            except: pass

        # ── alerts ──
        if fu_date and fu_status not in ("אישר ✓", "לא מעוניין", ""):
            try:
                fd = date.fromisoformat(fu_date)
                if fd < today:
                    alerts["overdue_followup"].append(
                        {"name": name, "url": url, "days": (today - fd).days, "date": fu_date})
            except: pass

        if fu_status == "ממתין לתשובה" and call_date:
            try:
                cd = date.fromisoformat(call_date)
                days = (today - cd).days
                if days >= 7:
                    alerts["waiting_7days"].append(
                        {"name": name, "url": url, "days": days, "date": call_date})
            except: pass

        # ── manager ──
        if manager not in managers:
            managers[manager] = dict(name=manager, total=0, contacted=0, interested=0, approved=0)
        m = managers[manager]
        m["total"] += 1
        if is_contacted:         m["contacted"]  += 1
        if "יש עניין" in calldone: m["interested"] += 1
        if fu_status == "אישר ✓":  m["approved"]   += 1

        # ── programs ──
        prog_text = txt(C_PROGRAMS)
        school_progs = [p for p in PROG_COLS if p in prog_text]

        for prog_name, col_id in PROG_COLS.items():
            st = txt(col_id)
            if st:
                programs[prog_name]["proposed"] += 1
                if any(w in st for w in ("מעוניין", "פעיל", "פגישה")):
                    programs[prog_name]["interested"] += 1
                if "אישר" in st:
                    programs[prog_name]["approved"] += 1
                if "ממתינ" in st:
                    programs[prog_name]["waiting"] += 1

        # ── ספירת כיתות לכל תכנית ──
        for prog_name, cls_col in PROG_CLASS_COLS.items():
            cv_obj = cvs.get(cls_col)
            if cv_obj:
                raw = cv_obj.get("text") or cv_obj.get("value") or ""
                if raw:
                    try:
                        # value field מגיע כ-"\"3\"" — מנקה גרשיים
                        clean = str(raw).strip().strip('"')
                        programs[prog_name]["classes"] += int(float(clean))
                    except: pass

        # ── by_program (waiting) ──
        added_to_any = False
        for prog_name, col_id in PROG_COLS.items():
            prog_st = txt(col_id)
            if "ממתינ" in prog_st:
                cls_col = PROG_CLASS_COLS.get(prog_name)
                cls_count = 0
                if cls_col:
                    try: cls_count = int(float(txt(cls_col) or 0))
                    except: pass
                entry = {"name": name, "url": url, "fu_date": fu_date or "", "classes": cls_count}
                by_program[prog_name]["waiting"].append(entry)
                added_to_any = True

        # בית ספר עם "ממתין לתשובה" גלובלי שלא שויך לאף תכנית → "אחר"
        if not added_to_any and fu_status == "ממתין לתשובה":
            by_program["אחר"]["waiting"].append(
                {"name": name, "url": url, "fu_date": fu_date or "", "classes": 0})

        # ── future followup ──
        if fu_date and fu_status not in ("אישר ✓", "לא מעוניין", ""):
            try:
                fd = date.fromisoformat(fu_date)
                if fd >= today:
                    fp_progs = school_progs if school_progs else ["אחר"]
                    for pname in fp_progs:
                        by_program[pname]["future_followup"].append(
                            {"name": name, "url": url, "date": fu_date, "days": (fd - today).days})
            except: pass

    # ── sort alerts ──
    for k in ("overdue_followup", "waiting_7days"):
        alerts[k].sort(key=lambda x: x["days"], reverse=True)
    alerts["planned_not_contacted"] = alerts["planned_not_contacted"][:20]
    alerts["overdue_followup"]       = alerts["overdue_followup"][:20]
    alerts["waiting_7days"]          = alerts["waiting_7days"][:20]
    alerts["not_interested"]         = sorted(alerts["not_interested"],  key=lambda x: x["name"])
    alerts["waiting_answer"]         = sorted(alerts["waiting_answer"],  key=lambda x: x["programs"], reverse=True)
    alerts["future_followup"]        = sorted(alerts["future_followup"], key=lambda x: x["programs"], reverse=True)

    # ── manager list ──
    mgr_list = []
    for m in managers.values():
        if m["total"] == 0: continue
        cr = m["contacted"]  / m["total"]        * 100
        ir = m["interested"] / max(m["contacted"], 1) * 100
        score = round(cr * 0.4 + ir * 0.6)
        mgr_list.append({**m,
            "contacted_pct": round(cr), "interest_rate": round(ir), "score": score})
    mgr_list.sort(key=lambda x: x["score"], reverse=True)

    # ── funnel ──
    t = stats["total"]
    c = stats["contacted"]
    d = stats["call_done"]
    w = stats["waiting"] + stats["followup_done"]
    i = stats["interested"]
    a = stats["approved"]
    funnel = [
        {"stage": 'סה"כ בתי ספר',  "count": t, "pct_abs": 100,              "pct_prev": 100,              "color": "#035596"},
        {"stage": "נפנינו",          "count": c, "pct_abs": pct(c, t),        "pct_prev": pct(c, t),        "color": "#1B75BC"},
        {"stage": "שיחה בוצעה",      "count": d, "pct_abs": pct(d, t),        "pct_prev": pct(d, c),        "color": "#5EC8DA"},
        {"stage": "ממתין / פולו-אפ", "count": w, "pct_abs": pct(w, t),        "pct_prev": pct(w, d),        "color": "#fdab3d"},
        {"stage": "יש עניין",        "count": i, "pct_abs": pct(i, t),        "pct_prev": pct(i, d),        "color": "#9d50dd"},
        {"stage": "אישרו ✓",         "count": a, "pct_abs": pct(a, t),        "pct_prev": pct(a, i),        "color": "#00c875"},
    ]

    prog_list = [{"name": k, **v} for k, v in programs.items()]
    by_prog_list = [{"name": k,
                     "waiting": sorted(v["waiting"], key=lambda x: x["fu_date"])}
                    for k, v in by_program.items()
                    if v["waiting"]]

    return dict(stats=stats, funnel=funnel, alerts=alerts,
                managers=mgr_list, programs=prog_list,
                by_program=by_prog_list, top_notes=top_notes,
                last_updated=datetime.now().strftime("%d/%m/%Y %H:%M"))

def pct(a, b): return round(a / b * 100) if b else 0

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")

@app.route("/api/data")
def api_data():
    token = request.headers.get("X-Monday-Token", "") or os.environ.get("MONDAY_API_TOKEN", "")
    if not token:
        return jsonify({"error": "Monday API token לא סופק"}), 401
    try:
        items = fetch_all_items(token)
        return jsonify(parse(items))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/insight", methods=["POST"])
def api_insight():
    req  = request.json or {}
    ant_key = req.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not ant_key:
        return jsonify({"error": "ANTHROPIC_API_KEY לא סופק"}), 400

    d        = req.get("data", {})
    stats    = d.get("stats", {})
    managers = d.get("managers", [])
    programs = d.get("programs", [])
    alerts   = d.get("alerts", {})
    notes    = d.get("top_notes", [])

    top  = managers[0]["name"] if managers else "?"
    t, c, i, a = stats.get("total",0), stats.get("contacted",0), stats.get("interested",0), stats.get("approved",0)
    od   = len(alerts.get("overdue_followup", []))
    pnc  = stats.get("planned_not_contacted", 0)

    notes_text = ""
    if notes:
        notes_lines = "\n".join(f'  • {n["school"]}: {n["note"]}' for n in notes[:3])
        notes_text = f"\n\nהערות שיח מהשטח (3 הראשונות):\n{notes_lines}"

    prompt = f"""אתה מנתח מכירות בכיר של עמותת מגשימים.
נתוני הפייפליין הנוכחי:
• סה"כ בתי ספר: {t}
• פנינו: {c} ({pct(c,t)}%)  |  שיחה בוצעה: {stats.get('call_done',0)}
• יש עניין: {i}  |  אישרו: {a}
• בתכנון כיתות שטרם פנינו: {pnc} ← דחוף
• פולו-אפ שעבר תאריך: {od}
• מנהל מוביל: {top}
תכניות: {', '.join(f"{p['name']} ({p['proposed']} הוצע, {p['interested']} מעוניין)" for p in programs)}{notes_text}

כתוב בדיוק 3 תובנות ניהוליות קצרות בעברית, ממוספרות 1–3.
כל תובנה: משפט אחד, ישיר ועסקי.
התייחס לנתונים ולהערות מהשטח אם יש."""

    try:
        client = anthropic.Anthropic(api_key=ant_key)
        msg = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=350,
            messages=[{"role": "user", "content": prompt}])
        return jsonify({"insight": msg.content[0].text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Sync: sales board → cooperation board ────────────────────────────────────
COOP_BOARD_ID  = "8113606849"
COOP_GROUP_ID  = "1736251401__1736251001_mkkzwjtj"
C_TRANSFERRED  = "color_mm3eh84f"   # הועבר לשיתוף פעולה (sales board)
C_SALE_YEAR    = "status_mkmtbnd3"  # שנת המכירה (coop board)
C_SALE_STATUS  = "color_mkkkk3tw"   # סטטוס מכירה  (coop board)
C_PROG_COL     = "color_mkkkre63"   # תכנית         (coop board)

# label names in cooperation board's תכנית column (safer than indices)
PROG_LABEL_MAP = {
    'מדמ"ח חט"ב':  'מדמ"ח חט"ב',
    'מדמ"ח תיכון': 'מדמ"ח חט"ב',      # אין תיכון בבורד שיתוף פעולה — ממפים לחט"ב
    'קדם מגשימים': 'קדם מגשימים',
    'קדם ממריאות': 'קדם ממריאות',
    'הנדסת תוכנה': 'הנדסת תוכנה EdTec',
}

def _create_coop_item(token, school_name, prog_name, contact_name="", contact_role="", contact_phone=""):
    prog_label = PROG_LABEL_MAP.get(prog_name) if prog_name else None
    col_vals = {
        C_SALE_YEAR:   {"label": "תשפ''ז"},
        C_SALE_STATUS: {"label": "שיחה ראשונית"},
    }
    if prog_label:
        col_vals[C_PROG_COL] = {"label": prog_label}
    if contact_name or contact_role:
        parts = [p for p in [contact_name, contact_role] if p]
        col_vals["text_mknngmxx"] = " - ".join(parts)
    if contact_phone:
        phone_clean = contact_phone.strip().replace("-", "").replace(" ", "")
        col_vals["phone_mknnhhq5"] = {"phone": phone_clean, "countryShortName": "IL"}

    # json.dumps twice: once for the dict, once to safely quote the resulting string for GQL
    col_vals_gql   = json.dumps(json.dumps(col_vals, ensure_ascii=False), ensure_ascii=False)
    school_name_gql = json.dumps(school_name, ensure_ascii=False)

    q = f"""mutation {{
      create_item(
        board_id: {COOP_BOARD_ID},
        group_id: "{COOP_GROUP_ID}",
        item_name: {school_name_gql},
        column_values: {col_vals_gql}
      ) {{ id }}
    }}"""
    return _gql(token, q)

def _mark_transferred(token, item_id):
    col_val_gql = json.dumps(json.dumps({"index": 1}))
    q = f"""mutation {{
      change_column_value(
        board_id: {BOARD_ID},
        item_id: {item_id},
        column_id: "{C_TRANSFERRED}",
        value: {col_val_gql}
      ) {{ id }}
    }}"""
    return _gql(token, q)

@app.route("/api/sync", methods=["POST"])
def api_sync():
    token = request.headers.get("X-Monday-Token", "") or os.environ.get("MONDAY_API_TOKEN", "")
    if not token:
        return jsonify({"error": "Monday API token לא סופק"}), 401

    try:
        items = fetch_all_items(token)

        schools_done  = []
        items_created = 0
        skipped       = 0

        for item in items:
            cvs = {cv["id"]: cv for cv in item.get("column_values", [])}
            def txt(cid): return (cvs.get(cid) or {}).get("text", "") or ""
            def val(cid): return (cvs.get(cid) or {}).get("value", "") or ""

            calldone    = txt(C_CALLDONE)
            transferred = txt(C_TRANSFERRED)
            programs_v  = val(C_PROGRAMS)

            if "יש עניין" not in calldone:
                continue
            if transferred and transferred.strip():
                skipped += 1
                continue

            school_name   = item["name"]
            iid           = item["id"]
            contact_name  = txt(C_CONTACT)
            contact_role  = txt(C_ROLE)
            contact_phone = txt(C_PHONE)

            # parse proposed programs from dropdown text
            prog_text = txt(C_PROGRAMS)
            proposed  = [pname for pname in PROG_COLS if pname in prog_text]
            if not proposed:
                proposed = [None]   # create one item without a specific program

            for pname in proposed:
                _create_coop_item(token, school_name, pname,
                                  contact_name, contact_role, contact_phone)
                items_created += 1

            _mark_transferred(token, iid)
            schools_done.append(school_name)

        return jsonify({
            "synced_schools": len(schools_done),
            "items_created":  items_created,
            "already_synced": skipped,
            "schools":        schools_done,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    print(f"\n🚀 Sales War Room → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
