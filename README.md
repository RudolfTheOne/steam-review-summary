# steamsum: summarise a game's Steam reviews with any LLM

```
python3 steamsum.py https://store.steampowered.com/app/3321460/Crimson_Desert_Enhanced/
python3 steamsum.py 1465360 --model gemini
python3 steamsum.py 10 --model openrouter:openai/gpt-6-luna --months 12
python3 steamsum.py 1203190 --dry-run      # no model: chart, sample and estimated cost only
python3 steamsum.py --set-default-model luna   # save the default model to config.toml
python3 steamsum.py 3321460 --cached       # show the saved summary without asking
python3 steamsum.py 3321460 --refresh      # always generate a new one
```

Requires Python 3.11+ and nothing else. Works on macOS, Linux and Windows.

The terminal messages and the report are in Polish; the summary language is set by `answer_language` in the config.

## What it does

1. Reads the app id from the link and fetches the game name, release date and the date of the first review.
2. **Timeline:** positive and negative review counts in the chosen languages, month by month for the last 24 months and year by year before that. Steam purchases are counted separately, so the rest are key activations. Code computes these numbers from all reviews; the model never does.
3. Prints a btop-style chart in the terminal, coloured by Steam's thresholds (80/70/40%).
4. **Picks reviews** within a token budget (350k by default, hard limit 600k):
   - 75% goes to the analysis window (by default from the first day of the month 9 months ago), 25% to earlier history. Whatever the window cannot use, for example because most of its reviews are one-liners, goes to the history.
   - In the window, each month gets a share proportional to the square root of its review count, so a launch month does not eat everything and quiet months stay visible.
   - Within a month: half most helpful, half newest. The minority side (positive or negative) gets at least 25% of the space; the model is given the real proportions separately.
   - Earlier history: the most helpful reviews of each year, or of each month if the history is shorter than a year. This also shows how opinions evolved.
   - Cleaning: BBCode tags, unchecked lines of "☐ Bad ☑ Good" templates, repeated characters, duplicate texts. Reviews under 30 characters are skipped, those over 3000 are shortened.
5. Builds the prompt: the score table, developer announcements from the window (patch notes) and reviews grouped by period. The model must cite reviews as `[#12]` and must not state its own numbers.
6. Streams the model's answer to the terminal, formatted in the style of glow's "dracula" theme: wrapped text, coloured headings and bold, dimmed citations.
7. Writes a single-file HTML report:
   - % positive over time with an uncertainty band, review counts and announcement markers;
   - the summary, where every citation links to the review on Steam and shows its beginning on hover;
   - a table of window months split into Steam purchases and key activations;
   - method notes.

   `summary.md` and `prompt.txt` are saved next to it; the prompt can be pasted into any chat by hand.

## Saved summaries

Each summary is saved next to its report (`~/steamsum/<date>-<game>-<model>/`): `run.json` with the summary, the model, the window, the languages and a snapshot of the review counts and developer announcements at the time, and `citations.json` mapping the `[#n]` numbers to reviews.

When you run the tool again for the same game, languages, window and model, it fetches the current counts (a few requests) and tells you what changed since, e.g.:

```
Masz podsumowanie z 23.10 17:36 (gpt-6-luna).
Od tego czasu: 142 nowe recenzje (odsetek pozytywnych w oknie 86,3% → 86,1%), 1 nowe ogłoszenie twórców: „Patch Notes 2.01.00”.
[W]yświetl zapisane / [O]dśwież? [W/o]
```

"Show saved" prints the saved summary and rebuilds the report with current charts, so it is instant and costs nothing. The question is only asked in an interactive terminal; in scripts or with redirected output the tool generates a new summary as before. `--cached` and `--refresh` skip the question. A summary from a different model is never reused silently; the tool only mentions that it exists.

## Models

Built-in profiles (`--model`):

| Profile | Provider | Model | Key |
|---|---|---|---|
| `deepseek` (default) | OpenRouter | deepseek/deepseek-v4.1-flash | `OPENROUTER_API_KEY` |
| `gemini` | OpenRouter | google/gemini-3.8-flash | `OPENROUTER_API_KEY` |
| `luna` | OpenAI | gpt-6-luna, `reasoning_effort: low` | `OPENAI_API_KEY` |
| `claude` | Anthropic | claude-opus-5-5, `effort: medium` | `ANTHROPIC_API_KEY` |

Any other model ad hoc: `provider:model`, where the provider is `openrouter`, `openai`, `anthropic`, `deepseek` (DeepSeek's own API) or `ollama` (local).

### API keys

Keys never go into the repository or into `config.toml`; a profile only says *where* to look for the key. Lookup order:

1. **Environment variable** named in `key_env` (e.g. `OPENROUTER_API_KEY`). Works everywhere; simplest on Windows.
2. **`key_cmd`**: any command that prints the key, e.g. 1Password `op read "op://Private/OpenRouter/credential"`, `pass show openrouter`, or `secret-tool lookup service steamsum account openrouter` on Linux.
3. **macOS Keychain** (recommended on a Mac): service `steamsum`, account = the profile's `key_name`. Once:
   ```
   security add-generic-password -s steamsum -a openrouter -w
   ```
   It prompts for the key, so the key does not end up in your shell history.
4. **The key store of the `llm` tool**, if installed (`llm keys get openrouter`). Note that it is a plain JSON file.

Good practice: a separate key just for this tool, with a spending limit set on the provider's site.

To change the default model without editing files: `python3 steamsum.py --set-default-model <profile or provider:model>`. It only rewrites the top-level `model = ...` line and keeps the rest of the file.

Your own profiles and settings go in `~/.config/steamsum/config.toml` (Windows: `%APPDATA%\steamsum\config.toml`):

```toml
model = "gemini"
budget_tokens = 400000
languages = ["english", "polish", "german"]
answer_language = "English"

[models.sonnet]
api = "anthropic"
model = "claude-sonnet-5-5"
key_env = "ANTHROPIC_API_KEY"
key_name = "anthropic"
price_in = 2.0
price_out = 10.0

[models.deepseek]
key_cmd = "op read op://Private/OpenRouter/credential"   # overrides how the key is obtained
```

Prices for the cost estimate come from OpenRouter's public catalogue or from `price_in`/`price_out` in the profile ($ per 1M tokens).

**Tokens:** every model counts them differently. Measured: the Crimson Desert prompt (1226 reviews, 763k characters) is 203k tokens for the Gemini 3.8 Flash tokenizer, i.e. 3.76 characters per token. A hand-made CSV export of the same kind of data came to about 2.2 characters per token (1796 reviews = 380k), because long ids, timestamps, BBCode and ASCII templates eat tokens. The tool starts at 3.5, then records each model's actual ratio after a call and uses it for the next sample.

## Steam: what we learned

- **Endpoint change (2026-10-23):** `store.steampowered.com/appreviews/<id>` now redirects to the store front page. `ajaxappreviews/<id>` takes the same parameters and returns the same JSON; the tool tries both and uses whichever answers.
- The review endpoint accepts several comma-separated languages, a date window (`start_date`, `end_date`, `date_range_type=include`), `review_type=positive|negative` and `purchase_type=steam|non_steam_purchase`. No key needed.
- **"Most helpful" has limits.** With a date window the list stops at 100 reviews, because page 2 repeats page 1. Without a window it only covers the last year (`day_range` max 365). There is no "most helpful of all time", which is why earlier history is sampled year by year.
- **Rate limit:** about 250 requests within a few minutes trigger HTTP 429 for 1–2 minutes. The tool waits 0.6 s between requests and 30–120 s after a 429, so the first run for a big game takes 3–5 minutes. Everything is cached (macOS: `~/Library/Caches/steamsum`): closed months forever, the current month for 6 hours, so later runs take seconds.
- "Key activations" are not proof of astroturfing on their own. For Counter-Strike they were 75% of reviews in 2011 (retail boxes, bundles) and about 20% today. The report compares each month with the game's own average and hides the percentage for groups under 30 reviews.
- A web page cannot fetch the reviews by itself, because Steam sends no CORS headers. Hence a script.

## Files

| File | Role |
|---|---|
| `steamsum.py` | run without installing |
| `steamsum/__main__.py` | command-line flow and options |
| `steamsum/steam.py` | Steam requests, pacing, retries |
| `steamsum/cache.py` | disk cache (OS-specific directory) |
| `steamsum/timeline.py` | periods and review counts |
| `steamsum/sample.py` | cleaning and budgeted review selection |
| `steamsum/prompt.py` | system prompt and model input |
| `steamsum/llm.py` | OpenAI-compatible and Anthropic protocols, key lookup, token calibration, prices |
| `steamsum/chart_term.py` | terminal chart |
| `steamsum/report.py` | HTML report (SVG drawn in Python, a small Markdown renderer) |
| `steamsum/term_md.py` | streaming Markdown → coloured terminal output |
| `steamsum/progress.py` | the self-updating status line |
| `steamsum/history.py` | saved summaries and what changed since |

## License

MIT, see [LICENSE](LICENSE).
