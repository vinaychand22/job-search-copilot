# Setup: getting an AI model to run the copilot

The copilot sends small pieces of text (an email, a job post) to an AI model and gets structured
answers back. You need access to **one** model. You can switch any time by editing `.env`; no code changes.

**You don't need any of this to try the project.** `make test` and `make demo` run without a key.

## Which option should I pick?

| Option | Cost | Privacy | Best for |
|---|---|---|---|
| **A. Gemini (Google)** | Free tier | Free-tier content may be used by Google to improve its products | Starting out, the matcher, the coach on demo data |
| **B. Ollama (local)** | Free | Nothing leaves your computer | Your real emails (tracker) |
| **C. OpenAI** | Pay as you go, from $5 prepaid | Not used for training by default on the API | The best quality-to-cost ratio once you're serious |
| **D. Azure OpenAI** | Needs an Azure subscription | Enterprise terms | Matching what employers run |
| **E. Anthropic (Claude)** | Pay as you go | Not used for training by default on the API | An alternative to OpenAI |

**Recommended path:** start with **A** today (free). Use **B** for your real inbox, because it's private. Add **C**
later if you want better accuracy, and compare them with the evals. That comparison is a great interview story in itself.

Pricing and free tiers change; check the linked pages before you rely on them.

---

## A. Gemini API key (free, about 3 minutes)

1. Open **https://aistudio.google.com** and sign in with your Google account.
2. Click **Get API key**, then **Create API key**. Accept the terms if asked.
3. Copy the key (a long string). Treat it like a password.
4. In the project folder, create your `.env` file and open it:
   - Windows (PowerShell): `copy .env.example .env` then `notepad .env`
   - macOS: `cp .env.example .env` then `open -e .env`

   Paste the key after `GEMINI_API_KEY=` (no spaces) and save.
5. Check the connection first, then run the matcher (the `.env` file is read automatically):
   ```bash
   python -m core.check
   python -m copilot.matcher.run copilot/matcher/data/sample_postings
   ```

Free-tier limits and models are listed at https://ai.google.dev/gemini-api/docs/pricing.
**Privacy:** on the free tier, Google may use what you send to improve its products. Use it for job postings and
demo data, not your personal email.

## B. Ollama (free, private, about 10 minutes)

1. Download from **https://ollama.com/download** (Windows and macOS) and install it.
2. In a terminal (PowerShell on Windows), download a model. This is a one-time download of a few GB:
   ```bash
   ollama pull llama3.1:8b
   ```
3. In `.env`, comment out the Gemini lines and uncomment the Ollama block (`LLM_PROVIDER=ollama`).
4. Run as usual. Ollama must be running (it starts automatically after install).

Local models need a reasonably powerful computer (16 GB RAM recommended), and small ones are less accurate than cloud ones. Run `python -m copilot.tracker.eval` with both and
compare; the numbers tell you whether it's good enough.

## C. OpenAI API key (paid)

1. Go to **https://platform.openai.com**, sign up, and open **Settings → Billing**.
2. Add a payment method and buy credits. The minimum is $5, and credits expire after a year
   ([OpenAI billing help](https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing)).
3. Open **API keys → Create new secret key**, then copy it.
4. In `.env`: `LLM_PROVIDER=openai`, paste `OPENAI_API_KEY=...`, and set the model names (check the current list on the platform).
5. Set a **monthly budget limit** in Billing so you can never overspend.

A ChatGPT Plus subscription is separate and does **not** include API access.

## D. Azure OpenAI
You need an Azure subscription with Azure OpenAI access. Create a resource, deploy a model, then set
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and use the **deployment name** as `FAST_MODEL` / `SMART_MODEL`.

## E. Anthropic (Claude)
Sign up at **https://console.anthropic.com**, add credits under Billing, and create a key under API Keys. Then set
`LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=...`.

---

## If something seems stuck
Run `python -m core.check`. It sends one tiny request and explains any error in plain words
(rejected key, wrong model name, rate limit, no internet). On free tiers, use `--workers 1` with the matcher if you
see rate-limit errors.

## Rules for keys
- Keys live only in `.env`, which is in `.gitignore`. Check with `git status` that `.env` never appears.
- Never paste a key into code, a GitHub issue, or a chat.
- If a key leaks, delete it on the provider's site and create a new one.

## Gmail (for the tracker)
See README → *Gmail setup*. It's separate from the AI key and uses read-only access.
