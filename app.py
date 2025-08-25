import os
import io
import csv
import psycopg2
import psycopg2.extras
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
import pandas as pd
from dotenv import load_dotenv

# ---------- Load .env ----------
load_dotenv()  # loads environment variables from a .env file


# ---------- Config ----------
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
}


app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET")


# Update with your PostgreSQL credentials (for SQLAlchemy queries only)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://username:password@localhost:5432/your_database'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database Model
class Participant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    stake = db.Column(db.String(200), nullable=False)
    ward_branch = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(100), nullable=True)
    tshirt_size = db.Column(db.String(50), nullable=True)

    def __repr__(self):
        return f"<Participant {self.name}>"

# ---------- DB Helpers ----------
def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            stake TEXT NOT NULL,
            ward_branch TEXT NOT NULL,
            email TEXT,
            phone_number TEXT,
            tshirt_size TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS checkins (
            id SERIAL PRIMARY KEY,
            participant_id INT NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
            event_day INT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(participant_id, event_day)
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


init_db()


# ---------- Utilities ----------
def normalize_columns(df):
    colmap = {
        "name": "name",
        "full name": "name",
        "stake": "stake",
        "ward/branch": "ward_branch",
        "ward": "ward_branch",
        "branch": "ward_branch",
        "ward or branch": "ward_branch",
        "email": "email",
        "e-mail": "email",
        "phone": "phone_number",
        "phone number": "phone_number",
        "phone_no": "phone_number",
        "tel": "phone_number",
        "mobile": "phone_number",
        "tshirt": "tshirt_size",
        "t-shirt": "tshirt_size",
        "tshirt size": "tshirt_size",
        "shirt size": "tshirt_size",
    }
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    mapped = {}
    for c in df.columns:
        if c in colmap:
            mapped[c] = colmap[c]
    df = df.rename(columns=mapped)
    needed = ["name", "stake", "ward_branch", "email", "phone_number", "tshirt_size"]
    for need in needed:
        if need not in df.columns:
            df[need] = None
    return df[needed]


def get_checkin_status_map(cur, day):
    cur.execute("SELECT participant_id FROM checkins WHERE event_day = %s", (day,))
    return set(r[0] for r in cur.fetchall())


# ---------- Routes ----------
@app.route("/")
def dashboard():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT COUNT(*) FROM participants")
    total = cur.fetchone()[0]

    by_day = {}
    for d in (1, 2, 3):
        cur.execute("SELECT COUNT(*) FROM checkins WHERE event_day = %s", (d,))
        by_day[d] = cur.fetchone()[0]

    cur.execute("""
        SELECT stake, COUNT(*) AS total
        FROM participants
        GROUP BY stake
        ORDER BY stake ASC
    """)
    stakes = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("dashboard.html", total=total, by_day=by_day, stakes=stakes)


@app.route("/participants")
def participants():
    q = (request.args.get("q") or "").strip().lower()
    stake = (request.args.get("stake") or "").strip()
    ward = (request.args.get("ward") or "").strip()
    day = request.args.get("day")

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    sql = "SELECT * FROM participants WHERE 1=1"
    params = []
    if q:
        sql += " AND (lower(name) LIKE %s OR lower(stake) LIKE %s OR lower(ward_branch) LIKE %s OR phone_number LIKE %s)"
        like = f"%{q}%"
        params.extend([like, like, like, like])
    if stake:
        sql += " AND stake = %s"
        params.append(stake)
    if ward:
        sql += " AND ward_branch = %s"
        params.append(ward)
    sql += " ORDER BY name ASC LIMIT 500"

    cur.execute(sql, params)
    rows = cur.fetchall()

    day1_set = get_checkin_status_map(cur, 1)
    day2_set = get_checkin_status_map(cur, 2)
    day3_set = get_checkin_status_map(cur, 3)

    cur.execute("SELECT DISTINCT stake FROM participants ORDER BY stake")
    stakes = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT ward_branch FROM participants ORDER BY ward_branch")
    wards = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()

    def status_for(pid, dset):
        return pid in dset

    return render_template(
        "participants.html",
        participants=rows,
        stakes=stakes,
        wards=wards,
        q=q,
        selected_stake=stake,
        selected_ward=ward,
        day=day,
        status_for=status_for,
        day1_set=day1_set,
        day2_set=day2_set,
        day3_set=day3_set,
    )


@app.route("/toggle/<int:participant_id>/<int:day>", methods=["POST"])
def toggle_checkin(participant_id, day):
    if day not in (1, 2, 3):
        return "Invalid day", 400

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id FROM checkins WHERE participant_id = %s AND event_day = %s", (participant_id, day))
    existing = cur.fetchone()

    if existing:
        cur.execute("DELETE FROM checkins WHERE id = %s", (existing[0],))
    else:
        cur.execute(
            "INSERT INTO checkins (participant_id, event_day, timestamp) VALUES (%s, %s, %s)",
            (participant_id, day, datetime.utcnow())
        )

    conn.commit()
    cur.close()
    conn.close()

 
 
 
    return redirect(request.referrer or url_for("participants"))






@app.route("/")
def index():
    return render_template("index.html")






# Get distinct stakes
@app.route("/get_stakes")
def get_stakes():
    stakes = db.session.execute("SELECT DISTINCT stake FROM participants ORDER BY stake").fetchall()
    return jsonify([s[0] for s in stakes])


# Get wards for a stake
@app.route("/get_wards/<stake>")
def get_wards(stake):
    wards = db.session.execute(
        "SELECT DISTINCT ward_branch FROM participants WHERE stake = :stake ORDER BY ward_branch",
        {"stake": stake}
    ).fetchall()
    return jsonify([w[0] for w in wards])


# Filter participants by stake + ward
@app.route("/participants/filter", methods=["GET"])
def filter_participants():
    stake = request.args.get("stake")
    ward = request.args.get("ward")
    query = "SELECT * FROM participants WHERE 1=1"
    params = {}

    if stake:
        query += " AND stake = :stake"
        params["stake"] = stake
    if ward:
        query += " AND ward_branch = :ward"
        params["ward"] = ward

    results = db.session.execute(query, params).fetchall()
    return jsonify([dict(row) for row in results])


@app.route("/upload", methods=["GET", "POST"])
def upload():
    message = None
    error = None
    if request.method == "POST":
        file = request.files.get("file")
        mode = request.form.get("mode", "append")
        if not file or file.filename == "":
            error = "Please choose a file (.xlsx or .csv)."
        else:
            try:
                filename = file.filename.lower()
                if filename.endswith(".xlsx"):
                    df = pd.read_excel(file)
                elif filename.endswith(".csv"):
                    content = file.read().decode("utf-8-sig")
                    df = pd.read_csv(io.StringIO(content))
                else:
                    raise ValueError("Unsupported file type")

                df = normalize_columns(df)
                conn = get_conn()
                cur = conn.cursor()

                if mode == "replace":
                    cur.execute("DELETE FROM checkins")
                    cur.execute("DELETE FROM participants")

                inserted = 0
                for _, r in df.iterrows():
                    name = (r["name"] or "").strip()
                    stake = (r["stake"] or "").strip()
                    ward = (r["ward_branch"] or "").strip()
                    email = r["email"] if pd.notna(r["email"]) else None
                    phone = r["phone_number"] if pd.notna(r["phone_number"]) else None
                    tshirt = r["tshirt_size"] if pd.notna(r["tshirt_size"]) else None
                    if name and stake and ward:
                        cur.execute("""
                            INSERT INTO participants (name, stake, ward_branch, email, phone_number, tshirt_size)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (name, stake, ward, email, phone, tshirt))
                        inserted += 1

                conn.commit()
                cur.close()
                conn.close()
                message = f"Uploaded {inserted} participants ({'replaced' if mode=='replace' else 'appended'})."
            except Exception as e:
                error = f"Upload failed: {e}"

    return render_template("upload.html", message=message, error=error)


@app.route("/export")
def export_csv():
    only_day = request.args.get("day")
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT * FROM participants ORDER BY name ASC")
    rows = cur.fetchall()
    day1 = get_checkin_status_map(cur, 1)
    day2 = get_checkin_status_map(cur, 2)
    day3 = get_checkin_status_map(cur, 3)

    output = io.StringIO()
    w = csv.writer(output)
    header = ["id", "name", "stake", "ward_branch", "email", "phone_number", "tshirt_size"]
    if only_day in ("1", "2", "3"):
        header.append(f"day{only_day}")
    else:
        header.extend(["day1", "day2", "day3"])
    w.writerow(header)

    for r in rows:
        base = [r["id"], r["name"], r["stake"], r["ward_branch"], r["email"], r["phone_number"], r["tshirt_size"]]
        if only_day == "1":
            base.append(1 if r["id"] in day1 else 0)
        elif only_day == "2":
            base.append(1 if r["id"] in day2 else 0)
        elif only_day == "3":
            base.append(1 if r["id"] in day3 else 0)
        else:
            base.extend([
                1 if r["id"] in day1 else 0,
                1 if r["id"] in day2 else 0,
                1 if r["id"] in day3 else 0,
            ])
        w.writerow(base)

    cur.close()
    conn.close()

    output.seek(0)
    fname = "participants.csv" if not only_day else f"participants_day{only_day}.csv"
    return send_file(io.BytesIO(output.getvalue().encode("utf-8")),
                     mimetype="text/csv",
                     as_attachment=True,
                     download_name=fname)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)