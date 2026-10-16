
from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from pathlib import Path

app = Flask(__name__)
app.secret_key = "change-this-before-deployment"

DB_PATH = Path(__file__).with_name("expenses.db")


def get_db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                amount REAL NOT NULL CHECK(amount > 0),
                payer_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (payer_id) REFERENCES members(id)
            );

            CREATE TABLE IF NOT EXISTS expense_splits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                share REAL NOT NULL,
                FOREIGN KEY (expense_id)
                    REFERENCES expenses(id) ON DELETE CASCADE,
                FOREIGN KEY (member_id) REFERENCES members(id),
                UNIQUE (expense_id, member_id)
            );
        """)


def get_dashboard_data(db):
    members = db.execute(
        "SELECT * FROM members ORDER BY name"
    ).fetchall()

    expenses = db.execute("""
        SELECT e.id, e.description, e.amount, e.created_at,
               m.name AS payer
        FROM expenses e
        JOIN members m ON e.payer_id = m.id
        ORDER BY e.id DESC
    """).fetchall()

    balances = {member["id"]: 0.0 for member in members}

    for expense in db.execute(
        "SELECT id, amount, payer_id FROM expenses"
    ).fetchall():
        balances[expense["payer_id"]] += expense["amount"]

    for split in db.execute(
        "SELECT member_id, share FROM expense_splits"
    ).fetchall():
        balances[split["member_id"]] -= split["share"]

    balance_list = []
    for member in members:
        balance_list.append({
            "id": member["id"],
            "name": member["name"],
            "balance": round(balances[member["id"]], 2)
        })

    total = db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM expenses"
    ).fetchone()[0]

    creditors = [
        {"name": b["name"], "amount": b["balance"]}
        for b in balance_list if b["balance"] > 0.005
    ]
    debtors = [
        {"name": b["name"], "amount": -b["balance"]}
        for b in balance_list if b["balance"] < -0.005
    ]

    settlements = []
    i = j = 0

    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i]["amount"], creditors[j]["amount"])

        settlements.append({
            "from": debtors[i]["name"],
            "to": creditors[j]["name"],
            "amount": round(amount, 2)
        })

        debtors[i]["amount"] -= amount
        creditors[j]["amount"] -= amount

        if debtors[i]["amount"] < 0.005:
            i += 1
        if creditors[j]["amount"] < 0.005:
            j += 1

    return members, expenses, balance_list, round(total, 2), settlements


@app.route("/")
def index():
    with get_db() as db:
        members, expenses, balances, total, settlements = (
            get_dashboard_data(db)
        )

    return render_template(
        "index.html",
        members=members,
        expenses=expenses,
        balances=balances,
        total=total,
        settlements=settlements
    )


@app.route("/members", methods=["POST"])
def add_members():
    names_text = request.form.get("names", "")
    names = list(dict.fromkeys(
        name.strip() for name in names_text.split(",") if name.strip()
    ))

    if not names:
        flash("Enter at least one member name.", "error")
        return redirect(url_for("index"))

    try:
        with get_db() as db:
            for name in names:
                db.execute(
                    "INSERT OR IGNORE INTO members (name) VALUES (?)",
                    (name,)
                )
        flash("Members saved successfully.", "success")
    except sqlite3.Error:
        flash("Could not save members. Please try again.", "error")

    return redirect(url_for("index"))


@app.route("/expenses", methods=["POST"])
def add_expense():
    description = request.form.get("description", "").strip()
    payer_id = request.form.get("payer_id", type=int)
    participant_ids = list(dict.fromkeys(
        request.form.getlist("participants", type=int)
    ))

    try:
        amount = round(float(request.form.get("amount", "")), 2)
    except (ValueError, TypeError):
        amount = 0

    if not description or len(description) > 100:
        flash("Enter a description of up to 100 characters.", "error")
        return redirect(url_for("index"))

    if amount <= 0 or not participant_ids or not payer_id:
        flash("Enter a valid amount, payer, and participants.", "error")
        return redirect(url_for("index"))

    with get_db() as db:
        valid_ids = {
            row["id"] for row in db.execute(
                "SELECT id FROM members"
            ).fetchall()
        }

        if payer_id not in valid_ids or not set(participant_ids).issubset(valid_ids):
            flash("Invalid member selection.", "error")
            return redirect(url_for("index"))

        cursor = db.execute("""
            INSERT INTO expenses (description, amount, payer_id)
            VALUES (?, ?, ?)
        """, (description, amount, payer_id))

        expense_id = cursor.lastrowid
        share = round(amount / len(participant_ids), 2)
        shares = [share] * len(participant_ids)
        shares[-1] = round(amount - sum(shares[:-1]), 2)

        for member_id, member_share in zip(participant_ids, shares):
            db.execute("""
                INSERT INTO expense_splits (expense_id, member_id, share)
                VALUES (?, ?, ?)
            """, (expense_id, member_id, member_share))

    flash("Expense added successfully.", "success")
    return redirect(url_for("index"))


@app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
def delete_expense(expense_id):
    with get_db() as db:
        db.execute(
            "DELETE FROM expenses WHERE id = ?",
            (expense_id,)
        )

    flash("Expense deleted.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
