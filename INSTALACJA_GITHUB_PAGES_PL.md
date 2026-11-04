# Instalacja Mathematics Atlas w lokalnej kopii repozytorium

Paczka jest nakładką na `marcinb-pwr/mathematics-atlas-controller-prototype`.
Zawiera pełną stronę i dane Atlasa, workflow GitHub Pages oraz pliki weryfikacyjne.
Nie zawiera katalogu `.git` ani narzędzi Lean. Istniejące pliki kontrolera pozostają w repo.
Przygotowano na podstawie `main` o SHA `660c71007216ca405cfa09a8157d0248fc446627`.

## 1. Wgraj pliki

Zapisz lub zacommituj własne lokalne zmiany. W terminalu, w katalogu repo:

```sh
git switch main
git pull --ff-only
```

Rozpakuj ZIP BEZPOŚREDNIO do głównego katalogu repozytorium, ze scaleniem
katalogów i zastąpieniem odpowiadających plików. Po rozpakowaniu muszą istnieć
`dist/index.html` oraz `.github/workflows/pages.yml` bez dodatkowego katalogu
pośredniego. ZIP zawiera ukryty katalog `.github`; nie pomijaj go przy kopiowaniu.
Na macOS/Linux można użyć:

```sh
unzip -o /sciezka/do/mathematics-atlas-github-pages.zip -d .
```

## 2. Sprawdź i wypchnij

Opcjonalne sprawdzenie lokalne wymaga Python 3.11+ i Node.js 22+:

```sh
python3 scripts/verify_web_assets.py
node --test tests/test_graph.mjs tests/test_loading.mjs
python3 scripts/stage_public.py
python3 -m http.server 8000 --directory out
```

Otwórz http://localhost:8000/ . Serwer zatrzymasz przez Ctrl+C.
Do samego pushowania gotowej paczki nie potrzebujesz Node, Pythona ani Lean.

```sh
git status --short
git add -- .github/workflows/pages.yml dist scripts/stage_public.py scripts/verify_web_assets.py tests/test_graph.mjs tests/test_loading.mjs WEB_ASSETS.json GITHUB_PAGES.md INSTALACJA_GITHUB_PAGES_PL.md README.md CONTROLLER_MANIFEST.json SHA256SUMS
git diff --cached --stat
git commit -m "Publish Mathematics Atlas on GitHub Pages"
git push origin main
```

## 3. Włącz GitHub Pages

1. Otwórz https://github.com/marcinb-pwr/mathematics-atlas-controller-prototype/settings/pages .
2. W **Build and deployment → Source** wybierz **GitHub Actions**.
3. W **Actions → Publish Mathematics Atlas** sprawdź wynik uruchomienia.
   Jeśli pierwsze wdrożenie nie powiodło się przed włączeniem Pages, użyj
   **Re-run failed jobs**, albo **Run workflow** dla gałęzi `main`.
4. Po zielonym zadaniu `deploy` otwórz:
   https://marcinb-pwr.github.io/mathematics-atlas-controller-prototype/

Przykładowy bezpośredni link do twierdzenia:
https://marcinb-pwr.github.io/mathematics-atlas-controller-prototype/#node=Nat.exists_infinite_primes&focus=Nat.exists_infinite_primes&branch=all&view=dependencies&depth=1&kernel=0

Kolejne zmiany strony na `main` uruchomią publikację automatycznie. Workflow ma
uprawnienia `pages: write` i `id-token: write`; nie wymaga własnego tokena ani sekretu.
Jeśli organizacja lub repozytorium blokuje Actions, zezwól na użyte oficjalne akcje GitHub.
Pierwsze ładowanie pobiera około 13 MB skompresowanego grafu.

## Co sprawdzono

- 17/17 testów eksploracji i ładowania.
- Zgodność wszystkich 20 517 deklaracji z surowym eksportem w paczce danych.
- Integralność 507 773 krawędzi oraz ośmiu gałęzi matematyki.
- Sumy SHA-256 17 zasobów i zgodność tabeli wyrażeń z oryginalnym eksportem.
- Przygotowanie statycznego katalogu `out`.

To eksport istniejącego korpusu i interfejsu, a nie ponowne sprawdzenie dowodów
przez Lean. Wdrożenie na Twoim GitHubie nastąpi dopiero po Twoim pushu i
włączeniu Pages; nie było uruchamiane podczas tworzenia ZIP.
