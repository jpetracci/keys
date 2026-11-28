# Keys frontend

The React single-page app for Keys, built with Vite. The [project README](../README.md) describes the whole application, including how this build is served by Django in the Docker image.

## Commands

```bash
npm ci          # install exactly what package-lock.json records
npm run dev     # dev server on http://localhost:5173
npm test        # unit and component tests (Vitest, jsdom)
npm run lint    # ESLint, warnings are errors
npm run build   # production build into dist/
```

`npm test` needs Node 22.12 or newer (24 is what CI and the Docker image use).

## Talking to the backend

The dev server forwards `/api` requests to Django at `http://127.0.0.1:8000` (see `vite.config.js`), so run `python manage.py runserver` in `backend/` alongside it. A production build calls the API on the address it is served from. Set `VITE_API_URL` only to send requests somewhere else; the project README explains what the backend then needs.

## Layout

- `src/pages/` holds the routed pages; `Home.jsx` is the transaction page.
- `src/components/` holds the transaction row, the filter controls, the statement upload and the login form.
- `src/transactionList.js` holds the list's paging and filter logic, and `src/api.js` the HTTP client.
- `public/` holds files copied to the build as they are, such as the favicon.
