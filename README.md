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

Classifica (25) + longevità in classifica (25) + crescita settimanale (20) + novità (15) + recensioni (15),
moltiplicato per lo stato in Italia: autore inedito ×1, autore tradotto ×0,85, da verificare ×0,8, già tradotto ×0,2.

Lo stato "inedito" è una stima automatica: i diritti potrebbero essere già stati venduti.
