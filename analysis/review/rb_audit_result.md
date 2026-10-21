# Read-back label audit (review round 1)

**Sample.** There are 231 exposed episodes in the released trace sample (`traces/sample.tar.gz`: four tasks plus the cited cases). From these, the 60 audited episodes were drawn at random (seed 7): 30 with the automatic label RB = true and 30 with RB = false. The draw covers:
- policies B0, B1, B2, VRV and its ablations, and Oracle;
- conditions C0 and F1–F6;
- tasks reddit-404 (upvote), reddit-620 (new post), shopping_admin-453 (disable a product) and shopping_admin-458 (change a price).

**Procedure.** `scripts/rb_audit_dump.py` lists, for each episode, every step after the first matched task write that loaded a document or reported a verification. Each listed step shows the agent's action and the documents it loaded, and marks redirects that follow a write in the same step. One author (the reviewer) labelled whether the agent itself loaded, by a new request after the write, a page that displays the written state. Site redirects do not count. Opening the item's edit form, its forum listing or the user's submissions page does count. The labelling was done without the automatic label as input; it was compared afterwards.

**Pages that display the state, by task:**
- upvote: the forum listing `/f/books/new`;
- new post: the post page, the forum listing, or the user's submissions page;
- product tasks: the product edit page or the product grid.

| | manual RB = true | manual RB = false |
|---|---|---|
| automatic RB = true (30) | 30 | 0 |
| automatic RB = false (30) | 0 (1 ambiguous) | 29 (+1 ambiguous) |

**Agreement:** 59/60 clear agreements and 1 ambiguous case.
- The ambiguous case is #35, reddit-620 with Qwen3.7-Plus under B0 F5. After the error, the agent clicked the home link, which loaded the forum front page, and then claimed success. The front page may or may not list the new post; the catalog does not count it as a read-back view.
- Counting the ambiguous case as a disagreement gives 59/60 = 98% agreement and Cohen's κ = 0.97.

**Patterns the audit confirms:**
- "Verification" under VRV-samepage and VRV-modeltrigger is often a report about the current page without a reload, as in #13, #18, #33 and #41. These are correctly labelled RB = false.
- After a lost acknowledgement, agents that navigate back often reload the *submission form* or the *save URL*, which returns a 404. Neither displays the state, as in #26, #29, #36, #43, #44 and #52. Both are correctly not counted on their own.
- After a write, B0 agents almost always send DONE directly from the redirect page.

**Limitations.**
- A single annotator labelled the episodes.
- The sample covers 4 of the 64 tasks, plus cited cases.
- The labels are based on URLs and recorded navigation, not on rendered page content.
