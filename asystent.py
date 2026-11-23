"""
Prosty asystent AI dla firmy - model Gemini w Google Cloud (Vertex AI).

Uruchomienie:
    pip install google-genai
    gcloud auth application-default login
    export GOOGLE_CLOUD_PROJECT=twoj-projekt-gcp
    python asystent.py
"""

import os
import sys

from google import genai
from google.genai import types

# --- Konfiguracja (mozna nadpisac zmiennymi srodowiskowymi) ---
PROJEKT = os.environ.get("GOOGLE_CLOUD_PROJECT")
REGION = os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-central2")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

NAZWA_FIRMY = os.environ.get("NAZWA_FIRMY", "Moja Firma")

INSTRUKCJA_SYSTEMOWA = f"""
Jestes uprzejmym i rzeczowym asystentem AI firmy "{NAZWA_FIRMY}".
Odpowiadasz po polsku, krotko i konkretnie.
Pomagasz klientom i pracownikom: odpowiadasz na pytania, piszesz maile,
opisy produktow i podsumowania.
Jesli czegos nie wiesz (np. cen, terminow, stanow magazynowych),
powiedz to wprost i nie wymyslaj informacji.
"""


def utworz_klienta() -> genai.Client:
    if not PROJEKT:
        sys.exit(
            "Brak projektu Google Cloud. Ustaw zmienna, np.:\n"
            "  export GOOGLE_CLOUD_PROJECT=twoj-projekt-gcp"
        )
    return genai.Client(vertexai=True, project=PROJEKT, location=REGION)


def main() -> None:
    klient = utworz_klienta()
    czat = klient.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=INSTRUKCJA_SYSTEMOWA,
            temperature=0.4,
        ),
    )

    print(f"Asystent AI firmy {NAZWA_FIRMY} (model: {MODEL}, region: {REGION})")
    print("Wpisz pytanie. Aby zakonczyc, wpisz 'koniec' lub nacisnij Ctrl+C.\n")

    while True:
        try:
            pytanie = input("Ty: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nDo zobaczenia!")
            break

        if not pytanie:
            continue
        if pytanie.lower() in {"koniec", "exit", "quit"}:
            print("Do zobaczenia!")
            break

        try:
            print("Asystent: ", end="", flush=True)
            for fragment in czat.send_message_stream(pytanie):
                if fragment.text:
                    print(fragment.text, end="", flush=True)
            print("\n")
        except Exception as blad:  # np. brak uprawnien, zly region, limit
            print(f"\n[Blad] {blad}\n")


if __name__ == "__main__":
    main()
