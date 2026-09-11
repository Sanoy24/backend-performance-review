# Performance Review — Conduit (RealWorld) Rails API

**Repo:** `rails-realworld` · Rails 4.2.6 / Ruby-era 2.x · SQLite · Puma · Jbuilder
**Scope:** static review of `app/`, `config/`, `db/`. No profiler or production traces were available, so query counts below are derived by reading the call graph (controller → Jbuilder template → model → gem) rather than measured. Every count is reproducible with `ActiveSupport::Notifications` on `sql.active_record` or the Bullet gem.

## Executive summary

The app is small, but the two hottest endpoints — `GET /api/articles` and `GET /api/articles/feed` — issue roughly **60–65 SQL queries per request for a 20-item page**, when 4 would do. That is the single dominant cost and it scales linearly with page size, so it gets worse exactly when a client asks for more.

Underneath that sits an infrastructure problem that caps the app regardless of query tuning: **SQLite is configured as the production database**, and the ActiveRecord connection pool (5) is smaller than Puma's default thread count (16).

A third class of issue is **unbounded input**: `limit` and `offset` are taken straight from query params with no ceiling, and the comments endpoint has no pagination at all. Either one lets a single unauthenticated request pull an entire table through Jbuilder.

Ranked list, worst first:

| # | Finding | Severity | Where |
|---|---|---|---|
| 1 | N+1 explosion in article serialization (3 queries per article) | Critical | `app/views/articles/_article.json.jbuilder`, `app/views/profiles/_profile.json.jbuilder` |
| 2 | SQLite in production + pool/thread mismatch | Critical | `config/database.yml`, missing `config/puma.rb` |
| 3 | Unbounded `limit`/`offset`; unpaginated comments | Critical | `articles_controller.rb:13,21`, `comments_controller.rb:6` |
| 4 | No index on `articles.created_at` — filesort on every list | High | `db/schema.rb:16-28` |
| 5 | `@articles_count` runs a second full scan per request | High | `articles_controller.rb:11,19` |
| 6 | `TagsController#index` — uncached aggregate on every call | High | `tags_controller.rb:3` |
| 7 | `tagged_with` can't use the tag-name index | Medium-High | `articles_controller.rb:7` |
| 8 | `Article has_many :articles` — broken, expensive destroy cascade | Medium-High | `app/models/article.rb:15` |
| 9 | No cache store, no HTTP caching, no Jbuilder fragment caching | Medium | `config/environments/production.rb` |
| 10 | `log_level = :debug` in production | Medium | `config/environments/production.rb:49` |
| 11 | Missing unique index on `favorites(user_id, article_id)` | Medium | `db/schema.rb:41-49` |
| 12 | Per-request overhead: sessions, `deep_transform_keys!`, `User.find` | Low-Medium | `application_controller.rb` |
| 13 | Unstable sort order breaks offset pagination | Low-Medium | `articles_controller.rb:13` |
| 14 | `favorites_count` nullable with no default | Low | `db/schema.rb:21` |
| 15 | No instrumentation — none of this is observable today | Low (but blocking) | repo-wide |

---

## 1. N+1 explosion in article serialization — **Critical**

**Where:** `app/views/articles/_article.json.jbuilder` and `app/views/profiles/_profile.json.jbuilder`, driven by `ArticlesController#index` and `#feed`.

The controller does the right thing at the top:

```ruby
@articles = Article.all.includes(:user)   # articles_controller.rb:5
```

…and then the view undoes it three separate ways. For **each** article in the page:

```ruby
# _article.json.jbuilder
json.(article, :title, :slug, :body, :created_at, :updated_at, :description, :tag_list)
json.favorited signed_in? ? current_user.favorited?(article) : false

# _profile.json.jbuilder (rendered once per article, as the author)
json.following signed_in? ? current_user.following?(user) : false
```

Each of the three does a round trip:

- **`:tag_list`** → acts-as-taggable-on's `tag_list` calls `tags_on(:tags)`, which builds `base_tags.where(taggings.context = 'tags')` — a fresh query per article. `includes(:user)` does not preload it, and because AATO 3.5 rebuilds the scope from `base_tags` rather than reading a preloaded association, adding `includes(:tags)` will **not** reliably fix it either.
- **`current_user.favorited?(article)`** → `app/models/user.rb:37` is `favorites.find_by(article_id: article.id).present?`. The `favorites` association is never loaded, so this is one `SELECT * FROM favorites … LIMIT 1` per article. It also materializes a full AR object just to throw it away.
- **`current_user.following?(user)`** → acts_as_follower 0.2.1 implements this as `0 < Follow.unblocked.for_follower(self).for_followable(followable).count` — one `SELECT COUNT(*) FROM follows` per article.

**Cost.** For an authenticated `GET /api/articles?limit=20`:

```
 1  count query (@articles_count)
 1  articles page query
 1  users preload
60  3 x 20 in the view
---
63  queries, ~59 of them avoidable
```

Anonymous requests short-circuit `favorited`/`following` (the `signed_in?` ternary) and land at ~23 queries — still 20 more than necessary. So the **logged-in** experience, the one that matters, is the slow one. On SQLite each query is cheap in isolation, which is exactly why this hides in development and bites in production, where every query carries network latency.

**Fix.** Resolve all three as set memberships computed once per request, then hand the sets to the view.

Add a `cached_tag_list` column so AATO stops querying entirely (it auto-enables its caching module when a `cached_<context>_list` column exists):

```ruby
class AddCachedTagListToArticles < ActiveRecord::Migration
  def change
    add_column :articles, :cached_tag_list, :string
  end
end
# then backfill: Article.find_each(&:save)
```

Then batch the two user-scoped booleans in the controller:

```ruby
def index
  @articles = filtered_scope.order(created_at: :desc, id: :desc)
                            .offset(page_offset).limit(page_limit)
  load_viewer_context(@articles)
end

private

def load_viewer_context(articles)
  @favorited_ids = Set.new
  @following_ids = Set.new
  return unless signed_in?

  @favorited_ids = Favorite.where(user_id: current_user.id, article_id: articles.map(&:id))
                           .pluck(:article_id).to_set
  @following_ids = Follow.where(follower_id: current_user.id, follower_type: 'User',
                                followable_type: 'User', followable_id: articles.map(&:user_id),
                                blocked: false)
                         .pluck(:followable_id).to_set
end
helper_method :favorited_ids, :following_ids
```

and in the templates:

```ruby
json.favorited @favorited_ids.include?(article.id)
json.following @following_ids.include?(user.id)
```

That takes the page from ~63 queries to **5**, independent of page size. The same `load_viewer_context` serves `#feed`. Guard the regression with a `assert_queries` test or Bullet in the test environment — this is the kind of thing that grows back the next time someone adds a field to the serializer.

While you are in `user.rb`, `favorited?` should be `favorites.where(article_id: article.id).exists?` for the single-article path (`articles#show`, `favorites#create`) — same answer, no object allocation, and it stops at the index.

---

## 2. SQLite in production, and pool < threads — **Critical**

**Where:** `config/database.yml:23-25`; `config/puma.rb` does not exist.

```yaml
production:
  <<: *default
  adapter: sqlite3
  pool: 5
  database: db/production.sqlite3
```

Two compounding problems:

**SQLite serializes writes.** The whole database takes one writer lock. Every `POST /api/articles`, every favorite, every comment, every Devise `:trackable` sign-in update blocks all other writers. With `timeout: 5000`, contending writes wait up to 5s and then raise `SQLite3::BusyException` — a 500 to the client, not a retry. Concurrent write throughput is effectively one transaction at a time regardless of how many app threads you run. It also rules out running more than one app process against the same data, since the file lives on local disk.

**The pool is smaller than the thread count.** Puma 3.4 with no `config/puma.rb` defaults to a 0–16 thread pool. Sixteen threads sharing five connections means threads block in `ConnectionPool#checkout` and, past the 5s default, raise `ActiveRecord::ConnectionTimeoutError`. This is latent even after you move off SQLite.

**Fix.**

1. Move to PostgreSQL. The schema is portable as-is; `acts-as-taggable-on` and `devise` both support it. This is a prerequisite for finding 4 (partial/functional indexes) and for horizontal scaling.
2. Add `config/puma.rb` and tie the pool to the thread count:

   ```ruby
   threads_count = Integer(ENV.fetch('RAILS_MAX_THREADS', 5))
   threads threads_count, threads_count
   workers Integer(ENV.fetch('WEB_CONCURRENCY', 2))
   preload_app!
   on_worker_boot { ActiveRecord::Base.establish_connection }
   ```

   ```yaml
   production:
     adapter: postgresql
     pool: <%= ENV.fetch('RAILS_MAX_THREADS', 5) %>
     url: <%= ENV['DATABASE_URL'] %>
   ```

3. Once on Postgres, add real foreign keys. The migrations pass `foreign_key: true`, but `db/schema.rb` shows no FK constraints survived under SQLite — so referential integrity is currently unenforced, and orphaned `favorites`/`comments` rows will silently distort counter caches.

---

## 3. Unbounded `limit`/`offset`, and comments with no pagination at all — **Critical**

**Where:** `articles_controller.rb:13` and `:21`, `comments_controller.rb:6`.

```ruby
@articles = @articles.order(created_at: :desc).offset(params[:offset] || 0).limit(params[:limit] || 20)
```

`params[:limit]` goes to the database unfiltered. `GET /api/articles?limit=1000000` loads a million rows — each with a `text` body column — instantiates a million AR objects, then renders each through a Jbuilder partial (finding 9). One request exhausts the heap. This is a denial-of-service vector reachable without authentication.

`params[:offset]` is equally unbounded and separately slow: `OFFSET 500000` makes the database walk and discard half a million rows before returning 20.

The comments endpoint has no limit of any kind:

```ruby
def index
  @comments = @article.comments.order(created_at: :desc)   # every comment, forever
end
```

…and no `includes(:user)`, so `_comment.json.jbuilder`'s `comment.user` plus the profile partial's `following?` add **2 queries per comment**. A popular article with 5,000 comments is 10,000 queries and 5,000 serialized objects in one response.

**Fix.** Clamp both params in one place and paginate comments:

```ruby
MAX_LIMIT = 100

def page_limit
  [[params[:limit].to_i, 1].max, MAX_LIMIT].min
rescue
  20
end

def page_offset
  [params[:offset].to_i, 0].max
end
```

Note `.to_i` also removes a second bug: Rails 4.2's `sanitize_limit` calls `Integer(limit)`, so `?limit=abc` currently raises `ArgumentError` and returns a 500.

For comments, add `includes(:user)`, apply the same limit/offset, and add the composite index from finding 4. Longer term, switch both endpoints to **keyset pagination** (`WHERE created_at < ? ORDER BY created_at DESC LIMIT 20`), which is O(limit) at any depth and sidesteps finding 13 as well.

---

## 4. No index on `articles.created_at` — **High**

**Where:** `db/schema.rb:16-28`. Indexes exist on `slug` (unique) and `user_id`, and nothing else.

Every list request ends in `ORDER BY created_at DESC LIMIT 20`. With no index on `created_at`, the planner does a full table scan plus a sort of the entire result set to return 20 rows — on *every* request, including the unfiltered homepage, which is the most-hit endpoint in the app.

The filtered variants need composites:

- `authored_by` → `WHERE user_id IN (…) ORDER BY created_at DESC` wants `(user_id, created_at)`.
- `feed` → same shape, same index.
- `favorited_by` → `joins(:favorites).where(favorites: {user_id: …})`; `index_favorites_on_user_id` exists but the sort still falls back to the articles table.
- `comments#index` → `WHERE article_id = ? ORDER BY created_at DESC` wants `(article_id, created_at)`.

**Fix:**

```ruby
class AddSortIndexes < ActiveRecord::Migration
  def change
    add_index :articles, :created_at
    add_index :articles, [:user_id, :created_at]
    add_index :comments,  [:article_id, :created_at]
    add_index :favorites, [:user_id, :article_id], unique: true   # see finding 11
  end
end
```

These are cheap to add and turn the sort into an index scan. Verify with `EXPLAIN QUERY PLAN` (SQLite) / `EXPLAIN ANALYZE` (Postgres) that the sort node disappears.

---

## 5. `@articles_count` doubles the cost of every list request — **High**

**Where:** `articles_controller.rb:11` and `:19`.

```ruby
@articles_count = @articles.count
@articles = @articles.order(created_at: :desc).offset(…).limit(…)
```

`count` executes the full filtered query as an aggregate — a second complete scan of the same rows the page query is about to scan again. On an unfiltered `Article.all` this is `SELECT COUNT(*) FROM articles`, which is a sequential scan in Postgres (MVCC means no O(1) row count). As the table grows, this becomes the slowest statement on the endpoint, and it exists only to populate `articlesCount` in the payload.

There is also a latent correctness problem: `favorited_by` uses `joins(:favorites)`, so the count is over joined rows. Today that's safe because a user favorites an article at most once — but only by convention, since there's no unique index (finding 11). Add one duplicate row and the count silently inflates.

**Fix, in escalating order:**

1. Short term — make the count explicit and de-duplicated: `@articles_count = @articles.distinct.count(:id)`.
2. Better — cache it. The unfiltered total changes rarely; `Rails.cache.fetch('articles/total', expires_in: 1.minute) { Article.count }` removes it from the hot path entirely (requires finding 9).
3. Best — drop it. Move to keyset pagination and return a `hasMore` boolean derived from fetching `limit + 1` rows. The client gets what it actually needs for an infinite-scroll UI at zero extra cost.

---

## 6. `TagsController#index` is an uncached aggregate — **High**

**Where:** `tags_controller.rb:3`.

```ruby
render json: { tags: Article.tag_counts.most_used.map(&:name) }
```

`tag_counts` joins `tags` to `taggings`, filters by taggable type and context, and does a `GROUP BY` across the whole tagging table; `most_used` then orders by count and takes 20. This runs on every page load of the client's home screen, and the result is the *same for every user* and changes slowly.

**Fix:** cache it.

```ruby
def index
  tags = Rails.cache.fetch('tags/most_used/v1', expires_in: 10.minutes) do
    Article.tag_counts.most_used.map(&:name)
  end
  render json: { tags: tags }
end
```

Requires a real cache store (finding 9). `most_used` already orders on the `tags.taggings_count` counter-cache column, so also confirm that counter is actually being maintained — AATO 3.5 only updates it when the `taggings_count` column is present, which it is (`db/schema.rb:79`).

---

## 7. `tagged_with` can't use the tag-name index — **Medium-High**

**Where:** `articles_controller.rb:7`, `@articles.tagged_with(params[:tag])`.

`acts-as-taggable-on` 3.5 defaults to `strict_case_match = false`, which makes it compare `LOWER(tags.name) = LOWER(?)`. A function on the indexed column makes `index_tags_on_name` unusable, so tag lookup degrades to a scan of `tags` and then a join into `taggings`. Tag-filtered browsing is a primary navigation path in this app.

**Fix:** pick one.

- Normalize on write and match strictly, in an initializer:
  ```ruby
  ActsAsTaggableOn.force_lowercase   = true
  ActsAsTaggableOn.strict_case_match = true
  ```
  (requires a one-time backfill to downcase existing `tags.name` and dedupe collisions).
- Or, on Postgres, add a functional index: `CREATE INDEX index_tags_on_lower_name ON tags (LOWER(name));`

Also note `tagged_with` generates a correlated `id IN (SELECT taggable_id FROM taggings …)`. Combined with the missing `created_at` index (finding 4), the tag-filtered list is the slowest read path in the app.

---

## 8. `Article has_many :articles, dependent: :destroy` — **Medium-High**

**Where:** `app/models/article.rb:15`.

```ruby
has_many :articles, dependent: :destroy
```

This is a self-referential association to a `article_id` column that does not exist on `articles`. `DELETE /api/articles/:slug` will hit the `dependent: :destroy` callback, emit `SELECT * FROM articles WHERE articles.article_id = ?`, and raise `ActiveRecord::StatementInvalid`. **The delete endpoint is broken**, not merely slow. Almost certainly a copy-paste of the `User` associations.

Separately, the legitimate cascades are inefficient. `dependent: :destroy` on `favorites` instantiates every favorite row and runs callbacks one at a time — for a popular article, that's hundreds of `DELETE` statements inside one transaction, each also decrementing the counter cache on a row that is about to be deleted.

**Fix:**

```ruby
class Article < ActiveRecord::Base
  belongs_to :user
  has_many :favorites, dependent: :delete_all   # no callbacks needed; parent is going away
  has_many :comments,  dependent: :delete_all
  # remove `has_many :articles` entirely
end
```

`delete_all` is safe here because neither child has destroy-time behavior that outlives the parent. Once you are on Postgres with real FKs (finding 2), `ON DELETE CASCADE` moves the work into the database and out of Ruby entirely. `User#articles` has the same shape and the same fix — destroying a user currently loads every one of their articles into memory.

---

## 9. No cache store, no HTTP caching, no Jbuilder fragment caching — **Medium**

**Where:** `config/environments/production.rb:58` (`config.cache_store` commented out).

`perform_caching = true` is set, but the store defaults to `FileStore` on local disk — which means findings 5 and 6 have nowhere to cache to, and nothing is shareable across processes or hosts.

Two further layers are unused:

**Jbuilder per-row partial rendering.** `index.json.jbuilder` does `json.array! @articles, partial: 'articles/article', as: :article`. Jbuilder resolves and renders the template once per element; at 20 elements that's 20 template renders plus 20 nested `profiles/_profile` renders. Once a cache store exists, `json.array! @articles, partial: …, as: :article, cached: true` keys each row on the article's `cache_key` and skips re-rendering unchanged rows. Note the per-viewer `favorited`/`following` fields would need to move outside the cached fragment (or into a composite cache key) to stay correct — another reason to compute them as sets in the controller per finding 1.

**HTTP caching.** No `fresh_when` / `stale?` anywhere. `articles#show` and `profiles#show` are ideal candidates:

```ruby
def show
  @article = Article.find_by_slug!(params[:slug])
  fresh_when(@article)   # ETag + Last-Modified; 304s cost nothing to serve
end
```

**Fix:** add a shared store and turn both layers on.

```ruby
config.cache_store = :mem_cache_store, ENV['MEMCACHE_SERVERS']   # or :redis_store
```

---

## 10. `log_level = :debug` in production — **Medium**

**Where:** `config/environments/production.rb:49`.

At `:debug`, ActiveRecord logs every SQL statement with its bind parameters. Combined with finding 1's 63 queries per request, that is ~63 formatted, synchronously-written log lines per request. Log formatting and blocking I/O to stdout/disk are measurable at load, and the volume makes the logs unusable for actually finding problems.

**Fix:** `config.log_level = :info`. If you need query-level detail, get it from an APM sampler (finding 15) rather than by logging everything. The commented-out `config.log_tags = [:uuid]` is worth enabling at the same time so request-scoped lines can be correlated.

---

## 11. Missing unique index on `favorites(user_id, article_id)` — **Medium**

**Where:** `db/schema.rb:41-49`; `app/models/user.rb:27`.

```ruby
def favorite(article)
  favorites.find_or_create_by(article: article)
end
```

`find_or_create_by` is read-then-write with no constraint behind it. Two concurrent `POST /api/articles/:slug/favorite` requests both miss on the read and both insert — producing duplicate favorites, a `favorites_count` that over-counts permanently (the counter cache increments twice but `unfavorite`'s `destroy_all` removes both and decrements twice, so it can also drift *negative* in interleaved orders), and inflating the `favorited_by` join count from finding 5.

Performance-wise, `favorited?` and `favorite` both filter on `(user_id, article_id)` but only single-column indexes exist, so the database scans all of a user's favorites to find one row.

**Fix:** add `add_index :favorites, [:user_id, :article_id], unique: true` (dedupe existing rows and recompute `favorites_count` first), and let the constraint do the work:

```ruby
def favorite(article)
  favorites.create!(article: article)
rescue ActiveRecord::RecordNotUnique
  favorites.find_by!(article_id: article.id)
end
```

Also: `unfavorite` calls `article.reload` (`user.rb:33`) solely to refresh the counter cache for the view — a full row re-fetch including the `text` body column. `article.reload(select: :favorites_count)` or simply re-reading the counter is cheaper.

---

## 12. Per-request overhead in `ApplicationController` — **Low-Medium**

**Where:** `app/controllers/application_controller.rb`.

Three small, constant costs paid on every single request:

- **`underscore_params!`** (`:44`) runs `params.deep_transform_keys!(&:underscore)` before every action. `underscore` on a String is a chain of `gsub`/`tr` allocations; this walks and rebuilds the entire params hash, including the bodies of article payloads. Small, but it is on 100% of requests and buys camelCase tolerance that the Jbuilder `key_format camelize: :lower` initializer only needs on the *output* side.
- **Cookie sessions on a token API.** `config/initializers/session_store.rb` mounts `:cookie_store`, and Devise's `:rememberable`/`:trackable` keep Warden in the middleware stack. `current_user` (`:37`) calls `super` — a Warden lookup — before falling back to the JWT id. For a stateless bearer-token API, the session machinery is pure overhead on every request. Consider `config.session_store :disabled` and removing `:rememberable`.
- **`User.find(@current_user_id)`** on every authenticated request. Unavoidable in general, but note it raises `RecordNotFound` (→ 404) when `@current_user_id` is nil, which makes the failure mode confusing. `User.find_by(id: @current_user_id)` is the safer form.

None of these is individually significant; together they are a fixed tax worth trimming after findings 1–3 are done.

---

## 13. Unstable sort order breaks offset pagination — **Low-Medium**

**Where:** `articles_controller.rb:13`.

`ORDER BY created_at DESC` with no tiebreaker. `created_at` is not unique — bulk imports, seeds, or two articles posted in the same second produce ties, and the database is free to order tied rows differently between the page-1 and page-2 queries. Clients then see duplicated or silently skipped articles while scrolling. This is a correctness bug caused by a pagination strategy, and it is also the reason offset pagination is the wrong tool here.

**Fix:** `order(created_at: :desc, id: :desc)` as an immediate patch (and match the composite index from finding 4), then migrate to keyset pagination per finding 3.

---

## 14. `favorites_count` is nullable with no default — **Low**

**Where:** `db/schema.rb:21`, `t.integer "favorites_count"` — no `default: 0`, no `null: false`.

The symptom is visible in the view: `json.favorites_count article.favorites_count || 0` (`_article.json.jbuilder:4`). Every article created before its first favorite carries `NULL`, and every serialization pays a Ruby-side coalesce. Rails' `update_counters` wraps the column in `COALESCE` so the counter still works, but the `NULL` state means you can't index or filter on it usefully (e.g. a future "most favorited" sort).

**Fix:** `change_column_default :articles, :favorites_count, 0`, backfill `UPDATE articles SET favorites_count = 0 WHERE favorites_count IS NULL`, then `change_column_null :articles, :favorites_count, false`, and drop the `|| 0` from the template. Add a rake task to recompute counters (`Article.find_each { |a| Article.reset_counters(a.id, :favorites) }`) after fixing finding 11, since existing drift won't self-heal.

---

## 15. Nothing here is observable — **Low severity, but it blocks everything else**

There is no APM agent, no `rack-mini-profiler`, no Bullet, no `sql.active_record` subscriber, no slow-query logging, no request-ID tagging. The `log_level = :debug` setting (finding 10) is the closest thing to instrumentation, and it produces volume rather than signal.

Every number in this report is read off the source. Before and after the fixes above, you want measurement:

- **`bullet`** in development and test — it would have flagged finding 1 on day one, and it will catch the next one.
- **`rack-mini-profiler`** in development for per-request query counts and timings.
- An **APM** (Skylight, Scout, New Relic) or at minimum a `sql.active_record` subscriber that logs per-request query counts and total DB time, so a regression from 5 queries back to 63 shows up as an alert rather than a support ticket.
- A **request-count assertion** in the test suite for `articles#index` and `comments#index` — these two endpoints have repeatedly proven able to regrow N+1s as serializer fields are added.

---

## Recommended order of work

**Week 1 — stop the bleeding.**
1. Clamp `limit`/`offset`, paginate and `includes(:user)` on comments (finding 3). One afternoon, removes the DoS vector.
2. Add the sort indexes (finding 4). One migration.
3. Drop `log_level` to `:info` (finding 10). One line.
4. Remove `has_many :articles` from `Article` (finding 8). One line; also fixes a broken endpoint.

**Week 2 — the main event.**
5. Batch-load `favorited`/`following` and add `cached_tag_list` (finding 1). This is the 63→5 query change and the largest single win in the repo.
6. Add Bullet and a query-count test around it so it stays fixed (finding 15).

**Week 3 — infrastructure.**
7. Migrate to PostgreSQL, add `config/puma.rb`, align pool and threads, restore foreign keys (finding 2).
8. Add a shared cache store; cache the tag list and the article count; enable `fresh_when` on `show` actions (findings 5, 6, 9).

**Backlog.**
9. Unique index on favorites + counter-cache backfill (findings 11, 14).
10. Tag case-matching normalization (finding 7).
11. Keyset pagination, replacing both the offset scheme and the `count` query (findings 3, 5, 13).
12. Trim per-request middleware overhead (finding 12).

**One cross-cutting note:** the stack is on Rails 4.2.6, which reached end-of-life in 2017. Beyond the security exposure, it means no access to `includes` improvements, modern `ActiveRecord::Relation` optimizations, or Ruby 3.x performance work — and several gems here (`acts_as_follower` 0.2.1, `acts-as-taggable-on` 3.5) are pinned to versions whose query generation is the direct cause of findings 1 and 7. A framework upgrade is out of scope for a performance pass, but it should be on the roadmap; some of the fixes above are working around the age of the stack rather than around the application's own code.
