# How this project works: a guided walkthrough

Read this with the code open. The goal: by the end you can explain every design decision in an
interview *and* change the code yourself. Budget about 2 hours for the first read, then do the
exercises at the bottom. Exercises are what turn "I know what was used" into "I know how to build it".

---

## 1. The big picture in one paragraph

Two small AI pipelines write into one SQLite database, and a dashboard reads it. The **tracker** reads
your emails, decides what each one means for an application (applied? assessment? rejected?), and
updates that application's status. The **matcher** reads job postings, turns each into structured
fields, and scores how well it fits you. The LLM is used in exactly two places, both for *reading
messy text into a fixed structure*. Everything else (scoring, status rules, funnel maths, deduplication)
is ordinary Python and SQL, which makes the system cheap, predictable and testable.

## 2. Ten concepts to be able to explain

Each has a "say it like this" line for interviews.

**1. Structured output.** Instead of asking the model for free text and parsing it, we give it a
"tool" whose input schema *is* our Pydantic model and force it to call that tool. The model's answer
arrives as JSON that matches the schema.
*Say it like this:* "I never parse free text from the model. It fills a schema, and Pydantic validates it."
→ `core/structured.py`

**2. Validation plus one repair round.** Valid JSON can still be wrong: a deadline of "next Friday",
`salary_min > salary_max`. Pydantic catches it, we send the exact error back to the model once, and it
fixes its own output. If it fails twice we raise an error instead of saving bad data.
→ `core/structured.py`, validators in `copilot/tracker/classifier.py` and `copilot/matcher/schema.py`

**3. Rules before the model.** Job alerts, newsletters and OTPs are recognised by sender and subject
with regular expressions, at zero cost and instantly. Only ambiguous emails go to the model.
*Say it like this:* "Use the cheapest method that's reliable for each case. The eval reports how many
emails never needed an LLM call."
→ `rule_classify()` in `copilot/tracker/classifier.py`. Note the comment: a rule matching LinkedIn's
`jobs-noreply@` sender wrongly classified real rejections as alerts, and the eval caught it.

**4. The model reads, code decides.** The fit score is a weighted formula, not "LLM, rate this 1–10".
LLM ratings drift between runs, can't be explained, and cost money every time you re-rank.
→ `copilot/matcher/scoring.py`

**5. A status state machine.** Applications move saved → applied → assessment → interview → offer.
Status only moves forward; rejected, offer and withdrawn are terminal. That's why an old "thanks for
applying" email can't undo an interview.
→ `should_advance()` in `copilot/db.py`

**6. Idempotency.** Running the sync twice gives the same result and the second run costs nothing,
because processed email IDs are stored in the `emails` table. Postings are deduplicated by a content hash.
→ `copilot/tracker/sync.py`, `content_hash()` in `copilot/matcher/extractor.py`

**7. Entity matching.** "Brightpath Analytics Pvt Ltd" (Naukri), "BrightPath Analytics" (the recruiter) and
"brightpath" (your filename) must map to one application. We normalise names (lowercase, strip legal
suffixes), then fall back to fuzzy matching at 85% similarity.
→ `company_key()` and `find_application()` in `copilot/db.py`

**8. Evals.** A golden set of labelled examples plus metrics. The tracker reports category, company,
action and deadline accuracy plus rule coverage. The matcher separates *extraction* accuracy (did the
model read it right?) from *ranking* precision@5 (did the formula put relevant jobs on top?), so you
know which half to fix.
*Say it like this:* "Without an eval, a prompt change is a guess. With one, it's an experiment."
→ `copilot/tracker/eval.py`, `copilot/matcher/eval.py`, `core/evals.py`

**9. Safety and privacy.** Gmail access uses the `gmail.readonly` scope, so the app physically can't send
or delete mail. Email text is untrusted: the prompt says to ignore instructions inside it, and the model
has no tools it could misuse, so the worst a malicious email can do is get mislabelled. One sample email
(`m16`) is a prompt-injection attempt.

**10. Cost control.** A fast, cheap model for classification and extraction; email bodies cut to 3,000
characters with quoted replies removed; never re-processing; a hard call/token budget in `core/llm.py`;
the system prompt marked for prompt caching.

## 3. File tour, in reading order

| # | File | What to look at |
|---|------|-----------------|
| 1 | `copilot/db.py` | The four tables, `company_key`, `should_advance`, `upsert_application` |
| 2 | `copilot/tracker/sources.py` | The `Email` record; how Gmail messages become plain text |
| 3 | `copilot/tracker/classifier.py` | Categories, rules, `EmailTriage` schema, the prompt |
| 4 | `core/structured.py` | Forced tool call, validation, repair message |
| 5 | `copilot/tracker/sync.py` | The pipeline loop; error handling; action items |
| 6 | `copilot/matcher/schema.py` → `extractor.py` → `scoring.py` | Fields, extraction prompt, scoring weights |
| 7 | `copilot/matcher/market.py`, `copilot/analytics.py` | Pure aggregation: skill demand, funnel |
| 8 | `app.py` | Each tab is one SQL query or function call |
| 9 | `core/llm.py` | Budgets, usage counting, prompt caching, model routing |
| 10 | `tests/` | How a fake LLM makes the pipeline testable offline |

## 4. Trace one email end to end

Email: *"Please complete the HackerRank assessment within 48 hours"* from `careers@orbitretail.in`.

1. `GmailSource.fetch()` lists matching message IDs, skips ones already in the `emails` table, downloads
   this one, and `clean_body()` strips HTML and quoted text.
2. `classify()` → `rule_classify()` finds no alert, newsletter or OTP pattern, so it returns `None`.
3. `structured()` sends the email with the `submit_emailtriage` tool forced. The model returns
   `{"category": "assessment", "company": "Orbit Retail", "action_required": true, "deadline": "2026-09-07", ...}`.
4. Pydantic validates it. If the deadline weren't a real ISO date, the error would go back for one repair.
5. `sync()` maps `assessment` to status `assessment` and calls `upsert_application()`. That finds or creates
   "Orbit Retail", advances the status because assessment outranks applied, stores the deadline, and writes an event.
6. The email ID is saved, so it is never classified again.
7. The dashboard's **Action items** tab now lists the deadline.

## 5. Interview questions to prepare

1. **Why did you build it?** Your real story: six months of searching, folders as a tracker, and what went wrong. Then your before/after numbers from `impact report`.
2. **Why not just use the LLM for everything?** Cost, predictability, explainability. Show rule coverage and the scoring formula.
3. **How do you know it works?** The golden sets, the metrics, the accuracy on *your* labelled emails, and a failure you found and fixed (the LinkedIn sender rule).
4. **What happens if the model returns garbage?** Schema-forced output, Pydantic validation, one repair, then an exception. One failing email doesn't stop the sync and is retried next run.
5. **How do you avoid duplicates?** Message-ID dedup, content hashes, company normalisation plus fuzzy matching.
6. **What about privacy and security?** Read-only scope, local SQLite, secrets gitignored, prompt injection treated as mislabelling risk, truncation.
7. **How would you scale it to 1,000 users?** Postgres instead of SQLite; per-user OAuth tokens in a secrets store; a queue worker for syncs; Gmail push notifications instead of polling; per-user cost limits.
8. **What are its failure modes?** Fuzzy matching merging two different companies; relative deadlines resolved wrongly; postings with no skills listed scoring neutrally; regex rules going stale as portals change email formats.
9. **Why these scoring weights?** They're a starting point you tuned against your own labels. Show precision@5 before and after a tweak.
10. **What would you do next?** The career coach: use your own funnel ("callbacks are 3× higher for analytics-engineer roles") to target applications and learning.

## 6. Exercises (do them in order; run `make test` after each)

1. **Change your profile.** Edit `config/profile.toml`, rerun `python -m copilot.matcher.run --report`, and watch the ranking change with no API calls. Why does that cost nothing?
2. **Add a portal.** Add `"iimjobs": "IIMJobs"` to `PORTALS` in `classifier.py`, then write a test for `detect_portal`.
3. **Tune a weight.** Change `WEIGHTS` in `scoring.py`, run `make eval-matcher-offline`, and explain what moved.
4. **Add a rule.** Find a type of email in your inbox the rules miss (for example "Your profile was viewed"), add a regex, and add a test like `test_rules_catch_alerts_but_not_portal_relayed_rejections`.
5. **New column.** Add a "days since last update" column to the dashboard's Applications tab (one line of pandas), then explain how it relates to the follow-up rule.
6. **Break it on purpose.** Remove the repair round (`max_repairs=0`) and run `tests/test_copilot.py::test_invalid_deadline_is_repaired_by_model`. Read the failure.
7. **Your own eval.** Label 50 of your real emails and run the tracker eval. Write down the three most common errors. That write-up is your strongest interview material.

## 7. Glossary
- **Schema:** the exact shape of data allowed (fields, types, limits).
- **Pydantic:** a Python library that validates data against a schema.
- **Golden set:** hand-labelled examples used to measure accuracy.
- **Precision@5:** of the top 5 ranked items, the share that are actually relevant.
- **Idempotent:** doing it twice has the same effect as doing it once.
- **OAuth scope:** the permission level you grant an app on your account.
- **Prompt injection:** text inside the input that tries to give the model new instructions.
- **Prompt caching:** the API reuses a repeated prompt prefix more cheaply on later calls.

---

# Part 2: the advanced pieces

## 8. The model-agnostic LLM layer (`core/llm.py`)

Every vendor's API is slightly different: OpenAI calls them `tool_calls`, Anthropic calls them `tool_use`
blocks, and system prompts are passed differently. If agent code spoke a vendor's dialect directly, switching
vendors would mean rewriting every agent.

So agents speak one **internal format** (user / assistant-with-tool-calls / tool-result messages), and two small
**adapters** translate it:
- `OpenAICompatProvider` covers OpenAI, Azure OpenAI, Gemini and Ollama, because all four accept the OpenAI format at different URLs.
- `AnthropicProvider` covers Claude.

*Say it like this:* "I used the adapter pattern so the agents don't know which vendor they're talking to. Swapping
providers is a config change, which let me compare a free local model against a paid one on the same eval."

`core/structured.py` adds one more layer of resilience: if a provider ignores the "you must call this tool"
instruction (some local models do), it falls back to finding a JSON object in the text, then validates as usual.

## 9. The career coach (`copilot/coach/`)

### Why this one is an agent and the others aren't
The tracker always does the same steps, so it's a **workflow**. The coach's analyst can't know in advance what
to look at: if the portal split shows nothing, it should try role families; if response rates look fine, it
should check assessments. Deciding the next step from what it just saw is what makes it an **agent**.

### Why LangGraph
The coach needs three things that plain function calls make awkward:
1. **Loops:** the planner can be sent back by the critic or by you.
2. **Branching:** too little data skips the analyst entirely.
3. **Pause and resume:** it must stop, wait for your approval, and continue later.

LangGraph models this as a **graph of nodes** over a shared **state** dict, with **conditional edges** for
routing and a **checkpointer** that saves the state so `interrupt()` can pause and `Command(resume=...)` can continue.
Open `graph.py` and find `_build()`: it's the diagram from the docstring, line by line.

### Evidence-grounded tools (`tools.py`)
The analyst gets four tools: `funnel_stats`, `run_sql` (read-only, enforced by `PRAGMA query_only` at the database
level, not just a regex), `skill_gaps`, and the terminal `submit_diagnosis`. Every tool result is recorded as
**evidence**. `submit_diagnosis` rejects any finding containing a number that never appeared in that evidence,
and tells the agent to fetch it. The model physically cannot report a response rate it made up.

*Say it like this:* "Prompts ask for honesty; validation enforces it. My agent's claims are checked against its
own tool outputs before they're accepted."

### Critic in code, not a model
`check_plan()` tests concrete rules: the hours fit your budget; the actions address the bottleneck stage; a
learning item targets a real skill gap; the plan doesn't focus on your worst-converting portal. A model could do
this, but the rules are clear-cut, so code is free, instant and never inconsistent.

### Knowing when not to answer
With fewer than 10 applications, `data_check` writes an "insufficient data" diagnosis itself, so the analyst is
never called. Conversion rates on 5 applications are noise, and a confident AI diagnosis would mislead you.

### How the coach is evaluated
You can't know the "right" diagnosis of a real job search, so `scenarios.py` builds **synthetic searches with a
planted problem**: Naukri barely responds; assessments rarely convert; data-scientist roles never reply; too
little data. The eval checks that the coach finds the planted problem, names the weak segment, spots the skill
gap, and produces a plan that passes the critic. Building these scenarios also exposed a real bug: fuzzy company
matching merged "Acme 1" and "Acme 2" into one application. See `same_company()` in `db.py`.

## 10. More interview questions

11. **Why LangGraph for the coach but not for the tracker?** Loops, branching and human pause/resume, versus a straight pipeline.
12. **How do you stop the agent hallucinating numbers?** Evidence recording plus a validating terminal tool. Show `test_diagnosis_with_invented_numbers_is_rejected`.
13. **What's a human-in-the-loop step and why here?** `interrupt()` before saving; a plan changes what you do all week, so you approve it.
14. **How did you evaluate an agent whose "correct answer" is subjective?** Synthetic scenarios with planted ground truth, plus deterministic plan checks.
15. **How would you switch to a cheaper model?** Change `.env`, run the evals, compare accuracy against tokens.

## 11. Part 2 exercises
8. Add a `median_days_to_response` tool to `AnalystTools` (SQL over `events`), and write a test for it.
9. Add a fifth scenario, "slow responder portal", to `scenarios.py` and its expected answer.
10. Add a critic rule: `applications_target` must be at least 5 unless the bottleneck is "interview". Add a test.
11. Run the coach eval with two different models and record accuracy and tokens for each in the README.
