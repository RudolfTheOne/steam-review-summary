# steamsum: streszczenie recenzji gry ze Steama

```
python3 steamsum.py https://store.steampowered.com/app/3321460/Crimson_Desert_Enhanced/
python3 steamsum.py 1465360 --model gemini
python3 steamsum.py 10 --model openrouter:openai/gpt-6-luna --months 12
python3 steamsum.py 1203190 --dry-run      # bez modelu: wykres, próbka, szacowany koszt
```

Wymaga Pythona 3.11+ i niczego więcej: działa na macOS, Linuksie i Windowsie.

## Co robi

1. Z linku wyciąga numer gry. Pobiera nazwę, datę premiery i datę pierwszej recenzji.
2. **Oś czasu:** liczba recenzji pozytywnych i negatywnych w wybranych językach. Ostatnie 24 miesiące liczy miesiąc po miesiącu, wcześniejsze lata rok po roku. Osobno liczy zakupy na Steamie, a różnica to aktywacje kluczy. Liczy to kod ze wszystkich recenzji, nie model.
3. Rysuje wykres w terminalu, kolorowany progami Steama (80/70/40%).
4. **Dobór recenzji** w budżecie tokenów (domyślnie 350 tys., twardy limit 600 tys.):
   - 75% na okno analizy (domyślnie od 1. dnia miesiąca sprzed 9 miesięcy), 25% na tło sprzed okna (najbardziej pomocne recenzje z całej wcześniejszej historii);
   - w oknie każdy miesiąc dostaje część proporcjonalną do pierwiastka z liczby recenzji;
   - w miesiącu połowa najbardziej pomocnych, połowa najnowszych; strona mniejszościowa (pozytywne albo negatywne) dostaje co najmniej 25% miejsca;
   - czyszczenie: znaczniki BBCode, niezaznaczone pola szablonów „☐ Bad ☑ Good”, powtórzenia znaków, kopie tych samych tekstów; recenzje krótsze niż 30 znaków pomijane, dłuższe niż 3000 skracane.
5. Prompt: tabela liczb, ogłoszenia twórców z okna (patch notes) i recenzje pogrupowane po okresach. Model ma cytować recenzje `[#12]` i nie podawać własnych liczb.
6. Raport HTML w jednym pliku: wykres % pozytywnych z pasmem niepewności, liczba recenzji, znaczniki ogłoszeń, streszczenie z odnośnikami do recenzji na Steamie, tabela okna, opis metody. Obok zapisuje `summary.md` i `prompt.txt`. Prompt można wkleić ręcznie do dowolnego czatu.

## Modele

Profile wbudowane (`--model`):

| Profil | Dostawca | Model | Klucz |
|---|---|---|---|
| `deepseek` (domyślny) | OpenRouter | deepseek/deepseek-v4.1-flash | `OPENROUTER_API_KEY` |
| `gemini` | OpenRouter | google/gemini-3.8-flash | `OPENROUTER_API_KEY` |
| `luna` | OpenAI | gpt-6-luna, `reasoning_effort: low` | `OPENAI_API_KEY` |
| `claude` | Anthropic | claude-opus-5-5, `effort: medium` | `ANTHROPIC_API_KEY` |

Dowolny model „w locie”: `dostawca:model`, gdzie dostawca to `openrouter`, `openai`, `anthropic`, `deepseek` (API DeepSeek) albo `ollama` (lokalnie).

### Klucze API

Klucze nigdy nie trafiają do repozytorium ani do `config.toml`. Profil wskazuje tylko, *gdzie* szukać klucza. Kolejność:

1. **Zmienna środowiskowa** z `key_env` (np. `OPENROUTER_API_KEY`). Działa wszędzie, na Windowsie najprostsza.
2. **`key_cmd`**: dowolne polecenie, które wypisuje klucz, np. 1Password `op read "op://Private/OpenRouter/credential"`, `pass show openrouter`, na Linuksie `secret-tool lookup service steamsum account openrouter`.
3. **Pęk kluczy macOS** (zalecane na Macu): usługa `steamsum`, konto = `key_name` profilu. Jednorazowo:
   ```
   security add-generic-password -s steamsum -a openrouter -w
   ```
   Polecenie zapyta o klucz, więc nie zostaje on w historii powłoki.
4. **Magazyn kluczy narzędzia `llm`**, jeśli jest zainstalowane (`llm keys get openrouter`). Uwaga: to zwykły plik JSON.

Dobra praktyka: osobny klucz OpenRouter tylko do tego narzędzia, z limitem wydatków ustawionym na stronie OpenRouter.

Własne profile i ustawienia: `~/.config/steamsum/config.toml` (Windows: `%APPDATA%\steamsum\config.toml`):

```toml
model = "gemini"
budget_tokens = 400000
languages = ["english", "polish", "german"]

[models.sonnet]
api = "anthropic"
model = "claude-sonnet-5-5"
key_env = "ANTHROPIC_API_KEY"
key_name = "anthropic"
price_in = 2.0
price_out = 10.0

[models.deepseek]
key_cmd = "op read op://Private/OpenRouter/credential"   # nadpisuje sposób pobrania klucza
```

Ceny do szacunku kosztu: z publicznego katalogu OpenRouter albo z `price_in`/`price_out` w profilu ($ za 1 mln tokenów).

**Tokeny:** każdy model liczy je inaczej. Pomiar: prompt Crimson Desert (1226 recenzji, 763 tys. znaków) to 203 tys. tokenów według tokenizatora Gemini 3.8 Flash, czyli 3,76 znaku na token. Ręczny eksport CSV dawał ok. 2,2 znaku na token (1796 recenzji = 380 tys.), bo długie ID, znaczniki czasu, BBCode i szablony ASCII zjadają tokeny. Narzędzie zaczyna od 3,5. Po każdym wywołaniu zapisuje faktyczny przelicznik danego modelu i używa go przy następnym doborze próbki.

## Steam: czego się dowiedzieliśmy

- `appreviews` przyjmuje kilka języków po przecinku, okno dat (`start_date`, `end_date`, `date_range_type=include`), `review_type=positive|negative`, `purchase_type=steam|non_steam_purchase`. Bez klucza.
- **Limit tempa:** ok. 250 zapytań w kilka minut kończy się HTTP 429 na 1–2 minuty. Narzędzie robi odstęp 0,6 s, a po 429 czeka 30–120 s. Pierwsze uruchomienie dla dużej gry trwa więc kilka minut. Wszystko trafia do pamięci podręcznej (macOS: `~/Library/Caches/steamsum`): zamknięte miesiące na stałe, bieżący na 6 h.
- „Z kluczy” to nie dowód astroturfingu. W Counter-Strike to 75% recenzji w 2011 roku (pudełka, pakiety) i ok. 20% dziś. Raport porównuje to z poziomem bazowym gry.
- Strona w przeglądarce nie pobierze recenzji sama, bo Steam nie wysyła nagłówków CORS. Stąd skrypt.

## Pliki

| Plik | Rola |
|---|---|
| `steamsum.py` | uruchamianie bez instalacji |
| `steamsum/__main__.py` | przebieg i opcje wiersza poleceń |
| `steamsum/steam.py` | zapytania do Steama, tempo, ponawianie |
| `steamsum/cache.py` | pamięć podręczna na dysku (katalog zależny od systemu) |
| `steamsum/timeline.py` | okresy i liczby recenzji |
| `steamsum/sample.py` | czyszczenie i dobór recenzji w budżecie |
| `steamsum/prompt.py` | prompt systemowy i dane dla modelu |
| `steamsum/llm.py` | protokoły OpenAI-zgodny i Anthropic, szukanie kluczy, kalibracja tokenów, ceny |
| `steamsum/chart_term.py` | wykres w terminalu |
| `steamsum/report.py` | raport HTML (SVG rysowane w Pythonie, mały renderer Markdown) |
