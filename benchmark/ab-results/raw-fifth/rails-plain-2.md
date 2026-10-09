# Performance Review: rails-realworld-example-app (Conduit API)

**Stack:** Rails 4.2.6, SQLite3, Puma, Devise + JWT, Jbuilder, acts-as-taggable-on 3.5.0, acts_as_follower 0.2.1.

## Summary

The app is small and most single-record endpoints are fine. The cost is concentrated in the **list endpoints** (`GET /api/articles`, `GET /api/articles/feed`, `GET /api/articles/:slug/comments`) and in **`GET /api/tags`**. These are the highest-traffic endpoints in a RealWorld/Conduit client: the home page calls articles + tags on every load. Their cost grows with page size and table size:

- Every article row in a list triggers about 3 extra queries (tags, "favorited?", "following?"). A default 20-item page costs about 64 queries, and a client can request any page size.
- Comments are listed with no eager loading and no pagination.
- The tag cloud runs a GROUP BY over the whole `taggings` table on every request, even though a counter cache already exists.
- Listing and feed queries sort on `articles.created_at`, which has no index.
- Production runs on SQLite with a 5-connection pool, which serializes writes and can't scale beyond one host.

Ranked findings:

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | N+1 queries in article list/feed serialization (tags, favorited?, following?) | Critical | Medium |
| 2 | Unbounded `limit` / `offset` on article list and feed | High | Low |
| 3 | Comments index: N+1 on author + following?, no pagination | High | Low |
| 4 | Tags endpoint aggregates the entire `taggings` table on every request | High | Low |
| 5 | Missing indexes: `articles.created_at`, composite `favorites` and `follows` indexes | High | Low |
| 6 | SQLite as the production database | High (at any real scale) | Medium |
| 7 | Separate `COUNT(*)` on every list request | Medium | Low–Medium |
| 8 | Tag filtering uses case-insensitive `LIKE` lookups and multi-join plans | Medium | Medium |
| 9 | Cascading `dependent: :destroy` loads and deletes rows one at a time; broken `Article has_many :articles` | Medium | Low |
| 10 | Favorite/follow toggles: find-or-create races, extra reloads, no unique constraints | Medium | Low |
| 11 | Production logging at `:debug` | Medium | Trivial |
| 12 | No HTTP or application caching on public read endpoints | Low–Medium | Medium |
| 13 | Minor per-request overhead (full `rails/all` stack, params key rewriting, Devise `super` in `current_user`) | Low | Low |

---

## 1. N+1 queries when serializing article lists — Critical

**Where**
- `app/controllers/articles_controller.rb:5` (`index`) and `:17` (`feed`). Both use only `includes(:user)`.
- `app/views/articles/_article.json.jbuilder:1`: `:tag_list`
- `app/views/articles/_article.json.jbuilder:3`: `current_user.favorited?(article)`
- `app/views/profiles/_profile.json.jbuilder:3`: `current_user.following?(user)`, rendered once per article for `author`
- `app/models/user.rb:36-38`: `favorited?` runs `favorites.find_by(article_id: ...)`

**What happens**
`includes(:user)` preloads only the authors. For each article the partial then makes these calls:

1. **`tag_list`.** acts-as-taggable-on 3.5 builds `tag_list` from `tags_on(:tags)`, which issues a fresh `SELECT tags.* ... JOIN taggings ... WHERE taggable_id = ? AND context = 'tags'` per article. There is no `cached_tag_list` column, so the gem's cache isn't used.
2. **`favorited?`.** One `SELECT favorites.* WHERE user_id = ? AND article_id = ? LIMIT 1` per article, for signed-in users.
3. **`following?`.** acts_as_follower runs `Follow.unblocked.for_follower(self).for_followable(user).count`, a `SELECT COUNT(*)` per article, for signed-in users.

A default page of 20 articles for a logged-in user costs: 1 (articles) + 1 (users preload) + 1 (count) + 20×3 = **about 63 queries**, plus one query to load `current_user`. Anonymous users still pay 20 tag queries. Because `limit` is client-controlled (see #2), `?limit=500` means about 1,500 queries in a single request. This is the dominant cost of the most-hit endpoint.

**Fix**
- **Tags.** Preload the tags association and read from it:
  ```ruby
  @articles = Article.includes(:user, :tags) # acts_as_taggable defines tag_taggings / tags
  ```
  In the partial, use `json.tag_list article.tags.map(&:name)` instead of `:tag_list`. Alternatively, add a `cached_tag_list` string column. acts-as-taggable-on 3.5 maintains it automatically when present, so `tag_list` becomes a column read with zero queries. Backfill it with `Article.find_each(&:save)` or a SQL update.
- **favorited?** Compute the set once per request in the controller:
  ```ruby
  @favorited_ids = signed_in? ? current_user.favorites.where(article_id: page_ids).pluck(:article_id).to_set : Set.new
  ```
  The partial then uses `@favorited_ids.include?(article.id)`.
- **following?** Compute the set once:
  ```ruby
  @followed_user_ids = signed_in? ? Follow.unblocked.where(follower_id: current_user.id, follower_type: 'User', followable_type: 'User', followable_id: author_ids).pluck(:followable_id).to_set : Set.new
  ```
  Change `_profile.json.jbuilder` to use the set when it's present and fall back to `following?` otherwise. The same partial is used for single profiles, where one query is fine.
- Materialize the page with `@articles.to_a` before building the ID lists so the main query runs only once.

**Result:** about 63 queries become about 6 queries per page, regardless of page size. Add a test or a Bullet / `ActiveSupport::Notifications` query-count assertion so it doesn't regress.

---

## 2. Unbounded `limit` and `offset` on list endpoints — High

**Where:** `articles_controller.rb:13` and `:21`: `.offset(params[:offset] || 0).limit(params[:limit] || 20)`

**What happens**
- A client can send `limit=100000`. Combined with #1, that's hundreds of thousands of queries and a very large JSON payload built in Ruby (Jbuilder partial rendering per row is itself slow, at roughly tens of microseconds per partial). One request can tie up a Puma thread for seconds or minutes, which makes it a cheap DoS vector.
- Large `offset` values force the DB to scan and discard every skipped row (`ORDER BY created_at DESC OFFSET n`). Cost grows linearly with page depth.
- The params arrive as strings and aren't validated. Rails quotes them, but negative or garbage values aren't rejected.

**Fix**
```ruby
limit  = params.fetch(:limit, 20).to_i.clamp(1, 100)   # Ruby < 2.4: [[x,1].max,100].min
offset = [params.fetch(:offset, 0).to_i, 0].max
```
Put this in a shared private helper used by `index`, `feed` and comments. If deep paging matters, add keyset pagination (`WHERE (created_at, id) < (?, ?)`) and keep offset only for the RealWorld spec's compatibility.

---

## 3. Comments index: N+1 on author and `following?`, no pagination — High

**Where:** `app/controllers/comments_controller.rb:6`, `app/views/comments/_comment.json.jbuilder:2`, `app/views/profiles/_profile.json.jbuilder:3`

**What happens**
`@article.comments.order(created_at: :desc)` has no `includes(:user)`. Each comment triggers:
- `SELECT users.* WHERE id = ?` to load the author
- for signed-in users, a `COUNT(*)` on `follows` (`following?`)

An article with 300 comments therefore costs about 600 queries and returns all 300 comments in one response. There's also no index covering `(article_id, created_at)`, so the sort happens after the `article_id` index lookup. That's acceptable at small sizes but grows with comment volume.

**Fix**
- `@article.comments.includes(:user).order(created_at: :desc)`
- Reuse the followed-author-ID set approach from #1 for `following?`.
- Add pagination with the same clamped limit/offset helper, or a hard cap.
- Optionally replace the `article_id` index with a composite `add_index :comments, [:article_id, :created_at]`.

---

## 4. `GET /api/tags` aggregates the whole taggings table on every request — High

**Where:** `app/controllers/tags_controller.rb:3`: `Article.tag_counts.most_used.map(&:name)`

**What happens**
`tag_counts` in acts-as-taggable-on 3.5 builds a query that joins `tags` to `taggings` (and a subquery over `articles`), then runs `GROUP BY tags.id` with `COUNT(taggings.id)`. `most_used` adds `ORDER BY count DESC LIMIT 20`. The DB must read **every tagging row** and sort the aggregate on each call. That makes it O(total taggings) for a widget shown on every page view, and the result changes rarely.

Meanwhile the schema already keeps a counter: `tags.taggings_count` (migration `..._add_taggings_counter_cache_to_tags`), which the gem maintains on tagging create and destroy.

**Fix**
- Read the counter directly:
  ```ruby
  ActsAsTaggableOn::Tag.where('taggings_count > 0').order(taggings_count: :desc).limit(20).pluck(:name)
  ```
  Add `add_index :tags, :taggings_count` so this is an index scan of 20 rows. (This counts taggings across all taggable types. Articles are the only taggable model here, so the result is the same.)
- Wrap the result in `Rails.cache.fetch('popular_tags', expires_in: 5.minutes)` and add `expires_in 5.minutes, public: true` or an ETag so clients and CDNs can cache it. Configure a real cache store in production (Redis or Memcached). The current default is a per-process file/memory store.

---

## 5. Missing indexes for the hot query shapes — High

**Where:** `db/schema.rb`

| Query | Current indexes | Problem | Add |
|---|---|---|---|
| `articles ORDER BY created_at DESC LIMIT 20` (global list) | none on `created_at` | Full scan and sort of `articles` on every home-page load | `add_index :articles, :created_at` |
| Feed / `authored_by`: `WHERE user_id IN (...) ORDER BY created_at DESC` | `user_id` only | Fetches all of the followed authors' articles, then sorts | `add_index :articles, [:user_id, :created_at]` (can replace `index_articles_on_user_id`) |
| `favorited?`, `favorited_by`, `unfavorite`, find-or-create: `WHERE user_id = ? AND article_id = ?` | separate single-column indexes | Index lookup on one column, then filter; no uniqueness guarantee | `add_index :favorites, [:user_id, :article_id], unique: true` (dedupe first) |
| `following?`: `WHERE follower_id/type = ? AND followable_id/type = ? AND blocked = f` | `fk_follows (follower_id, follower_type)` | Scans every follow of the follower to find one row; a heavy follower pays per call | `add_index :follows, [:follower_id, :follower_type, :followable_id, :followable_type], unique: true, name: 'idx_follows_unique_pair'` |
| Comments for an article, newest first | `article_id` | Sort after lookup | `[:article_id, :created_at]` (see #3) |
| `Tag` by popularity | none | Sort over all tags | `add_index :tags, :taggings_count` (see #4) |

Also make the foreign keys `NOT NULL` where they're always set (`articles.user_id`, `comments.user_id/article_id`, `favorites.*`). That helps the planner and data integrity, and set `articles.favorites_count` to `default: 0, null: false` so the view's `|| 0` isn't needed.

---

## 6. SQLite in production — High (once there is any concurrent write traffic)

**Where:** `config/database.yml:23-25` (`adapter: sqlite3`, `pool: 5`, `timeout: 5000`), `Gemfile:7`

**What happens**
- SQLite allows **one writer at a time** for the whole database file. Every favorite, follow, comment, article write, and Devise registration takes a database-wide lock. Under concurrent writes, other Puma threads block for up to `timeout: 5000` ms and then raise `SQLite3::BusyException`.
- The database is a local file, so the app can't run on more than one host or container, and there's no horizontal scaling.
- The pool of 5 must match Puma's thread count. There's no `config/puma.rb`, so Puma 3.4 defaults to 0–16 threads. Threads beyond 5 wait on `ActiveRecord::ConnectionTimeoutError` (the default checkout timeout is 5 s).
- In SQLite, `COUNT(*)`, the unindexed sorts and the per-row lookups above all run in-process and compete for the GVL with request handling.

**Fix**
- Move production to PostgreSQL (or MySQL). Set `pool: <%= ENV.fetch('RAILS_MAX_THREADS', 5) %>` and add a `config/puma.rb` with `threads` equal to the pool size and `workers` sized to CPU cores, with `preload_app!`.
- If SQLite has to stay (for demo use), enable WAL mode and keep Puma at a single worker with threads equal to the pool size.

---

## 7. Separate `COUNT(*)` on every list request — Medium

**Where:** `articles_controller.rb:11` and `:19`

**What happens**
Every list and feed call runs `SELECT COUNT(*)` over the full filtered set before fetching the page. For the unfiltered home page this counts the whole `articles` table on every request, which is a sequential scan in PostgreSQL. With `tagged_with` or `favorited_by` it's a full join count. Of the remaining queries after #1 is fixed, this one will be the most expensive.

**Fix**
- Cache the count for the unfiltered and tag-filtered cases, keyed by filter, with a short TTL (`Rails.cache.fetch(["articles_count", params[:tag], params[:author], params[:favorited]], expires_in: 1.minute)`).
- For the unfiltered case, keep a counter (a `Rails.cache` value, or use `pg_class.reltuples` estimates on Postgres if exact numbers aren't required).
- The RealWorld spec does require `articlesCount`, so caching is the practical option. Make sure the count query doesn't carry `includes` (Rails drops it for `count` unless `references` is used, which is fine today).

---

## 8. Tag filtering query plan — Medium

**Where:** `articles_controller.rb:7`: `@articles.tagged_with(params[:tag])`

**What happens**
In acts-as-taggable-on 3.5, `tagged_with` first resolves tag names with `Tag.named_any`, which generates `LOWER(tags.name) LIKE LOWER('foo')` (with `ESCAPE`). The `LOWER(...)` wrapper prevents use of `index_tags_on_name`, so every tag-filtered request seq-scans `tags`. It then adds one `JOIN taggings` alias per tag, filtered by `tag_id`, `taggable_type` and `context`. The `taggings_idx` index (leading `tag_id`) serves that join. Combined with the `ORDER BY created_at` (no index, #5) and the `COUNT` (#7), a popular tag scans all of its taggings twice per request.

**Fix**
- Store tag names in lowercase (the gem's `ActsAsTaggableOn.force_lowercase = true`) and resolve the tag with an exact `Tag.find_by(name: params[:tag].downcase)`. Then filter with `Article.joins(:taggings).where(taggings: { tag_id: tag.id, context: 'tags' })`.
- On PostgreSQL, alternatively add a functional index on `lower(name)`.
- Indexes from #5 cover the sort.

---

## 9. Cascading deletes load and delete rows one at a time; broken self-association — Medium

**Where**
- `app/models/article.rb:3-4`: `has_many :favorites, dependent: :destroy` and `has_many :comments, dependent: :destroy`
- `app/models/article.rb:15`: `has_many :articles, dependent: :destroy`
- `app/models/user.rb:7-9`: user → articles / favorites / comments, all `dependent: :destroy`

**What happens**
- `dependent: :destroy` instantiates every child and issues one `DELETE` per row, inside the parent's transaction. Deleting a popular article with 10k favorites and 2k comments means about 12k SELECT-materialized objects and 12k DELETE statements, plus a counter-cache `UPDATE articles SET favorites_count = ...` for each favorite. Deleting a user cascades through all their articles and each article's children. On SQLite this holds the database-wide write lock for the whole cascade (#6), so all other writers stall.
- `Article has_many :articles, dependent: :destroy` is a bug. `articles.article_id` doesn't exist, so `Article#destroy` will raise a SQL error when it tries to load `articles WHERE article_id = ?`. Remove the line.
- Taggings for the article are also destroyed one by one by the gem.

**Fix**
- Use `dependent: :delete_all` for `favorites` and `comments`. They have no callbacks of their own. The counter cache on `favorites` doesn't matter when the article is being deleted anyway.
- Add DB-level `ON DELETE CASCADE` foreign keys.
- For user deletion, move the work to a background job.
- Delete the `has_many :articles` line in `Article`.

---

## 10. Favorite and follow toggles: race conditions, redundant queries, missing unique constraints — Medium

**Where**
- `app/models/user.rb:26-34` (`favorite` / `unfavorite`)
- `app/controllers/favorites_controller.rb`
- `app/controllers/follows_controller.rb`

**What happens**
- `favorites.find_or_create_by(article:)` is SELECT then INSERT with no unique index. Double-clicks and concurrent requests create duplicate favorites, which inflates `favorites_count`, and every later `favorited?` / `destroy_all` handles the duplicates. acts_as_follower's `follow` has the same SELECT-then-INSERT race, and `follows` has no unique index either.
- `unfavorite` uses `where(...).destroy_all`, which loads the rows, then deletes each one and decrements the counter separately. It then calls `article.reload`, a full re-SELECT of the article. After `favorite`, the article is **not** reloaded, so the response shows a stale `favorites_count` (a correctness bug).
- Each toggle then renders `articles/show`, which re-runs the per-article queries from #1 (tags, favorited?, following?, author). That's about 6–8 queries for a one-row write.
- `follows_controller` also renders `profiles/show`, which runs `following?` again right after the write. The answer is already known.

**Fix**
- Add unique indexes (see #5). Use `create` and rescue `ActiveRecord::RecordNotUnique`, or use an upsert on Postgres.
- In `unfavorite`, use `where(...).delete_all` with an explicit `Article.decrement_counter(:favorites_count, article.id)`, or keep `destroy_all` but drop the full reload in favor of `article.reload(select: :favorites_count)`. Reload (or increment in memory) after `favorite` as well.
- Pass the known `favorited` and `following` state to the view instead of re-querying it.

---

## 11. Production logs at `:debug` — Medium

**Where:** `config/environments/production.rb:49`: `config.log_level = :debug`

**What happens**
At debug level every SQL statement is formatted and written to the log, along with every partial render line (Jbuilder logs each partial) and parameter dumps. With the N+1 patterns above, that's 60+ SQL log lines and 40+ render lines per list request. String formatting and synchronous file I/O add measurable latency per request, logs grow quickly, and it can expose data.

**Fix:** set `config.log_level = :info` (or `ENV.fetch('LOG_LEVEL', 'info')`). Consider lograge for single-line request logs.

---

## 12. No HTTP or application caching on public read endpoints — Low–Medium

**Where:** `articles#index`, `articles#show`, `tags#index`, `profiles#show`, `comments#index`

**What happens**
No responses use `fresh_when` / `stale?`, `expires_in`, or fragment caching. Every anonymous home-page load re-runs the full article list, count and tag aggregation and re-renders the JSON. `perform_caching` is on in production, but no cache store is configured, so it defaults to a per-process file store.

**Fix**
- For anonymous requests (`!signed_in?`), add `expires_in 30.seconds, public: true` and/or `fresh_when(etag: [@articles.maximum(:updated_at), @articles_count, params.slice(...)])` on list and show.
- Cache the anonymous article JSON partial with `json.cache! ['v1', article] do ... end`. Keep `favorited` and `following` outside the cached block, because they're per-user. Use `touch: true` on `Favorite` → `Article` if `favorites_count` is in the cached part.
- Configure `config.cache_store = :redis_cache_store` (via the redis-rails gem on Rails 4.2) or `:mem_cache_store`.

---

## 13. Minor per-request overhead — Low

- **`require 'rails/all'`** (`config/application.rb:3`) loads ActionMailer, Sprockets / the asset pipeline (sass-rails, coffee-rails, uglifier, jquery-rails, turbolinks) and ActionView HTML features in a JSON-only API. This adds memory per Puma worker and slower boot, but has almost no per-request cost. Require only the needed railties (`active_record/railtie`, `action_controller/railtie`) and drop the asset gems. Rails 5+ `api_only` mode is the long-term option.
- **`underscore_params!`** (`application_controller.rb:44-46`) deep-transforms every params key on every request, including large article bodies. The cost is small but nonzero. It's acceptable, but limiting it to request bodies with camelCase keys, or moving it to a Rack middleware that runs once, would be cleaner.
- **`current_user`** (`application_controller.rb:36-38`) calls Devise's `super` (Warden authenticate against the cookie session) before falling back to `User.find`. For JWT requests the Warden lookup is wasted work. Use `@current_user ||= User.find(@current_user_id) if @current_user_id` for token-authenticated requests. Endpoints that only need the user ID (update/destroy ownership checks) already avoid loading the user, which is good. Keep that pattern.
- **`users/_user.json.jbuilder:2`** generates a new JWT on every `GET /api/user` and `PUT /api/user`. HMAC signing is cheap, so this is not a real concern. It's listed only for completeness.
- **bcrypt `stretches = 11`** (`config/initializers/devise.rb:108`) costs about 100–250 ms of CPU per login or registration and holds the GVL. That's the right security trade-off and should not be lowered. It does mean login bursts need enough Puma workers (processes), not just threads. Rate-limit `POST /api/users/login` (e.g. rack-attack) so login floods can't monopolize CPU.
- **`ProfilesController#show`** uses `find_by_username` (no bang). A missing user renders the partial with `nil` and raises a 500 instead of a cheap 404. Use `find_by_username!`.

---

## Suggested order of work

1. **Quick wins (about a day):** #2 limit clamp, #11 log level, #5 indexes (with dedupe migrations for favorites and follows), #4 tag cloud via `taggings_count`, #3 `includes(:user)` on comments, and removing the broken `has_many :articles` line (#9).
2. **Main fix:** #1, eliminating the per-article queries (preload tags or add `cached_tag_list`, plus favorited/following ID sets). Add query-count tests for `index`, `feed` and `comments#index`.
3. **Infrastructure:** #6 move to PostgreSQL with Puma threads, workers and pool sized together; configure a shared cache store.
4. **Follow-ups:** #7 count caching, #8 tag lookup, #9 `delete_all` cascades, #10 toggle races and reloads, #12 HTTP and fragment caching.

**Verification:** seed about 10k users, 100k articles, 1M favorites and taggings. Then measure p50/p95 latency and queries per request for `GET /api/articles`, `/api/articles/feed` (as a user following 500 authors), `/api/tags` and `/api/articles/:slug/comments`, before and after each step. Use `rack-mini-profiler` or `ActiveSupport::Notifications` SQL counters, and `EXPLAIN` (or `EXPLAIN QUERY PLAN` on SQLite) for the index changes.
