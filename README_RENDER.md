# DobraTorebka Ads V46 — Render

Ta paczka jest przygotowana do wdrożenia aplikacji Flask na Render.

## Najszybsze wdrożenie

### 1. Utwórz repozytorium GitHub
Utwórz prywatne repozytorium, np. `dobratorebka-ads`.

Wgraj do niego CAŁĄ zawartość tego folderu. Nie wgrywaj własnego pliku `.env`.

### 2. Render
W Render:
1. New -> Blueprint
2. Połącz GitHub.
3. Wybierz repozytorium aplikacji.
4. Render odczyta `render.yaml`.
5. Utwórz usługę.

Możesz też utworzyć ręcznie New -> Web Service:
- Runtime: Python 3
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300`
- Health Check Path: `/healthz`

## 3. Zmienne środowiskowe

W Render -> Environment dodaj:

`NEWBLACK_API_KEY`
- Twój klucz The New Black.

`FB_PAGE_ID`
- `207538753475384`

`FB_PAGE_ACCESS_TOKEN`
- aktualny Page Access Token strony DobraTorebka.pl.

`FB_GRAPH_VERSION`
- np. `v26.0`

NIE wpisuj prawdziwych kluczy ani tokenów do GitHub.

## 4. Adres

Po wdrożeniu Render nada adres podobny do:

`https://dobratorebka-ads.onrender.com`

## 5. Subdomena ads.dobratorebka.pl

W Render:
Settings -> Custom Domains -> Add Custom Domain

Dodaj:

`ads.dobratorebka.pl`

Render pokaże rekord DNS, który trzeba dodać w panelu DNS dhosting. Po weryfikacji Render automatycznie obsłuży HTTPS.

## Ważne przy planie Free

Render Free usypia usługę po okresie bezczynności. Pierwsze otwarcie po uśpieniu może potrwać około minuty.

Lokalny system plików Render jest nietrwały. Grafiki w `static/generated` mogą zniknąć po restarcie, redeployu lub uśpieniu. Dla obecnego sposobu pracy jest to OK, jeśli grafikę od razu pobierasz lub publikujesz. Jeśli chcesz historię grafik, potrzebny będzie zewnętrzny storage albo płatny persistent disk.

## Pliki Render

- `render.yaml` — konfiguracja usługi
- `.python-version` — Python 3.11.11
- `Procfile` — zapasowa komenda startowa
- `/healthz` — endpoint health check
- `gunicorn` — produkcyjny serwer WSGI

## Test lokalny przed wdrożeniem

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
gunicorn app:app --bind 0.0.0.0:10000 --workers 1 --threads 4 --timeout 300
```

Windows nadal możesz uruchamiać dotychczasowym sposobem.
