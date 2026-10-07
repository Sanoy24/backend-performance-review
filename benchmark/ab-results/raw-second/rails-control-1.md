# Performance Review: rails-realworld (Conduit API, Rails 4.2.6)

Scope: everything under `app/`, `config/`, and `db/` in the repository. The app is a JSON API (the RealWorld "Conduit" backend) built on Devise and JWT, acts-as-taggable-on 3.5.0, acts_as_follower 0.2.1, Jbuilder, Puma, and SQLite.

The review is based on reading the code. Nothing was profiled. Query counts are worked out from the code and the gem behavior at the pinned versions.

## Summary (by priority)

| # | Finding | Where | Severity |
|---|---------|-------|----------|
| 1 | N+1 queries when rendering article lists (favorited?, following?, tag_list) | `views/articles/_article.json.jbuilder`, `views/profiles/_profile.json.jbuilder`, `ArticlesController#index/#feed` | **Critical** |
| 2 | Client controls page size with no cap | `ArticlesController#index/#feed` | **High** |
| 3 | Comments list has N+1 queries and no pagination | `CommentsController#index`, `views/comments/_comment.json.jbuilder` | **High** |
| 4 | Missing indexes for list ordering, favorites, and follows lookups | `db/schema.rb` | **High** |
| 5 | SQLite as the production database | `config/database.yml` | **High** (at any real concurrency) |
| 6 | `/api/tags` aggregates the whole taggings table on every request, with no limit and no cache | `TagsController#index` | **Medium** |
| 7 | COUNT plus OFFSET pagination on every list request | `ArticlesController#index/#feed` | **Medium** |
| 8 | Production log level is `:debug` | `config/environments/production.rb` | **Medium** |
| 9 | Cascading `dependent: :destroy` loads and deletes rows one by one (and an invalid association breaks article delete) | `models/user.rb`, `models/article.rb` | **Low–Medium** |
| 10 | Smaller per-request overheads (full Rails stack, Devise/Warden lookups, favorite or unfavorite reload, tag writes) | various | **Low** |

---

## 1. N+1 queries when rendering article lists — Critical

**Where**
- `app/controllers/articles_controller.rb:5` (`index`) and `:17` (`feed`) load `Article.includes(:user)` and nothing else.
- `app/views/articles/_article.json.jbuilder` is rendered once for each article:
  - line 1: `:tag_list` makes acts-as-taggable-on load the article's tags (`SELECT tags.* FROM tags INNER JOIN taggings ... WHERE taggable_id = ?`). That is **1 query per article**.
  - line 3: `current_user.favorited?(article)` leads to `favorites.find_by(article_id: article.id)` in `app/models/user.rb:36-38`. That is **1 query per article** when signed in.
- `app/views/profiles/_profile.json.jbuilder:3`: `current_user.following?(user)` (acts_as_follower runs a `COUNT` on `follows`). That is **1 query per article** when signed in.

**Impact.** With the default page size of 20 and a signed-in user, one `GET /api/articles` or `GET /api/articles/feed` runs about 2 + 20×3 = **about 62 queries**. With `?limit=100` it is about 300 (see #2). These are the most-hit endpoints in the app (home page, feed, profile tabs, tag filter), so this is the main load on the database. The same pattern runs for single-article responses (`show`, `create`, `update`, favorite and unfavorite). It costs only a few extra queries there, but they run on every write.

**Fix**
1. Preload tags. Use `Article.includes(:user, :tags)` and render the names from the preloaded association instead of `tag_list`, which goes through the tagging context cache and can re-query. In the partial, use `json.tag_list article.tags.map(&:name)`. Check with a query log that only one query runs.
2. Batch the per-user flags in the controller after pagination:
   ```ruby
   ids = @articles.map(&:id)
   @favorited_ids = signed_in? ? current_user.favorites.where(article_id: ids).pluck(:article_id).to_set : Set.new
   author_ids = @articles.map(&:user_id).uniq
   @followed_ids = signed_in? ? Follow.unblocked.where(follower_id: current_user.id, follower_type: 'User',
                                  followable_type: 'User', followable_id: author_ids).pluck(:followable_id).to_set : Set.new
   ```
   In the partials, use `@favorited_ids.include?(article.id)` and `@followed_ids.include?(user.id)`. The profile partial needs a fallback (`@followed_ids ? ... : current_user.following?(user)`) because `profiles/show` and `follows` also use it.
3. Add a request-level query-count check, for example Bullet in development or a test that asserts the query count on `/api/articles`, so this cannot come back.

Expected result: about 5 queries per list request, whatever the page size.

## 2. Client controls page size with no cap — High

**Where:** `articles_controller.rb:13` and `:21` use `.limit(params[:limit] || 20)` and `.offset(params[:offset] || 0)`.

**Impact.** Any client, including an anonymous one on `index`, can send `?limit=1000000`. That loads every article with its full `body` into Ruby, and with #1 still in place it also runs 3 queries per row. One request can tie up a Puma thread and a DB connection for seconds and use a lot of memory. This is both a performance risk and a cheap denial-of-service vector. Very large `offset` values also force the DB to scan and discard rows (see #7).

**Fix.** Clamp both values: `limit = params[:limit].to_i.clamp(1, 100)` (Rails 4.2 / older Ruby: `[[params[:limit].to_i, 1].max, 100].min`, defaulting to 20), and `offset = [params[:offset].to_i, 0].max`. Optionally cap offset too, or move to keyset pagination (#7).

## 3. Comments list has N+1 queries and no pagination — High

**Where:** `app/controllers/comments_controller.rb:6`: `@article.comments.order(created_at: :desc)`. There is no `includes` and no limit. `app/views/comments/_comment.json.jbuilder:2` loads `comment.user` for each comment, and the profile partial runs `following?` for each comment when signed in.

**Impact.** Each comment costs 1 user query, plus 1 follows query when signed in. All comments are returned in one response, so a popular article with 500 comments means about 1,000 queries and a large response. This runs on every article page view.

**Fix.** Use `@article.comments.includes(:user).order(created_at: :desc)`, and batch-load the followed-author set as in #1. Add `limit`/`offset` with a cap (or a cursor on `created_at, id`). Add a composite index `comments(article_id, created_at)` so the ordered read is served from the index (see #4).

## 4. Missing indexes — High

From `db/schema.rb`:

| Query | Current index | Problem | Proposed index |
|---|---|---|---|
| `Article ... ORDER BY created_at DESC LIMIT/OFFSET` (index, all filters) | none on `created_at` | Full scan and sort of `articles` on every home page load | `articles(created_at)` |
| `feed`: `WHERE user_id IN (followed) ORDER BY created_at DESC` and `authored_by` | `articles(user_id)` | Fetches all of each author's articles, then sorts | `articles(user_id, created_at)` (replaces `user_id`) |
| `favorited?`, `find_or_create_by(article:)`, `favorited_by` | separate `favorites(user_id)`, `favorites(article_id)` | Point lookups scan all of a user's favorites. No uniqueness, so `find_or_create_by` can create duplicates under concurrency and inflate `favorites_count` | **unique** `favorites(user_id, article_id)` |
| `following?` / follow / unfollow | `follows(follower_id, follower_type)` and `follows(followable_id, followable_type)` | Point lookup filters a follower's whole follow list, and nothing stops duplicate follows | **unique** `follows(follower_id, follower_type, followable_id, followable_type)` |
| Comments for an article, ordered | `comments(article_id)` | Sort after fetch | `comments(article_id, created_at)` |

Before adding the unique indexes, de-duplicate existing rows. Then handle `ActiveRecord::RecordNotUnique` in `User#favorite` (rescue and continue) so a double-click cannot cause a 500.

## 5. SQLite as the production database — High (scales with concurrency)

**Where:** `config/database.yml` sets `production: adapter: sqlite3, pool: 5, timeout: 5000`. `Gemfile` pins `sqlite3` with no production alternative.

**Impact.** SQLite allows only one writer at a time and locks the whole database file. Under Puma's threads (and any multi-process or multi-host setup, where it cannot be shared at all), every write blocks other writers: sign-in tracking, favorites, comments, follows, and article creation. Requests then wait up to the 5 s busy timeout and fail with `SQLite3::BusyException`. Read throughput is also limited to one host. There is no Puma config, so the thread count is whatever Puma defaults to. Keep `pool` at or above the thread count.

**Fix.** Use PostgreSQL (or MySQL) in production. Size `pool` to Puma's `max_threads` (`pool: <%= ENV.fetch("RAILS_MAX_THREADS", 5) %>`), and add a `config/puma.rb` that sets workers and threads explicitly. If SQLite must stay for a small deployment, use a single process and enable WAL mode. Treat that as a stopgap.

## 6. `/api/tags` aggregates the whole taggings table on every request — Medium

**Where:** `app/controllers/tags_controller.rb:3` calls `Article.tag_counts.most_used.map(&:name)`.

**Impact.** `tag_counts` runs a `GROUP BY tags.id` / `COUNT(*)` join over **all** `taggings` rows. `most_used` with no argument sets no meaningful limit, so every tag ever created is sorted and returned. The cost grows with total taggings, and the RealWorld frontend calls this endpoint on every home page load. The data changes rarely.

**Fix.**
- Use the existing counter-cache column: `ActsAsTaggableOn::Tag.most_used(20).pluck(:name)`. It reads `tags.taggings_count` (already in the schema) instead of aggregating taggings. Add an index on `tags(taggings_count)` if the table is large.
- Cache the result: `Rails.cache.fetch('popular_tags', expires_in: 5.minutes) { ... }`. A short TTL is acceptable. Configure a real cache store (`:mem_cache_store` / `:redis_cache_store`) in production, because none is set.
- Add HTTP caching (`expires_in 5.minutes, public: true`) because the response does not depend on the user.

## 7. COUNT plus OFFSET pagination on every list request — Medium

**Where:** `articles_controller.rb:11` and `:19` run `@articles.count` on every list call, then `.offset(...)`.

**Impact.** Every list request runs a second query that counts the whole filtered set. For `tagged_with` and `favorited_by` that count goes through joins. In acts-as-taggable-on 3.5, `tagged_with` matches tag names with `LIKE` / `LOWER(...)` on non-Postgres adapters, which can bypass the `tags.name` index. Deep `OFFSET` pages cost O(offset). This is not significant at small scale, but it grows linearly with data size.

**Fix.** Keep the count (the API contract requires `articlesCount`), but make it cheap. Make sure the filters hit indexes (#4). For the global unfiltered count, consider caching it or a counter. For deep pagination, offer cursor pagination (`WHERE (created_at, id) < (?, ?)`) or cap `offset`. For tag filtering, use an exact-match lookup: find the tag id first (`ActsAsTaggableOn::Tag.find_by(name:)`), then filter with a join on `taggings.tag_id`.

## 8. Production log level is `:debug` — Medium

**Where:** `config/environments/production.rb:49` sets `config.log_level = :debug`.

**Impact.** Every SQL statement, including the dozens per request from #1 and #3, is formatted and written synchronously to the log. This adds measurable latency and I/O per request and creates very large log files. It also logs SQL with bound values (emails, for example).

**Fix.** Use `config.log_level = :info` (or set it from `ENV["RAILS_LOG_LEVEL"]`).

## 9. Cascading destroys run one row at a time — Low–Medium

**Where**
- `app/models/user.rb:7-9`: `has_many :articles/:favorites/:comments, dependent: :destroy`.
- `app/models/article.rb:3-4`: `has_many :favorites/:comments, dependent: :destroy`.
- `app/models/article.rb:15`: `has_many :articles, dependent: :destroy` on **Article** itself.

**Impact**
- Deleting an article loads and destroys each favorite and comment separately. Each favorite destroy also fires a `favorites_count` decrement `UPDATE` on the article that is being deleted. Deleting a user cascades through all of that user's articles this way, so it can run tens of thousands of statements inside one request and transaction.
- `has_many :articles` on `Article` looks for an `articles.article_id` column that does not exist. `Article#destroy` will raise an SQL error, so `DELETE /api/articles/:slug` fails. This is a correctness bug, but it is listed here because it is on the destroy path.

**Fix.** Remove the bogus `has_many :articles` from `Article`. For favorites and comments, use `dependent: :delete_all` (they have no callbacks that matter, and the counter cache does not matter for a row being deleted), or add DB-level `ON DELETE CASCADE` foreign keys. Move user deletion to a background job if it is ever exposed.

## 10. Smaller per-request overheads — Low

- **`favorite` / `unfavorite` response staleness and reload** (`user.rb:26-34`, `favorites_controller.rb`): `unfavorite` does an extra `article.reload`. `favorite` does not reload, so the response returns a stale `favorites_count`. That is a correctness bug, fixed by `@article.reload` or by incrementing in memory. To do both cheaply, update in memory instead of reloading.
- **`current_user`** (`application_controller.rb:36-38`): `super` runs Devise/Warden's session and remember-me strategies on every call to `current_user`, before it falls back to `User.find`. This is memoized per request, so it happens once. For a token-only API, look the user up directly from `@current_user_id` and skip Warden.
- **Full Rails stack for a JSON API** (`config/application.rb` uses `require 'rails/all'`): it loads Sprockets, ActionMailer, cookie/session/flash middleware, jQuery/turbolinks/sass gems, and `protect_from_forgery`. Trimming to the needed frameworks and middleware lowers memory per worker and per-request middleware cost.
- **Tag writes on article create and update**: acts-as-taggable-on runs find-or-create per tag and inserts each tagging separately. This is acceptable at typical tag counts (under 10). Watch it if tag lists get large.
- **Slug generation** (`article.rb:17-19`): the uniqueness validation runs one indexed query per save. That is fine. A collision just fails validation instead of retrying, which is a correctness issue rather than a performance one.
- **`underscore_params!`** deep-transforms params on every request. The cost is negligible for normal payloads.

---

## Recommended order of work

1. Fix the N+1 queries in article lists (#1) and comments (#3), and clamp `limit` (#2). This is the biggest win for the least effort: about 60 queries per page drops to about 5.
2. Add the indexes, including the unique composites on favorites and follows (#4).
3. Set the production log level to `:info` (#8). This is a one-line change.
4. Switch to cache plus counter-cache for `/api/tags` (#6).
5. Plan the move from SQLite to PostgreSQL before any real production traffic (#5).
6. Clean up destroy cascades and fix the bogus association (#9). Then do pagination improvements (#7) and the minor items (#10) as needed.
