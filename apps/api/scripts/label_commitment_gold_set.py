"""Interactive labeler for the commitment classifier gold set.

Walks through every row in commitment_gold_set.jsonl where `label is null`,
displays the message + role, and prompts for a label (0/1) plus optional
metadata for positives. Saves progress after EVERY label so you can
quit and resume freely.

Usage:
    python apps/api/scripts/label_commitment_gold_set.py

Keys:
    1     positive (commitment)
    0     negative (not a commitment)
    s     skip (leave label=null)
    q     save and quit
    ?     show the rubric

For positives, you'll be prompted for:
    title:  short summary (default = first 80 chars of message)
    type:   action | delivery | response | meeting (default = action)
    due_at: ISO datetime or blank for "implicit"
"""
import json
import os
import sys

PATH = "apps/api/tests/fixtures/commitment_gold_set.jsonl"

RUBRIC = """
A COMMITMENT (label 1) is a statement where the speaker commits THEMSELVES
or someone else to a future action with a specific or implicit deadline.

  YES: "I'll send you the report by Friday"
  YES: "Luna, follow up with Ray tomorrow"
  YES: "Confirmed for 3pm Thursday"
  YES: "I owe you that doc"

  NO:  "Ray usually sends reports on Fridays"     (third-person description)
  NO:  "I sent the report yesterday"              (past tense)
  NO:  "What if we shipped Friday?"               (hypothetical/question)
  NO:  "Gap 3 is about commitment tracking"      (meta-discussion)
  NO:  "I'm thinking about reviewing the PR"     (intent without commitment)
  NO:  "Maybe tomorrow"                          (hedged)
"""


def load_rows() -> list[dict]:
    if not os.path.exists(PATH):
        sys.stderr.write(f"ERROR: {PATH} not found\n")
        sys.exit(1)
    with open(PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def save_rows(rows: list[dict]) -> None:
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, PATH)


def main() -> int:
    rows = load_rows()
    todo = [(i, r) for i, r in enumerate(rows) if r.get("label") is None]
    total = len(todo)

    if total == 0:
        print("All rows already labeled. Nothing to do.")
        return 0

    print(f"\n{'=' * 70}")
    print(f"  COMMITMENT GOLD SET LABELER")
    print(f"  {total} rows to label out of {len(rows)} total")
    print(f"  Keys: [1]=commitment  [0]=not  [s]=skip  [q]=quit  [?]=rubric")
    print(f"  Progress is saved after EVERY label. Ctrl-C to abort cleanly.")
    print(f"{'=' * 70}\n")

    labeled_this_session = 0
    try:
        for n, (idx, row) in enumerate(todo, start=1):
            print(f"\n── {n}/{total} ──  [role={row.get('role', '?')}]  "
                  f"created_at={row.get('created_at', '?')[:10]}")
            print(f"  TEXT: {row.get('text', '')[:500]}")
            while True:
                ans = input("\n  label (1/0/s/q/?): ").strip().lower()
                if ans == "?":
                    print(RUBRIC)
                    continue
                if ans == "q":
                    save_rows(rows)
                    print(f"\nSaved. Labeled {labeled_this_session} this session. "
                          f"{total - n + 1} remaining. Re-run to continue.")
                    return 0
                if ans == "s":
                    print("  skipped (label still null)")
                    break
                if ans == "0":
                    row["label"] = 0
                    row["title"] = None
                    row["due_at"] = None
                    row["type"] = None
                    row["labeled_by"] = "simon"
                    rows[idx] = row
                    save_rows(rows)
                    labeled_this_session += 1
                    print("  → 0 (not a commitment)")
                    break
                if ans == "1":
                    default_title = row["text"][:80]
                    title = input(f"  title [{default_title}]: ").strip() or default_title
                    typ = input("  type [action]: ").strip() or "action"
                    due_at = input("  due_at ISO (blank for implicit): ").strip() or None
                    row["label"] = 1
                    row["title"] = title
                    row["type"] = typ
                    row["due_at"] = due_at
                    row["labeled_by"] = "simon"
                    rows[idx] = row
                    save_rows(rows)
                    labeled_this_session += 1
                    print(f"  → 1 (commitment) title='{title}' type={typ}")
                    break
                print("  invalid — use 1, 0, s, q, or ?")
    except KeyboardInterrupt:
        save_rows(rows)
        print(f"\n\nAborted. Saved {labeled_this_session} labels. "
              f"Re-run to continue from where you left off.")
        return 0

    save_rows(rows)
    print(f"\n{'=' * 70}")
    print(f"  DONE. Labeled {labeled_this_session} rows.")
    print(f"  All {len(rows)} rows in {PATH} now have a label.")
    print(f"{'=' * 70}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
