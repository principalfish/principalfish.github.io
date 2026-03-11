# Full-Stack Migration

**Status:** Parked
**Created:** 2026-03-11

## Goal

Migrate from a GitHub Pages static site to a full-stack application with:
- A hosted database backend
- User accounts (saved predictions, leaderboards)
- React Native mobile app (priority: polls/predictions + election maps)

## Decisions Made

- **Budget:** ~$5-20/month
- **Auth:** Yes — user accounts needed
- **Mobile priority:** Polls/predictions first, election maps second

## Recommended Stack

| Layer | Choice | Why |
|-|-|-|
| Web hosting | Vercel (free tier) | Superior to GitHub Pages for React — preview deploys, edge CDN, instant rollbacks |
| Web framework | Next.js (React) | API routes built in, static export option, Vercel-native |
| API | Next.js API routes (TS) or FastAPI (Python) on Railway ($5-7/mo) | TS: no extra cost on Vercel. Python: familiar ecosystem, separate server |
| Database | Supabase (Postgres) | Managed Postgres, built-in auth, realtime, row-level security. Free tier sufficient to start |
| Mobile | React Native + Expo | Cross-platform, large ecosystem, shares React components/logic with web |

## Hosting Cost Summary

| Option | Cost/mo | What |
|-|-|-|
| Vercel free + Supabase free | $0 | Web + API (TS) + DB + auth. Enough for low traffic |
| Vercel free + Supabase Pro | $25 | 8GB DB, daily backups |
| Vercel free + Railway + Supabase free | $5-7 | If using Python API on Railway |
| Vercel Pro + Supabase Pro | $45 | Full production setup |

## Phase 1: React Web Rewrite (Vercel)

1. Set up Next.js project, deploy to Vercel
2. Port pages to React components: homepage, bio, election maps, guess the year
3. Make the site responsive/mobile-friendly (CSS or component library like Radix/shadcn)
4. D3 integration approach:
   - Use D3 for calculations only (`d3-geo`, `d3-scale`, `d3-array`)
   - React owns the DOM — render `<svg>` elements via JSX
   - Alternative: give D3 a `<div ref={}>` to control directly (easier initial migration)
5. Vercel handles deploys, preview branches, CDN — replaces GitHub Pages
6. Enables code/component sharing with React Native later

## Phase 2: API + Hosted Database

1. Set up Supabase project — migrate existing SQLAlchemy schema to hosted Postgres
2. Build FastAPI app with endpoints:
   - `GET /elections` — metadata
   - `GET /elections/{id}/results` — seat/region results
   - `GET /polls` — poll data with filters
   - `GET /predictions/uns` — UNS model results
   - `POST /guesstheyear/question`, `POST /guesstheyear/answer` — game flow
3. Keep TopoJSON as static assets (serve from CDN or API static files)
4. Deploy API via Next.js API routes on Vercel (free) or FastAPI on Railway ($5-7/mo)
5. Integrate Supabase Auth for user accounts
6. Migrate existing web frontend to call the API instead of reading local JSON

## Phase 3: User Features

1. User accounts via Supabase Auth (email + Google/GitHub social login)
2. Saved predictions — store user prediction sets in DB
3. Guess the Year leaderboard — track scores per user
4. User preferences (favourite regions, default map views)

## Phase 4: React Native Mobile App

1. Scaffold Expo project
2. **Polls & predictions screens** (priority):
   - Poll tracker with trend charts (`victory-native` or `react-native-svg` + D3 math)
   - UNS prediction input/output
   - Push notifications for new polls (optional)
3. **Election maps**:
   - v1: WebView wrapping existing D3 maps (fast to ship, full interactivity)
   - v2: Native SVG rendering with `react-native-svg` + D3 for calculations (better feel, more work)
4. Guess the Year game screen
5. Auth integration (Supabase JS client works in React Native)

## Phase 5: React Native Web Convergence (Future)

- Optional: use React Native Web to share components between mobile and web apps
- Only worth doing once both web (Phase 1) and mobile (Phase 4) patterns are stable

## Key Design Notes

- **Supabase over plain Railway Postgres** — built-in auth, realtime, and row-level security. Avoids building auth from scratch.
- **FastAPI over Flask** — better for API-first design. Existing Flask guesstheyear code is small enough to port.
- **WebView for maps on mobile v1** — pragmatic. Native SVG port is a large effort; validate demand first.
- **TopoJSON stays as static assets** — geographic boundary data doesn't belong in a relational DB.

## Verification

- Phase 1: Site looks/works identically, is responsive on mobile viewports. Deploys to Vercel.
- Phase 2: Web frontend fetches from API instead of local JSON. Run existing vitest suite.
- Phase 3: Create account, save a prediction, see it persist across sessions.
- Phase 4: Expo app runs on iOS/Android simulator, polls screen loads data, map WebView renders.

## Files to Create/Modify

- New: `api/` directory (FastAPI app, routes, models)
- New: `mobile/` directory (Expo React Native project)
- Modify: existing JS to fetch from API instead of local JSON
- Modify: `data/` models to align with Supabase schema
- Reference: existing SQLAlchemy models in `data/models/`
