# Performance Review: node-express-realworld

**Stack:** Express 4.18, Prisma 4.16 on PostgreSQL, bcryptjs, express-jwt. One Node process (Nx/esbuild build, Docker `node:lts-alpine`).

**Scope:** everything under `src/`: routes, services, mappers, the Prisma schema and migrations, `main.ts`, plus the `Dockerfile` and `project.json`.

## Executive summary

The API is small. Its main cost is in how it reads data, not in its CPU work. The most serious problem is that almost every article, comment and profile endpoint loads **whole relation lists** (every user who favorited an article, every follower of an author) just to work out one boolean and one count. As the data grows, that cost grows with it. A popular article or author makes every request that touches it slow and memory-heavy. On the favorite and unfavorite endpoints those full `User` rows, password hashes included, are also sent back to the client. The next problems are unbounded result sizes (no cap on `limit`, and the comments list has no pagination at all) and missing indexes on the foreign keys and sort columns used by the busiest queries.

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | Whole `favoritedBy` and `followedBy` relations loaded to compute a boolean and a count | **Critical** | Low–Medium |
| 2 | No upper bound on page size; comments endpoint has no pagination | **High** | Low |
| 3 | Missing indexes on FK and sort columns (`Article.authorId`, `Article.createdAt`, `Comment.articleId`, `Comment.authorId`, `User.demo`) | **High** | Low |
| 4 | List endpoints run count and page queries one after the other, and each Prisma `include` adds its own round trip | Medium | Low |
| 5 | `GET /tags` runs a full aggregate on every request with no caching | Medium | Low |
| 6 | bcryptjs (pure JS) hashing runs on the event loop | Medium | Low |
| 7 | `updateArticle` makes 4+ sequential round trips with no transaction | Medium | Low |
| 8 | Extra round trips in auth, create and delete paths | Low | Low |
| 9 | Runtime and deployment settings: no compression, no `NODE_ENV=production`, default pool size, one process, no timeouts | Low–Medium | Low |

---

## 1. Whole relation lists loaded to compute `favorited`, `favoritesCount` and `following` (Critical)

### Where

Every article query uses the same `include` block:

- `src/app/routes/article/article.service.ts`
  - `getArticles`, lines 84–104
  - `getFeed`, lines 133–153
  - `createArticle`, lines 215–235
  - `getArticle`, lines 246–266
  - `updateArticle`, lines 359–379
  - `favoriteArticle`, lines 574–594
  - `unfavoriteArticle`, lines 620–640

```ts
author: { select: { username: true, bio: true, image: true, followedBy: true } },
favoritedBy: true,
_count: { select: { favoritedBy: true } },
```

- Comments: `getCommentsByArticle`, lines 447–454, and `addComment`, lines 501–510, both use `author.followedBy: true`.
- Profiles: `src/app/routes/profile/profile.service.ts`. `getProfile`, `followUser` and `unfollowUser` all use `include: { followedBy: true }`.
- The mappers then reduce those arrays to a single value:
  - `article.mapper.ts:11-12`: `favoritedBy.some(...)` and `favoritedBy.length`
  - `author.mapper.ts:8`, `profile.utils.ts:9`: `followedBy.some(...)`

### Why it matters

- `favoritedBy: true` and `followedBy: true` select **every column of every related `User` row**: id, email, username, the bcrypt password hash, bio, image and demo. For one list page of 10 articles, the database returns 10 × (favorites per article) + 10 × (followers per author) full user rows. Prisma then turns them into JS objects, and the mapper throws away everything except one boolean and one length.
- The cost per request grows with how popular the content is, not with page size. One author with 50k followers means every article list that shows one of their articles, and every comment they write, pulls 50k user rows. That affects response time, Postgres I/O, the Prisma engine's serialization work and Node heap and GC pressure, and it gets worse the more successful the site is.
- `_count.favoritedBy` is already requested, which costs an extra query. But `articleMapper` ignores it and uses `favoritedBy.length`, so the work is done twice.
- **Payload blow-up and data leak:** `favoriteArticle` and `unfavoriteArticle` (lines 597–603, 643–649) return `...article`. That spreads the whole `favoritedBy` array into the JSON response, which means every favoriting user's email and password hash, plus `id` and `authorId`. Response size grows without limit as the favorite count grows. It is also a serious security problem in its own right.

### What to do

Load only what the mapper needs. Filter the relation down to the viewer and rely on `_count` for totals:

```ts
const articleInclude = (viewerId?: number) => ({
  tagList: { select: { name: true } },
  author: {
    select: {
      username: true, bio: true, image: true,
      followedBy: viewerId ? { where: { id: viewerId }, select: { id: true } } : false,
    },
  },
  favoritedBy: viewerId ? { where: { id: viewerId }, select: { id: true } } : false,
  _count: { select: { favoritedBy: true } },
});
```

- `favorited = article.favoritedBy?.length > 0`
- `favoritesCount = article._count.favoritedBy`
- `following = article.author.followedBy?.length > 0`

Put this in one shared helper, because the same block is repeated 7 times today. Make `favoriteArticle` and `unfavoriteArticle` return `articleMapper(...)` instead of spreading the raw Prisma object. Apply the same filtered `followedBy` select to comments and profiles. Each of those relation lookups then hits the `_UserFollows` or `_UserFavorites` unique `(A,B)` index and returns at most one row.

**Expected impact:** per-request work stops depending on follower and favorite counts. For popular content that means orders of magnitude less data moved.

---

## 2. Unbounded result sizes: no `limit` cap, no comment pagination (High)

### Where

- `article.service.ts:82-83`: `skip: Number(query.offset) || 0, take: Number(query.limit) || 10`
- `article.service.ts:131-132`: `getFeed` does the same, with values from `article.controller.ts:50-53`
- `article.service.ts:433-458`: `getCommentsByArticle` returns **all** comments for an article. There is no `take` and no `orderBy`.

### Why it matters

- A client can send `GET /api/articles?limit=1000000`. Combined with Finding 1, that loads every article together with every favoriting user and every follower of every author, all into one Node process. One anonymous request like that can exhaust memory or tie up the event loop and a database connection. It works both as an accidental performance cliff and as a cheap denial-of-service vector.
- Negative values or `NaN` fall through `||` in odd ways. For example, `limit=-5` makes Prisma take rows backwards.
- Comment threads grow without limit, and each comment also loads its author's whole `followedBy` list (Finding 1).
- `OFFSET` paging gets slower the deeper the page, because Postgres must scan and discard `offset` rows. That is fine for small tables but adds up on large ones.

### What to do

- Clamp the inputs, for example `limit = Math.min(Math.max(parseInt(limit) || 10, 1), 100)` and `offset = Math.max(parseInt(offset) || 0, 0)`. Do it once in a shared parser used by both `getArticles` and `getFeed`.
- Add `take`, `skip` (or a cursor) and `orderBy: { createdAt: 'desc' }` to the comments query, and expose the same parameters on the route. If the RealWorld contract has to stay as "return all comments", still apply a hard ceiling, for example 500.
- Longer term, move to keyset (cursor) pagination on `(createdAt, id)` for article lists and the feed.

---

## 3. Missing indexes on foreign keys and sort columns (High)

### Where

`src/prisma/schema.prisma` and `migrations/20210924225358_initial/migration.sql`. The only indexes are the primary keys, the unique indexes (`slug`, `email`, `username`, `Tag.name`) and the implicit many-to-many join tables. PostgreSQL does **not** index foreign keys automatically, so these have no index:

| Column | Used by |
|---|---|
| `Article.authorId` | the `author` filter in `getArticles` and `getFeed`; profile-filtered listings (`?author=`); `ON DELETE CASCADE` when a user is deleted |
| `Article.createdAt` | `ORDER BY "createdAt" DESC` in every list and the feed |
| `Comment.articleId` | `getCommentsByArticle`; cascade delete when an article is deleted |
| `Comment.authorId` | the comment author filter; cascade delete when a user is deleted |
| `User.demo` | the `author.demo = true` filter on every article list, comment list and tag query |

### Why it matters

- The default home-page query (`getArticles` with no filters) joins `Article` to `User` on `authorId`, filters on `demo`, sorts by `createdAt` and applies `OFFSET` and `LIMIT`. Without indexes Postgres does a sequential scan plus a sort over the whole `Article` table on **every** page load. It does this twice, because the `count` query repeats the filter.
- Loading comments for one article sequentially scans the whole `Comment` table.
- Deleting an article or a user cascades through `Comment` with sequential scans.

### What to do

Add to `schema.prisma` and generate a migration:

```prisma
model Article {
  ...
  @@index([authorId, createdAt(sort: Desc)])
  @@index([createdAt(sort: Desc)])
}
model Comment {
  ...
  @@index([articleId, createdAt])
  @@index([authorId])
}
model User {
  ...
  @@index([demo])   // or a partial index via raw SQL: CREATE INDEX ... ON "User"(id) WHERE demo
}
```

Check the result with `EXPLAIN (ANALYZE, BUFFERS)` on the SQL Prisma generates for `getArticles` and `getFeed`. You can capture it with `new PrismaClient({ log: ['query'] })`. Use `CREATE INDEX CONCURRENTLY` in production.

---

## 4. Sequential count and page queries, plus Prisma's extra query per `include` (Medium)

### Where

- `getArticles`, `article.service.ts:71-105`
- `getFeed`, `article.service.ts:114-154`

### Why it matters

- `count` is awaited before `findMany` starts, so the request pays for two database round trips one after the other. The count also repeats the relation-filtered join over the whole matching set every time.
- Prisma 4 resolves each `include` (tags, author, author.followedBy, favoritedBy, _count) with **separate follow-up queries**. One `GET /articles` therefore makes roughly 6–7 sequential round trips. Network latency to the database is paid on each one.

### What to do

- Run both at once: `const [articlesCount, articles] = await prisma.$transaction([prisma.article.count(...), prisma.article.findMany(...)])`, or use `Promise.all`.
- After Finding 1 is fixed the follow-up queries become cheap, but there are still several of them. Upgrading to Prisma 5.x and enabling `relationJoins` (`relationLoadStrategy: 'join'`) folds them into one SQL statement with `LATERAL` joins.
- If the exact `articlesCount` on the unfiltered home page becomes a hotspot, cache it for a short time (5–30 s), because it changes rarely relative to how often it is read.

---

## 5. `GET /tags` runs a full aggregate on every request (Medium)

### Where

`src/app/routes/tag/tag.service.ts:16-35`

### Why it matters

The front-end calls this on every home-page load. The query does the following each time:

1. Joins `Tag` to `_ArticleToTag` to `Article` to `User`.
2. Filters on `demo` or the viewer's id.
3. Counts articles per tag.
4. Sorts by that count.

That is a full aggregation over the tag-article join table on every call. It is also needlessly personalized: logged-in users get a different query (`OR id = viewer`), which defeats any shared caching.

### What to do

- Cache the result in process memory, or in Redis if you run several instances, with a short TTL (30–60 s). Popular tags almost never need to be real-time. The anonymous (demo-only) result can be shared by everyone.
- If it still matters at scale, keep a materialized view or a counter table (`Tag.articleCount`) updated on article create, update and delete, and read the top 10 from an index.
- Make sure `_ArticleToTag_B_index` is in place (it is) and that Finding 3's `User.demo` index exists.

---

## 6. bcryptjs runs hashing on the main thread (Medium)

### Where

`src/app/routes/auth/auth.service.ts`: line 58 (`bcrypt.hash(password, 10)` on register), line 111 (`bcrypt.compare` on login) and line 156 (`hash` on user update).

### Why it matters

`bcryptjs` is a pure-JavaScript implementation. Its "async" API only splits the work into chunks with `setImmediate`. It does not move the work off the event loop. At cost 10, each hash or compare costs tens of milliseconds of CPU on the single JS thread, a few times slower than native code. A burst of logins or sign-ups, or a credential-stuffing attack, therefore raises latency for **every** request the process is serving, including reads that have nothing to do with auth.

### What to do

- Switch to the native `bcrypt` package or to `argon2`. Both run on the libuv thread pool, so the event loop stays free. Existing `$2a$` and `$2b$` hashes stay compatible with `bcrypt`. Size `UV_THREADPOOL_SIZE` to match.
- Add rate limiting on `/users/login` and `/users`, for example with `express-rate-limit`, so hashing work cannot be used to amplify an attack.

---

## 7. `updateArticle` makes several sequential round trips with no transaction (Medium)

### Where

`article.service.ts:289-383`

### Why it matters

One update runs these steps one after the other:

1. A `findFirst` to check ownership.
2. A `findFirst` to check the new slug is unique.
3. `disconnectArticlesTags`, which is a whole `UPDATE` just to clear tags.
4. The final `update`, whose `connectOrCreate` runs a lookup or insert **for each tag**, plus the heavy include from Finding 1.

That is 4 + N round trips. They are not atomic, so a failure after step 3 leaves the article with no tags. (A smaller point: `await await` at lines 292 and 386 does nothing harmful but is a typo.)

### What to do

- Merge steps 3 and 4. Prisma accepts `tagList: { set: [], connectOrCreate: [...] }` in the same `update`.
- Use `findUnique` on `slug`, which is unique, and select `authorId` directly instead of joining to `author`.
- Drop the separate slug-uniqueness lookup. Let the unique constraint enforce it and turn Prisma error `P2002` into the 422 response. Do the same in `createArticle` (lines 180–191).
- Wrap whatever reads and writes remain in `prisma.$transaction(async tx => ...)`.
- For many tags, `createMany({ data, skipDuplicates: true })` on `Tag` followed by one `connect` is cheaper than N `connectOrCreate`s.

---

## 8. Smaller round-trip savings (Low)

- **`checkUserUniqueness`** (`auth.service.ts:9-36`) makes two sequential `findUnique` calls. Replace them with one `findMany({ where: { OR: [{ email }, { username }] }, select: { email: true, username: true } })`, or rely on the unique constraints and map `P2002`.
- **`deleteArticle`** (`article.service.ts:385-414`) and **`deleteComment`** (`527-560`) each look up the row, then delete it. They could use a single `deleteMany({ where: { slug, authorId: id } })` and read `count` to decide between 404 and 403, or at least use `findUnique` and select only `authorId`. `deleteComment` already filters by author in the lookup, so its 403 branch can never be reached.
- **`addComment`** (`473-525`) looks up the article by slug and then creates the comment. It could use `article: { connect: { slug } }` directly, which saves a round trip, and it would then fail cleanly when the slug does not exist instead of attempting `connect: { id: undefined }`.
- **`getCurrentUser`** (`131-149`) hits the database and signs a fresh JWT on every `GET /user`. Signing is cheap, so this is acceptable. Just be aware that it is one query per page load on the front-end.
- **List payloads include the full `body`** of every article (`article.mapper.ts:7`). Lists only need title and description. Dropping `body` from list responses (`select` instead of `include` on scalar fields) reduces the bytes read from Postgres and sent over the network. Newer versions of the RealWorld spec already omit it.

---

## 9. Runtime and deployment configuration (Low–Medium)

- **No response compression** (`src/main.ts`). JSON article lists compress well, often 5–10×. Add the `compression` middleware, or terminate gzip or brotli at a reverse proxy.
- **`NODE_ENV` is not set to `production`** in the `Dockerfile`. Express and several dependencies take slower development code paths without it. Add `ENV NODE_ENV=production`.
- **Prisma connection pool left at its default** (`num_cpus * 2 + 1`), with no `pool_timeout`. Set `connection_limit` and `pool_timeout` in `DATABASE_URL` to fit your Postgres `max_connections` and the number of instances. Add a PgBouncer-style pooler if you scale out horizontally.
- **One Node process.** `app.listen` runs on one core. Run several instances (container replicas, PM2 or `cluster`) behind a load balancer. This matters more while Finding 6 is unfixed.
- **No timeouts.** There is no HTTP server or request timeout and no Postgres `statement_timeout`. A runaway query (see Finding 2) holds a connection for as long as it runs. Set `statement_timeout`, for example `?options=-c%20statement_timeout%3D5000` or via the role, and set server `requestTimeout` and `headersTimeout`.
- **No query observability.** Turn on Prisma query logging or metrics (`log: [{ emit: 'event', level: 'query' }]` with duration) in staging, so the fixes above can be verified and regressions caught.

---

## Suggested order of work

1. **Finding 1.** Shared `articleInclude(viewerId)` helper, filtered relations, `_count` for counts, mapper-based responses for favorite and unfavorite. This removes the biggest cost and the password-hash leak.
2. **Finding 2.** Clamp `limit` and `offset`, and paginate comments. This is a small change that closes the denial-of-service vector.
3. **Finding 3.** Index migration, verified with `EXPLAIN ANALYZE`.
4. **Findings 4 and 5.** Parallel count and page queries; cache tags.
5. **Findings 6 and 7.** Native bcrypt and rate limiting; rework `updateArticle`.
6. **Findings 8 and 9.** Smaller round-trip and configuration fixes; consider Prisma 5 with `relationJoins`.

To confirm the fixes, seed the database at a realistic size, for example 10k users, 100k articles, an author with 50k followers and an article with 20k favorites. Load-test `GET /api/articles`, `GET /api/articles/:slug/comments`, `GET /api/profiles/:username` and `GET /api/tags` with `autocannon` or `k6` before and after the changes, and compare p95 latency, RSS memory and the number of database queries per request.
