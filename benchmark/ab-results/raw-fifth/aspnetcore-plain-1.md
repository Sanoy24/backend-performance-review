# Performance Review: aspnetcore-realworld-example-app (Conduit API)

Scope: `src/Conduit` (ASP.NET Core 10, EF Core 10, Mediator, SQLite by default). All paths below are relative to `src/Conduit/`.

## Summary

The application is functionally simple, but its data access pattern does not scale with data volume. The three problems that matter most:

1. **No database indexes on the columns every request filters by** (`Articles.Slug`, `Persons.Username`, `Persons.Email`, `Articles.CreatedAt`). Almost every endpoint does at least one full table scan, and the authenticated ones do two or more.
2. **Article reads load whole child collections to compute scalars.** Each article in a list loads every `ArticleFavorite` row just to produce `favoritesCount`, in one cartesian-joined query that also pages. The cost grows with how popular the articles are, not with page size.
3. **Several hot paths load unbounded collections they barely use.** Profile view loads all of the viewer's follow rows in both directions. Comment create and delete load every comment on the article. The feed and article list load the viewer's whole follow list.

Infrastructure adds a fixed per-request cost on top. Every request, including GETs, runs inside a synchronously opened and committed DB transaction. Every SQL statement is logged twice to a synchronous console. The article list issues a synchronous `COUNT`.

Severity scale: **Critical** means it degrades badly with data growth or under load and should be fixed first. **High** means a significant cost on a hot path. **Medium** means a real but bounded cost. **Low** means cleanup or an easy win.

| # | Finding | Severity | Where |
|---|---------|----------|-------|
| 1 | Missing indexes on lookup, sort and uniqueness columns | Critical | `Infrastructure/ConduitContext.cs` |
| 2 | Article reads load all favorites/tags rows (cartesian join, count-by-materialization) | Critical | `Features/Articles/ArticleExtensions.cs`, `List.cs`, `Details.cs`, `Favorites/*`, `Edit.cs` |
| 3 | Synchronous `queryable.Count()` and a full recount on every list/feed request | High | `Features/Articles/List.cs:133` |
| 4 | Every request (including GETs) wrapped in a synchronous DB transaction | High | `Infrastructure/DBContextTransactionPipelineBehavior.cs`, `ConduitContext.cs:94-136` |
| 5 | Profile read loads the viewer's entire Following and Followers collections | High | `Features/Profiles/ProfileReader.cs:36-49` |
| 6 | Comment create/delete load every comment (and author) on the article | High | `Features/Comments/Create.cs:36-38`, `Delete.cs:27-36` |
| 7 | Logging: every SQL command logged at Information to two synchronous console sinks | High | `ServicesExtensions.cs:110-125`, `Program.cs:105` |
| 8 | Feed / list: viewer's follow list loaded in full, extra lookup round trips | Medium | `Features/Articles/List.cs:33-131` |
| 9 | Comment list is unbounded, tracked, and loads the full article row | Medium | `Features/Comments/List.cs:22-32` |
| 10 | Article create: per-tag SaveChanges and an O(n) query loop for unique slugs | Medium | `Features/Articles/Create.cs:59-82`, `Edit.cs:94-105` |
| 11 | Write endpoints do redundant round trips (favorite/follow: 4-5 queries) | Medium | `Features/Favorites/*.cs`, `Features/Followers/*.cs`, `Articles/Edit.cs:140-143` |
| 12 | Current user resolved by username string on every authenticated request | Medium | `Infrastructure/CurrentUserAccessor.cs` + all handlers |
| 13 | SQLite hard-coded, no WAL, no pooling; config env vars ignored | Medium | `Program.cs:15-45` |
| 14 | Tags endpoint reads the whole table on every call, uncached | Low | `Features/Tags/List.cs` |
| 15 | Small per-request overheads (JWT handler, HMAC, legacy MVC, Swagger in prod) | Low | various |

---

## 1. Missing indexes on lookup, sort and uniqueness columns (Critical)

**Where:** `Infrastructure/ConduitContext.cs` `OnModelCreating`. The schema is created with `Database.EnsureCreated()` (`Program.cs:120-126`), so the only indexes are the primary keys and the ones EF adds automatically for foreign keys.

**Problem:** these predicates run on nearly every request, and none of their columns is indexed:

- `Articles.Slug`: `x.Slug == ...` in Article Details/Edit/Delete, Favorites Add/Delete, Comments List/Create/Delete, and the slug uniqueness loops in Create/Edit.
- `Persons.Username`: the current-user lookup in almost every authenticated handler, `ProfileReader`, the author and favorited filters in `List`, Followers Add/Delete, and Users Create/Edit.
- `Persons.Email`: Login (`Login.cs:48-50`), the uniqueness checks in Users Create/Edit.
- `Articles.CreatedAt`: `OrderByDescending(x => x.CreatedAt).ThenByDescending(x => x.ArticleId)` in `List.cs:106-107`. Without an index the database sorts the whole filtered set for every page.

Each of these is a full table scan. Login, profile view and article view therefore scale linearly with the number of users or articles. A typical authenticated "view an article with comments" flow costs several scans.

Nothing enforces uniqueness either. Slug, username and email are protected only by check-then-insert (`AnyAsync` followed by `SaveChanges`), which races under concurrency. A unique index fixes both the speed and the correctness problem.

**Fix:**
```csharp
modelBuilder.Entity<Article>(b =>
{
    b.HasIndex(x => x.Slug).IsUnique();
    b.HasIndex(x => new { x.CreatedAt, x.ArticleId }); // supports the list ordering
});
modelBuilder.Entity<Person>(b =>
{
    b.HasIndex(x => x.Username).IsUnique();
    b.HasIndex(x => x.Email).IsUnique();
});
```
- Also consider `FollowedPeople(TargetId, ObserverId)` and `ArticleFavorites(PersonId, ArticleId)`. EF creates single-column indexes on `TargetId` and `PersonId`, which is usually enough, but the composites make the reverse lookups covering.
- Move from `EnsureCreated` to EF migrations. Existing databases will not pick up new indexes otherwise, because `EnsureCreated` is a no-op when the DB already exists.
- Catch `DbUpdateException` on unique-constraint violation and map it to the existing 409/422 responses. You can then drop the separate `AnyAsync` pre-checks or keep them only as a fast path.

---

## 2. Article reads load all favorites and tags rows (Critical)

**Where:**
- `Features/Articles/ArticleExtensions.cs:9-14`: `GetAllData()` = `Include(Author).Include(ArticleFavorites).Include(ArticleTags)`.
- `Domain/Article.cs:28-36`: `Favorited => ArticleFavorites.Count != 0`, `FavoritesCount => ArticleFavorites.Count`, `TagList` computed from `ArticleTags`.
- Used by `List.cs:31` (list and feed), `Details.cs:28-30`, `Edit.cs:140-143`, `Favorites/Add.cs:69-71`, and `Favorites/Delete.cs:57-59`.

**Problems:**

1. **Count by materialization.** To show `favoritesCount`, EF loads every `ArticleFavorite` row for every article returned. An article with 50k favorites transfers and materializes 50k rows on each view. The same work happens again on every favorite/unfavorite click, which re-reads the article through `GetAllData`.
2. **Cartesian explosion.** Two collection `Include`s in a single query (EF's default is a single query) produce `favorites × tags` rows per article. An article with 1,000 favorites and 5 tags returns 5,000 rows for one article. In a 20-article list page, that is multiplied across every article.
3. **Paging with collection includes.** With `Skip/Take` plus collection `Include`, EF has to wrap the paged query in a subquery and join collections onto it. It also emits a warning that ordering must be unique (it is here, thanks to `ThenByDescending(ArticleId)`). Combined with point 2, a page-size of 20 can easily return thousands of rows.
4. **Correctness issue that shares the fix:** `Favorited` is true if *anyone* has favorited the article, not the current user. The fix below computes it correctly at no extra cost.
5. `Article.Body` is loaded for list queries and then nulled in memory (`List.cs:114-117`). With large article bodies, that is wasted I/O and memory.

**Fix:** stop returning the EF entity and project to a response DTO that computes scalars in SQL:
```csharp
var currentPersonId = /* from claim, see #12 */;
var page = await queryable
    .OrderByDescending(a => a.CreatedAt).ThenByDescending(a => a.ArticleId)
    .Skip(offset).Take(limit)
    .Select(a => new ArticleDto
    {
        Slug = a.Slug, Title = a.Title, Description = a.Description,
        // Body omitted for lists
        CreatedAt = a.CreatedAt, UpdatedAt = a.UpdatedAt,
        TagList = a.ArticleTags.Select(t => t.TagId).ToList(),
        FavoritesCount = a.ArticleFavorites.Count(),
        Favorited = currentPersonId != null && a.ArticleFavorites.Any(f => f.PersonId == currentPersonId),
        Author = new ProfileDto
        {
            Username = a.Author.Username, Bio = a.Author.Bio, Image = a.Author.Image,
            Following = currentPersonId != null && a.Author.Following.Any(f => f.ObserverId == currentPersonId)
        }
    })
    .ToListAsync(ct);
```
This turns the count into a correlated `COUNT(*)` that uses the `ArticleFavorites` PK. The only collection that still gets loaded is tags, which are few per article. It also removes the separate "followed IDs" query (see #8).

If a full projection is too large a change, take these two steps first:
- Add `.AsSplitQuery()` to `GetAllData()` to stop the cartesian product.
- Replace the `Include(ArticleFavorites)` with a projected count.

For very hot articles, consider a denormalized `FavoritesCount` column that is updated in the favorite/unfavorite transaction. Increment it with `ExecuteUpdateAsync(s => s.SetProperty(a => a.FavoritesCount, a => a.FavoritesCount + 1))`.

---

## 3. Synchronous, full recount on every list and feed request (High)

**Where:** `Features/Articles/List.cs:133`
```csharp
return new ArticlesEnvelope { Articles = articles, ArticlesCount = queryable.Count() };
```

**Problems:**
- `Count()` is the **synchronous** EF API inside an async handler. It blocks a thread-pool thread for the duration of a DB round trip. Under load this causes thread-pool starvation, so request latency rises across the whole app, not just this endpoint.
- The count re-runs the full filter (feed `IN (...)`, tag subquery, favorited `EXISTS`) over the entire table on every page request. Without the indexes from #1 it is another full scan.

**Fix:**
- Use `await queryable.CountAsync(cancellationToken)`.
- Build the count from the base filtered query without `Include`s and without `OrderBy`. EF ignores includes for counts, but keeping the two queries explicit avoids accidents.
- If the count becomes a hotspot on large tables, consider caching total counts for the unfiltered "global" list for a few seconds, or capping counts.

---

## 4. Every request runs inside a synchronous DB transaction (High)

**Where:** `ServicesExtensions.cs:26-30` registers `DBContextTransactionPipelineBehavior<,>` for **all** Mediator requests. `Infrastructure/DBContextTransactionPipelineBehavior.cs:27-31` and `ConduitContext.cs:94-136` call `Database.BeginTransaction(IsolationLevel.ReadCommitted)`, `Commit()` and `Rollback()`, all synchronous.

**Problems:**
- **Read-only endpoints pay for a transaction.** Article list, article details, comments, profile, tags and current user each get extra `BEGIN` and `COMMIT` round trips. The connection is also held open for the full duration of the handler, including CPU work.
- **Sync I/O.** `BeginTransaction` and `Commit` block thread-pool threads (same starvation risk as #3).
- **On SQLite**, `BeginTransaction(ReadCommitted)` maps to a SQLite transaction. Microsoft.Data.Sqlite upgrades ReadCommitted to Serializable, and the transaction is opened with `BEGIN` (deferred on recent versions, immediate on some). Once a handler writes, it holds the database's single write lock until commit, and that includes the time spent in every following round trip. Wrapping reads in transactions also lengthens lock windows for concurrent writers.
- **Multi-statement commands already work without it.** `SaveChanges` is already atomic per call. The explicit transaction is only needed in handlers that call `SaveChanges` more than once (`Articles/Create.cs`, because of the per-tag save; see #10).

**Fix:**
- Restrict the behavior to commands. One way is a marker interface (`ICommand`/`ITransactionalRequest`) with a generic constraint on the behavior. Another is to skip it when the request type implements `IQuery`.
- Use the async APIs: `await Database.BeginTransactionAsync(ct)`, `await tx.CommitAsync(ct)`, `await tx.RollbackAsync(ct)`.
- Better still, remove the multiple `SaveChanges` calls inside handlers so that a single `SaveChangesAsync` (implicitly transactional) suffices and the behavior can be deleted.

---

## 5. Profile read loads the viewer's entire follow graph (High)

**Where:** `Features/Profiles/ProfileReader.cs:36-49`
```csharp
var currentPerson = await context.Persons
    .Include(x => x.Following)
    .Include(x => x.Followers)
    .FirstOrDefaultAsync(x => x.Username == currentUserName, ct);
...
if (currentPerson.Followers.Any(x => x.TargetId == person.PersonId))
```

**Problems:**
- To answer one yes/no question ("does the viewer follow X?"), the code loads **every** `FollowedPeople` row where the viewer is observer **and** every row where the viewer is target. A popular user with 100k followers who views any profile pulls 100k+ rows into memory.
- The query is tracked (no `AsNoTracking`), so EF also builds change-tracking snapshots for all of those rows.
- Two collection includes in one query means a cartesian product: `|following| × |followers|` rows. For a user with 1k followers and 1k following, that is about 1M rows.
- `ProfileReader` is also called at the end of Follow and Unfollow (`Followers/Add.cs:72`, `Delete.cs:64`), so every follow click pays this cost as well.

**Fix:** a single `EXISTS` query:
```csharp
profile.IsFollowed = currentUserName != null && await context.FollowedPeople
    .AnyAsync(f => f.Observer!.Username == currentUserName && f.TargetId == person.PersonId, ct);
```
Or, with the person ID from the token (#12): `f.ObserverId == currentPersonId && f.TargetId == person.PersonId`, which is a PK seek. You can also fold it into the first query as a projection (`Select(p => new { p, IsFollowed = p.Following.Any(...) })`) for a single round trip.

---

## 6. Comment create and delete load every comment on the article (High)

**Where:**
- `Features/Comments/Create.cs:36-38`: `Articles.Include(x => x.Comments)`, tracked, then `article.Comments.Add(comment)`.
- `Features/Comments/Delete.cs:27-36`: `Articles.Include(x => x.Comments).ThenInclude(x => x.Author)`, then a linear search in memory for `CommentId`.

**Problem:** adding or deleting one comment materializes and change-tracks all comments on the article (plus their authors on delete). An article with 10k comments makes each comment post load 10k rows. `DetectChanges` during `SaveChanges` then walks all of them. The cost is O(comments per article) per write, and it gets worse exactly on the most active articles.

**Fix:**
- Create: fetch only the article ID, then insert the comment by foreign key:
  ```csharp
  var articleId = await context.Articles.Where(a => a.Slug == slug).Select(a => (int?)a.ArticleId).FirstOrDefaultAsync(ct)
      ?? throw NotFound;
  context.Comments.Add(new Comment { ArticleId = articleId, AuthorId = currentPersonId, ... });
  ```
- Delete: query the single comment directly:
  ```csharp
  var comment = await context.Comments
      .Where(c => c.CommentId == id && c.Article!.Slug == slug)
      .Select(c => new { c.CommentId, AuthorName = c.Author!.Username })
      .FirstOrDefaultAsync(ct);
  // check ownership, then:
  await context.Comments.Where(c => c.CommentId == id).ExecuteDeleteAsync(ct);
  ```

---

## 7. Logging overhead: every SQL statement logged twice, synchronously (High)

**Where:** `ServicesExtensions.cs:110-125`, `Program.cs:105`.

**Problems:**
- Serilog is configured with `MinimumLevel.Verbose()` and added *in addition to* the default ASP.NET Core providers (`loggerFactory.AddSerilog(log)`). Each log event goes to both the built-in console logger and Serilog's console sink.
- There is no `appsettings.json` and no `Logging` filter configuration, so the effective Microsoft.Extensions.Logging level is `Information`. At that level EF Core logs every `Executed DbCommand` with the SQL text, and ASP.NET Core logs request start and finish plus MVC action selection and execution. Given the query counts above (often 3-6 per request), that means more than 10 console writes per request.
- `Serilog.Sinks.Console` writes synchronously with an ANSI theme. Console I/O is slow and serialized by a lock, so under load it becomes a global contention point and adds directly to request latency. This is often one of the largest throughput limits in a containerized .NET API.

**Fix:**
- Use `builder.Host.UseSerilog(...)` (or `builder.Services.AddSerilog`) so Serilog *replaces* the default providers instead of duplicating them.
- Set production levels: `MinimumLevel.Information()`, `.MinimumLevel.Override("Microsoft", LogEventLevel.Warning)`, `.MinimumLevel.Override("Microsoft.EntityFrameworkCore.Database.Command", LogEventLevel.Warning)`. Make them configurable via `appsettings.{Environment}.json`.
- Wrap the console sink with `Serilog.Sinks.Async` (`WriteTo.Async(a => a.Console(...))`), drop the ANSI theme in containers, and prefer a compact JSON formatter.
- Add `app.UseSerilogRequestLogging()` for a single summary line per request, in place of the many framework lines.

---

## 8. Feed and list: viewer's follow list loaded in full, plus extra round trips (Medium)

**Where:** `Features/Articles/List.cs`

1. **Feed (lines 37-50).** This loads the current `Person` *with* the full `Followers` collection (the rows where the person is observer, despite the name). It then filters with `currentUser.Followers.Select(y => y.TargetId).Contains(...)`. The ID list goes to SQL as a parameter (`json_each`/`OPENJSON` in EF 8+). For a user following thousands of authors, every feed request transfers that list twice: once out, once back in as a parameter. Fix: use a correlated subquery so the filter stays in SQL:
   ```csharp
   queryable = queryable.Where(a => a.Author!.Following.Any(f => f.ObserverId == currentPersonId));
   ```
   (`Person.Following` holds rows where this person is the target, so `a.Author.Following` is that author's followers.)
2. **Tag filter (lines 55-63).** An extra round trip (`ArticleTags.FirstOrDefaultAsync`) checks that the tag exists before filtering. The filter alone returns an empty set when the tag is unknown, so drop the pre-query: `queryable.Where(a => a.ArticleTags.Any(t => t.TagId == message.Tag))`.
3. **Author and favorited filters (lines 73-101).** Each makes a separate `Persons` lookup by unindexed username. Fold them into the main query: `Where(a => a.Author!.Username == message.Author)` and `Where(a => a.ArticleFavorites.Any(f => f.Person!.Username == message.FavoritedUsername))`.
4. **Following flag (lines 120-131).** This loads *all* `TargetId`s the viewer follows to set `author.following` on at most 20 authors. It also does `List.Contains` per article (O(n·m)). Fix: compute it in the projection from #2. If you keep the current approach, restrict it to the page's author IDs (`Where(f => f.ObserverId == me && pageAuthorIds.Contains(f.TargetId))`) and use a `HashSet<int>`.
5. `Limit` is not validated, so a client can request `limit=1000000`. Clamp it (for example to `1..100`).

Together these cut the feed from 4-5 round trips with unbounded transfers down to 2 (page + count).

---

## 9. Comment list is unbounded, tracked, and loads the full article row (Medium)

**Where:** `Features/Comments/List.cs:22-32`

**Problems:**
- The query loads the whole `Article` entity, including the potentially large `Body`, just to reach its comments.
- There is no `AsNoTracking()`, so every comment and author is change-tracked for a read-only response.
- There is no limit. The RealWorld spec does not paginate comments, but a hot article returns its entire history in one response.
- The response is unordered, so the database is free to return any order.

**Fix:** query comments directly with a projection:
```csharp
var comments = await context.Comments.AsNoTracking()
    .Where(c => c.Article!.Slug == slug)
    .OrderByDescending(c => c.CreatedAt)
    .Select(c => new CommentDto { Id = c.CommentId, Body = c.Body, CreatedAt = c.CreatedAt, UpdatedAt = c.UpdatedAt,
        Author = new ProfileDto { Username = c.Author!.Username, Bio = c.Author.Bio, Image = c.Author.Image,
            Following = me != null && c.Author.Following.Any(f => f.ObserverId == me) } })
    .ToListAsync(ct);
```
If the result is empty you can tell "article not found" from "no comments" with a cheap `Articles.AnyAsync(a => a.Slug == slug)`. Consider a safety cap or optional pagination parameters. The composite index `Comments(ArticleId, CreatedAt)` supports this query.

---

## 10. Article create: per-tag SaveChanges and an O(n) slug probe loop (Medium)

**Where:** `Features/Articles/Create.cs:59-82`, and the same slug loop in `Edit.cs:94-105`.

**Problems:**
- **Tags.** For each tag, the code calls `FindAsync` (one round trip) and, if the tag is new, `SaveChangesAsync` (another round trip and write). An article with 10 new tags makes about 20 round trips plus 10 write statements, all while holding the transaction from #4. (`FindAsync` is also called without the cancellation token.)
- **Slug uniqueness.** `for (i = 1; await AnyAsync(Slug == uniqueSlug); i++)` probes `slug`, `slug-1`, `slug-2`, and so on, one query each. Common titles ("hello-world", "test") make this O(k) queries for the k-th duplicate, and O(k²) in total across all duplicates. Without the index from #1, each probe is also a full table scan. The loop also races: two concurrent creates can choose the same slug.

**Fix:**
- Tags: load all existing tags in one query, then add the missing ones, then do a single `SaveChangesAsync`:
  ```csharp
  var names = (message.Article.TagList ?? []).Distinct().ToList();
  var existing = await context.Tags.Where(t => names.Contains(t.TagId!)).ToListAsync(ct);
  var missing = names.Except(existing.Select(t => t.TagId!)).Select(n => new Tag { TagId = n });
  context.Tags.AddRange(missing);
  ```
- Slug: either fetch all colliding slugs at once (`Where(a => a.Slug == slug || a.Slug!.StartsWith(slug + "-")).Select(a => a.Slug)`) and pick the next free suffix in memory, or append a short random or ID-based suffix. Either way, rely on the unique index from #1 and retry on violation to close the race.

---

## 11. Write endpoints do redundant round trips (Medium)

**Where:**
- `Features/Favorites/Add.cs` and `Delete.cs`: (1) load article by slug, (2) load person by username, (3) load existing favorite, (4) save, (5) re-load the article with `GetAllData()` (which pulls every favorite row, #2). That is five round trips, all inside the per-request transaction.
- `Features/Followers/Add.cs` and `Delete.cs`: target lookup, observer lookup, existing-row lookup, save, then `ProfileReader` repeats the target lookup and does the expensive viewer load (#5).
- `Features/Articles/Edit.cs:140-143`: after saving, the article is fully re-queried through `GetAllData()` even though the tracked entity is already in memory.

**Fix:**
- Take the person ID from the JWT (#12) so the "load current person" query disappears.
- Favorites: insert or delete by key directly, for example `ExecuteDeleteAsync(f => f.ArticleId == id && f.PersonId == me)`. Insert idempotently by catching the PK violation, or use the existing `AnyAsync` check. Then return the article via the single projection query from #2.
- Follow/unfollow: same pattern, then build the profile from the already loaded `target` with `IsFollowed = true/false`. No `ProfileReader` call is needed.
- Edit: build the response from the tracked entity, or use one projection query.

Each of these endpoints goes from 5-6 round trips to 2-3.

---

## 12. Current user resolved by username string on every authenticated request (Medium)

**Where:** `Infrastructure/CurrentUserAccessor.cs`, `Infrastructure/Security/JwtTokenGenerator.cs:16`, and the `Persons.FirstAsync(x => x.Username == currentUserAccessor.GetCurrentUsername())` pattern in Articles Create, Comments Create, Favorites, Followers, the List feed, ProfileReader and Users Edit.

**Problem:** the JWT carries only the username (`sub`). Every authenticated handler therefore needs an extra `Persons` lookup by an unindexed string just to get `PersonId`. That is one more round trip and one more table scan per request. The accessor also does a linear `Claims.FirstOrDefault` each time it is called. That cost is small, but it is called several times per request, including from inside query lambdas.

**Fix:**
- Add a `PersonId` claim when issuing the token and expose `GetCurrentPersonId()`. Use it directly in predicates: `f.ObserverId == me`, `new Comment { AuthorId = me }`, and so on.
- Use `User.FindFirstValue(...)`, and cache the result in a field on the scoped accessor.
- Handle renames: `Users/Edit.cs` can change the username, so the ID is also the more robust key. Note that the current token keeps the old username until it is re-issued.

---

## 13. Database configuration: SQLite hard-coded, no WAL, no pooling (Medium)

**Where:** `Program.cs:15-45`. The provider and connection string are literals. The comments say they come from environment variables, and `docker-compose.yml` sets `ASPNETCORE_Conduit_DatabaseProvider`/`ConnectionString`, but those settings are never read.

**Problems:**
- The deployed container always runs SQLite on a local file (`Filename=realworld.db`). SQLite allows a single writer. In the default rollback-journal mode, writers also block readers, and that combines badly with the "transaction around every request" from #4. Under concurrent writes, requests queue on the DB lock and can fail with `SQLITE_BUSY`.
- Every request constructs a new `DbContext`, because `AddDbContext` is used without pooling. That is a minor allocation cost, but it is free to fix.

**Fix:**
- Read `DatabaseProvider` and `ConnectionString` from configuration (`builder.Configuration["Conduit:DatabaseProvider"]`, and so on) so that the SQL Server option, or PostgreSQL via Npgsql, can actually be used in production.
- If SQLite stays: enable WAL once at startup (`PRAGMA journal_mode=WAL;`), set `Cache=Shared` or a busy timeout (`Default Timeout=` / `PRAGMA busy_timeout`), and keep write transactions short (see #4).
- Use `AddDbContextPool<ConduitContext>(...)`. `ConduitContext` holds a `_currentTransaction` field, so that field must be reset, or the transaction behavior removed as recommended in #4, before pooling is safe.

---

## 14. Tags endpoint reads the whole table on every call, uncached (Low)

**Where:** `Features/Tags/List.cs:22-29`

**Problem:** `GET /api/tags` is requested on every home-page load by the reference frontend. Each call reads and sorts the full `Tags` table, inside the per-request transaction (#4). The table only grows, and orphaned tags are never deleted.

**Fix:**
- Project directly (`Select(t => t.TagId)`).
- Cache the result in `IMemoryCache` or with ASP.NET Core output caching (`[OutputCache(Duration = 60)]`) and invalidate it on article create or edit.
- Consider returning only "popular" tags, for example the top N by `ArticleTags` count, instead of all tags ever created.

---

## 15. Small per-request overheads (Low)

- **`JwtTokenGenerator.cs:33`** creates `new JwtSecurityTokenHandler()` per token. Use a static instance, or switch to `JsonWebTokenHandler` (faster and lower allocation, and it is what JwtBearer uses internally in recent versions).
- **`PasswordHasher.cs`** allocates a new `HMACSHA512` per scope and hashes via `ComputeHashAsync(new MemoryStream(...))`, an async stream wrapper over an in-memory buffer. Use the static one-shot `HMACSHA512.HashData(key, data)`. Separately, and more importantly, a fast keyed hash is not a suitable password hash. Moving to PBKDF2 (`Rfc2898DeriveBytes.Pbkdf2`) or Argon2 is a security requirement. It will deliberately make login and register CPU-heavy (tens of ms), so plan capacity and rate-limit `/users/login` accordingly. Also compare hashes with `CryptographicOperations.FixedTimeEquals` rather than `SequenceEqual`.
- **`Program.cs:79-88`** uses `AddMvc` with `EnableEndpointRouting = false` and `UseMvc()`. That is legacy routing and it also registers Razor view services the API does not use. Switch to `AddControllers()` + `MapControllers()` (endpoint routing). This is a small but free win in routing and startup cost, and it is required for features like output caching (#14).
- **`Program.cs:115-118`** serves Swagger and the Swagger UI in every environment. Gate them behind `app.Environment.IsDevelopment()`.
- **Entity serialization:** `Article` and `Comment` domain entities are serialized directly. `Article.Comments` (always an empty list) appears in every article payload, and nothing prevents accidentally serializing a loaded navigation graph. The DTO projections in #2 and #9 fix this as well.
- **No response compression.** Article bodies and lists are text-heavy. `AddResponseCompression()` (Brotli/Gzip) helps if a reverse proxy is not already compressing.

---

## Suggested order of work

1. **Indexes and migrations (#1)**: the biggest win for the least code, and they also fix uniqueness races.
2. **Logging configuration (#7)** and **remove the transaction from reads, make it async (#4)**: small config changes with an immediate throughput gain on every request.
3. **Article projection DTO (#2, #3, #8)**: one refactor of `List`/`Details`/`Favorites` that removes the cartesian queries, the materialized counts, the sync count and the extra lookups.
4. **Profile and comment fixes (#5, #6, #9)**: small, local query rewrites.
5. **PersonId claim (#12)**, then simplify the write paths (#10, #11).
6. **DB configuration (#13)**, then the low-severity items.

To verify, seed a dataset with thousands of users, articles, favorites and follows (a few "celebrity" users and articles with 10k+ favorites/followers/comments). Enable EF command logging at Debug only during the test, and load-test `GET /articles`, `GET /articles/feed`, `GET /profiles/{u}`, `POST /articles/{slug}/comments` and `POST /articles/{slug}/favorite`. Compare query count per request, rows read and p95 latency before and after each step.
