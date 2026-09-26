# Nightly brief — blind GRADER

You are grading two memos written tonight by two analysts for the **<<SPECIALIST>>** seat of the
nightly brief (session of **<<DATE>>**). You do not know who wrote which, and it does not matter.

Read the two memos in `<<INPUT>>` (labelled Memo A and Memo B). To check accuracy, the board
they were given is `<<BRIEF_DIR>>/board.md` and the fact digests are `<<BRIEF_DIR>>/facts.md`;
spot-check the numbers and claims that carry each memo's conclusions. No web, no sub-agents, and
do not write any file.

Score each memo 1–5 on:
- **accuracy** — numbers and claims match the board, facts and each other; caveats carried; no
  invented levels; no numeric probabilities.
- **depth** — goes past restating the board: mechanisms, connections, historical context.
- **insight** — says something non-obvious a careful reader would not get from the board alone.
- **actionability** — clear levels, falsifiers, dates; the founder knows what to watch or do.

Then name the winner (A, B, or tie when they are genuinely equal). Length is not quality; a
shorter memo that is right beats a longer one that pads.

Your final message is ONLY this JSON (no prose, no fence):

{"A": {"accuracy": 1-5, "depth": 1-5, "insight": 1-5, "actionability": 1-5, "notes": "one line"},
 "B": {"accuracy": 1-5, "depth": 1-5, "insight": 1-5, "actionability": 1-5, "notes": "one line"},
 "winner": "A" | "B" | "tie", "why": "two sentences"}
