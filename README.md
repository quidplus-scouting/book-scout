# Book Scout

Tracker settimanale di libri di successo negli USA (bambini 0-10, manualistica e varia, saggistica)
non ancora pubblicati in Italia, per lo scouting dei diritti di traduzione.

- **Dashboard:** `index.html`, pubblicata con GitHub Pages
- **Aggiornamento:** ogni lunedì alle 5:00 UTC (`.github/workflows/weekly.yml`), oppure a mano da
  *Actions → Aggiornamento settimanale → Run workflow*
- **Fonti:** classifiche Amazon.com (Best Sellers e Novità per categoria), liste NYT Best Sellers
- **Verifica Italia:** Google Books (edizioni in lingua italiana) e Open Library

## Chiavi API (Settings → Secrets and variables → Actions)

| Secret | Dove si ottiene |
|---|---|
| `NYT_API_KEY` | developer.nytimes.com → Apps → abilitare "Books API" |
| `GOOGLE_BOOKS_KEY` | console.cloud.google.com → abilitare "Books API" → Credenziali → Chiave API |

## Punteggio (0-100)

Orientato alla storia di mercato: recensioni accumulate (25) + classifica (20) + longevità in classifica (20)
+ storia (fino a 15: ancora in classifica 1, 3 o 5+ anni dopo l'uscita, o 26+ settimane NYT) + crescita (10) + novità (5)
+ aderenza ai pilastri (fino a 15: uno o più pilastri, libro illustrato), massimo 100,
moltiplicato per lo stato in Italia: autore inedito ×1, autore tradotto ×0,85, da verificare ×0,8, già tradotto ×0,2.
I titoli fuori linea (libri da colorare, calendari, ricettari, finanza personale…) valgono ×0,4 e sono nascosti di default.

## Pilastri

Conoscenza di sé · Relazioni umane · Pensiero critico · Terra e origini · Bambini.
Il pilastro viene dalla categoria Amazon di provenienza e dalle parole chiave di titolo e descrizione
(`AMAZON_CATEGORIES` e `PILLAR_KEYWORDS` in `scout.py`).

Lo stato "inedito" è una stima automatica: i diritti potrebbero essere già stati venduti.
