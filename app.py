
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
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

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

            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER NOT NULL,
                member_id INTEGER NOT NULL,
                PRIMARY KEY (group_id, member_id),
                FOREIGN KEY (group_id)
                    REFERENCES groups(id) ON DELETE CASCADE,
                FOREIGN KEY (member_id)
                    REFERENCES members(id) ON DELETE CASCADE
            );
        """)

        # Add group_id to an existing expenses table.
        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(expenses)"
            ).fetchall()
        }

        if "group_id" not in columns:
            db.execute(
                "ALTER TABLE expenses ADD COLUMN group_id INTEGER "
                "REFERENCES groups(id)"
            )

        # Create the default group if it does not exist.
        db.execute(
            "INSERT OR IGNORE INTO groups (name) VALUES (?)",
            ("My Group",)
        )

        default_group = db.execute(
            "SELECT id FROM groups WHERE name = ?",
            ("My Group",)
        ).fetchone()

        default_group_id = default_group["id"]

        # Move all existing expenses into the default group.
        db.execute("""
            UPDATE expenses
            SET group_id = ?
            WHERE group_id IS NULL
        """, (default_group_id,))

        # Connect existing members to the default group.
        db.execute("""
            INSERT OR IGNORE INTO group_members (group_id, member_id)
            SELECT ?, id FROM members
        """, (default_group_id,))

        # Ensure payers and split participants belong to the
        # group containing their existing expense.
        db.execute("""
            INSERT OR IGNORE INTO group_members (group_id, member_id)
            SELECT DISTINCT group_id, payer_id
            FROM expenses
            WHERE group_id IS NOT NULL
        """)

        db.execute("""
            INSERT OR IGNORE INTO group_members (group_id, member_id)
            SELECT DISTINCT e.group_id, s.member_id
            FROM expenses e
            JOIN expense_splits s ON s.expense_id = e.id
            WHERE e.group_id IS NOT NULL
        """)


def get_groups(db):
    return db.execute(
        "SELECT id, name FROM groups ORDER BY id"
    ).fetchall()


def get_current_group(db, group_id=None):
    groups = get_groups(db)

    if not groups:
        return None

    if group_id is not None:
        for group in groups:
            if group["id"] == group_id:
                return group

    default_group = db.execute(
        "SELECT id, name FROM groups WHERE name = ?",
        ("My Group",)
    ).fetchone()

    return default_group if default_group else groups[0]


def redirect_to_group(group_id):
    return redirect(url_for("index", group_id=group_id))


def get_dashboard_data(db, group_id):
    members = db.execute("""
        SELECT m.id, m.name
        FROM members m
        JOIN group_members gm ON gm.member_id = m.id
        WHERE gm.group_id = ?
        ORDER BY m.name
    """, (group_id,)).fetchall()

    expenses = db.execute("""
        SELECT e.id, e.description, e.amount, e.created_at,
               m.name AS payer
        FROM expenses e
        JOIN members m ON e.payer_id = m.id
        WHERE e.group_id = ?
        ORDER BY e.id DESC
    """, (group_id,)).fetchall()

    balances = {member["id"]: 0.0 for member in members}

    for expense in db.execute("""
        SELECT id, amount, payer_id
        FROM expenses
        WHERE group_id = ?
    """, (group_id,)).fetchall():
        if expense["payer_id"] in balances:
            balances[expense["payer_id"]] += expense["amount"]

    for split in db.execute("""
        SELECT s.member_id, s.share
        FROM expense_splits s
        JOIN expenses e ON e.id = s.expense_id
        WHERE e.group_id = ?
    """, (group_id,)).fetchall():
        if split["member_id"] in balances:
            balances[split["member_id"]] -= split["share"]

    balance_list = [
        {
            "id": member["id"],
            "name": member["name"],
            "balance": round(balances[member["id"]], 2)
        }
        for member in members
    ]

    total = db.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
        WHERE group_id = ?
    """, (group_id,)).fetchone()[0]

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
        amount = min(
            debtors[i]["amount"],
            creditors[j]["amount"]
        )

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

    return (
        members,
        expenses,
        balance_list,
        round(total, 2),
        settlements
    )


@app.route("/")
def index():
    with get_db() as db:
        groups = get_groups(db)

        # The app normally creates the default group on startup.
        current_group = get_current_group(
            db, request.args.get("group_id", type=int)
        )

        if current_group is None:
            flash("No group is available. Restart the application.", "error")
            return render_template(
                "index.html",
                groups=[],
                current_group=None,
                members=[],
                expenses=[],
                balances=[],
                total=0,
                settlements=[]
            )

        members, expenses, balances, total, settlements = (
            get_dashboard_data(db, current_group["id"])
        )

    return render_template(
        "index.html",
        groups=groups,
        current_group=current_group,
        members=members,
        expenses=expenses,
        balances=balances,
        total=total,
        settlements=settlements
    )


@app.route("/groups", methods=["POST"])
def add_group():
    name = request.form.get("group_name", "").strip()

    if not name or len(name) > 60:
        flash("Enter a group name of up to 60 characters.", "error")
        return redirect(url_for("index"))

    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO groups (name) VALUES (?)",
                (name,)
            )
            group = db.execute(
                "SELECT id FROM groups WHERE name = ?",
                (name,)
            ).fetchone()

        flash("Group created successfully.", "success")
        return redirect_to_group(group["id"])

    except sqlite3.IntegrityError:
        flash("A group with that name already exists.", "error")
        return redirect(url_for("index"))


@app.route("/members", methods=["POST"])
def add_members():
    group_id = request.form.get("group_id", type=int)

    with get_db() as db:
        group = get_current_group(db, group_id)

        if group is None:
            flash("Please create a group first.", "error")
            return redirect(url_for("index"))

        group_id = group["id"]
        names_text = request.form.get("names", "")
        names = list(dict.fromkeys(
            name.strip()
            for name in names_text.split(",")
            if name.strip()
        ))

        if not names:
            flash("Enter at least one member name.", "error")
            return redirect_to_group(group_id)

        for name in names:
            db.execute(
                "INSERT OR IGNORE INTO members (name) VALUES (?)",
                (name,)
            )

            member = db.execute(
                "SELECT id FROM members WHERE name = ? COLLATE NOCASE",
                (name,)
            ).fetchone()

            db.execute("""
                INSERT OR IGNORE INTO group_members (group_id, member_id)
                VALUES (?, ?)
            """, (group_id, member["id"]))

    flash("Members saved successfully.", "success")
    return redirect_to_group(group_id)


@app.route("/expenses", methods=["POST"])
def add_expense():
    group_id = request.form.get("group_id", type=int)

    with get_db() as db:
        group = get_current_group(db, group_id)

        if group is None:
            flash("Please create a group first.", "error")
            return redirect(url_for("index"))

        group_id = group["id"]
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
            return redirect_to_group(group_id)

        if amount <= 0 or not participant_ids or not payer_id:
            flash("Enter a valid amount, payer, and participants.", "error")
            return redirect_to_group(group_id)

        valid_ids = {
            row["member_id"]
            for row in db.execute("""
                SELECT member_id
                FROM group_members
                WHERE group_id = ?
            """, (group_id,)).fetchall()
        }

        if (
            payer_id not in valid_ids
            or not set(participant_ids).issubset(valid_ids)
        ):
            flash("Choose members from the selected group.", "error")
            return redirect_to_group(group_id)

        cursor = db.execute("""
            INSERT INTO expenses
                (description, amount, payer_id, group_id)
            VALUES (?, ?, ?, ?)
        """, (description, amount, payer_id, group_id))

        expense_id = cursor.lastrowid
        share = round(amount / len(participant_ids), 2)
        shares = [share] * len(participant_ids)
        shares[-1] = round(amount - sum(shares[:-1]), 2)

        for member_id, member_share in zip(participant_ids, shares):
            db.execute("""
                INSERT INTO expense_splits
                    (expense_id, member_id, share)
                VALUES (?, ?, ?)
            """, (expense_id, member_id, member_share))

    flash("Expense added successfully.", "success")
    return redirect_to_group(group_id)


@app.route("/expenses/<int:expense_id>/delete", methods=["POST"])
def delete_expense(expense_id):
    group_id = request.form.get("group_id", type=int)

    with get_db() as db:
        group = get_current_group(db, group_id)

        if group is None:
            flash("Group not found.", "error")
            return redirect(url_for("index"))

        group_id = group["id"]

        cursor = db.execute("""
            DELETE FROM expenses
            WHERE id = ? AND group_id = ?
        """, (expense_id, group_id))

        if cursor.rowcount:
            flash("Expense deleted.", "success")
        else:
            flash("Expense not found in this group.", "error")

    return redirect_to_group(group_id)


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
