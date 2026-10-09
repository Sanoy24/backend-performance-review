# Performance Review: rails-realworld-example-app

**Stack:** Rails 4.2.6, SQLite3, Puma 3.4.0 (no config file), Jbuilder, Devise 4.2, acts-as-taggable-on 3.5.0, acts_as_follower 0.2.1.
**Scope:** All application code under `app/`, `config/`, `db/schema.rb`, and `Gemfile`/`Gemfile.lock`.

## Summary

The API's hot paths are the article list (`GET /api/articles`, `GET /api/articles/feed`) and comments (`GET /api/articles/:slug/comments`). They have per-row N+1 queries hidden in Jbuilder partials, and clients can choose any page size, so a single request can trigger thousands of queries. Several common query shapes have no supporting index. In production, the app runs on SQLite with a connection pool smaller than Puma's default thread count, so throughput is capped and requests will fail with timeouts under concurrent load.

| # | Finding | Severity | Effort |
|---|---------|----------|--------|
| 1 | N+1 queries in article serialization (tags, favorited, following) | Critical | Medium |
| 2 | Unbounded `limit`, so clients choose how much N+1 work runs | Critical | Small |
| 3 | SQLite in production, plus a pool of 5 against Puma's 16 threads | High | Small–Large |
| 4 | Comments index: N+1 on author and follow status, and no pagination | High | Small |
| 5 | Missing composite indexes (articles, comments, favorites, follows) | High | Small |
| 6 | Tags endpoint aggregates every tagging on every request | Medium | Small |
| 7 | Duplicate favorites and follows can be created (no unique constraint) | Medium | Small |
| 8 | Every list request runs a separate `COUNT(*)`, and deep `OFFSET` pagination | Medium | Medium |
| 9 | Cascading deletes load and destroy rows one at a time (and a bogus association) | Medium | Small |
| 10 | Production log level `:debug` | Low | Trivial |
| 11 | Smaller items (bcrypt cost on login without throttling, stale counter after favorite, per-request user lookup) | Low | Small |

---

## 1. N+1 queries in article serialization — Critical

**Where:** `app/views/articles/_article.json.jbuilder`, `app/views/profiles/_profile.json.jbuilder`, `app/models/user.rb#favorited?`, used by `ArticlesController#index` and `#feed`.

```ruby
# _article.json.jbuilder
json.(article, ..., :tag_list)                                         # query per article
json.author article.user, partial: 'profiles/profile', as: :user
json.favorited signed_in? ? current_user.favorited?(article) : false  # query per article
# _profile.json.jbuilder
json.following signed_in? ? current_user.following?(user) : false    # query per article
```

The controller only does `includes(:user)`. Each rendered article then runs:

- **`tag_list`**: acts-as-taggable-on 3.5 builds this through `tags_on(context)`, which runs a `tags JOIN taggings` query for each article. `includes(:tags)` does not fix this, because the gem applies its own `where` to the association.
- **`current_user.favorited?(article)`**: `favorites.find_by(article_id: ...)`, one query per article.
- **`current_user.following?(article.user)`**: acts_as_follower runs a `COUNT(*)` on `follows` for each author. Rails' per-request query cache absorbs repeats for the same author, but each distinct author still costs one query.
- **Partial rendering overhead**: `json.array! ... partial:` renders the partial once for every element. In Jbuilder 2.x, rendering many small partials adds noticeable CPU cost.

For a signed-in user, a 20-article page costs about 1 + 1 (count) + 20 × 3 = **~62 queries**. An anonymous user costs ~22. With finding #2 this grows with the page size.

**Fix:**
1. Batch-load per-page lookups in the controller and expose them as sets:
   ```ruby
   ids = @articles.map(&:id)
   @favorited_ids = signed_in? ? current_user.favorites.where(article_id: ids).pluck(:article_id).to_set : Set.new
   author_ids = @articles.map(&:user_id).uniq
   @following_ids = signed_in? ? Follow.unblocked.where(follower_id: current_user.id, follower_type: 'User',
                                     followable_type: 'User', followable_id: author_ids).pluck(:followable_id).to_set : Set.new
   ```
   Then in the partials: `json.favorited @favorited_ids.include?(article.id)` and `json.following @following_ids.include?(user.id)`. Keep the single-object path (`show`) working by falling back to the per-object call when the set is nil.
2. For tags, either:
   - add a `cached_tag_list` string column to `articles`. acts-as-taggable-on detects it automatically, keeps it up to date on save, and serves `tag_list` from it with no query. This is the simplest option. Run `ActsAsTaggableOn::Taggable::Cache` / `article.save` to backfill existing rows, **or**
   - preload with `includes(taggings: :tag)` and render `article.taggings.map { |t| t.tag.name }` instead of `tag_list`.
3. Optionally render the list inline (`json.array! @articles do |article| ... end`) or use `cached: true` instead of a partial for each element.

**Expected result:** a fixed ~5 queries per list request, whatever the page size.

## 2. Unbounded `limit` — Critical

**Where:** `ArticlesController#index` and `#feed`:
```ruby
.offset(params[:offset] || 0).limit(params[:limit] || 20)
```
`limit` comes straight from the client. `?limit=100000` loads every article into memory, runs about 3 queries per article (finding #1), and builds a single huge JSON response. Any anonymous client can do this. It is both a DoS vector and a source of latency spikes.

**Fix:** clamp both values:
```ruby
limit  = params[:limit].to_i.clamp(1, 100) rescue 20   # Ruby 2.4+, or [[x,1].max,100].min
offset = [params[:offset].to_i, 0].max
```
Put this in a shared helper used by `index`, `feed`, and the comments index (finding #4).

## 3. SQLite in production and an undersized connection pool — High

**Where:** `config/database.yml`, plus the absence of `config/puma.rb`.

- `production:` uses `adapter: sqlite3`. SQLite allows only one writer at a time and locks the whole database file. Concurrent writes (favorites, comments, counter-cache updates, follows) are serialized. Under load they wait up to `timeout: 5000` ms and then fail with `SQLite3::BusyException`. SQLite also can't be shared by more than one app host, so you can't scale horizontally.
- `pool: 5`, but Puma 3.4 with no config file defaults to **0–16 threads**. Under concurrency, threads 6–16 block waiting for a connection and raise `ActiveRecord::ConnectionTimeoutError` after 5 s.

**Fix:**
- Short term: add a `config/puma.rb` with `threads ENV.fetch('RAILS_MAX_THREADS', 5)` and set `pool: <%= ENV.fetch('RAILS_MAX_THREADS', 5) %>`, so the pool size always matches the thread count. Use `workers` for CPU parallelism.
- Real fix: move production to PostgreSQL (or MySQL). If SQLite has to stay for now, at least enable WAL mode (`PRAGMA journal_mode=WAL`) so reads stop blocking behind writes.

## 4. Comments index: N+1 and no pagination — High

**Where:** `CommentsController#index` and `app/views/comments/_comment.json.jbuilder`.

```ruby
@comments = @article.comments.order(created_at: :desc)
```
- There's no `includes(:user)`, so `comment.user` runs a `SELECT` on `users` for each comment.
- The profile partial runs `current_user.following?(user)` for each comment author (finding #1).
- There's no limit, so a heavily discussed article returns every comment and runs about 2 queries per comment.

**Fix:** `@article.comments.includes(:user).order(created_at: :desc).limit(...).offset(...)`, plus the batched `@following_ids` set from finding #1. Add pagination parameters with the same clamping as finding #2. Also back this query with the `(article_id, created_at)` index from finding #5.

## 5. Missing composite indexes — High

**Where:** `db/schema.rb`. The table below lists the queries the app runs and the indexes that would support them.

| Query (origin) | Current index | Recommended |
|---|---|---|
| `articles ORDER BY created_at DESC LIMIT n` (index, every page) | none on `created_at`, so a full scan and sort | `add_index :articles, :created_at` |
| `articles WHERE user_id IN (...) ORDER BY created_at DESC` (feed, `authored_by`) | `user_id` only | `add_index :articles, [:user_id, :created_at]` |
| `comments WHERE article_id = ? ORDER BY created_at DESC` | `article_id` only | `add_index :comments, [:article_id, :created_at]` (replaces the single-column one) |
| `favorites WHERE user_id = ? AND article_id = ?` (`favorited?`, `favorite`, `unfavorite`) | separate single-column indexes | `add_index :favorites, [:user_id, :article_id], unique: true` (see finding #7) |
| `follows WHERE follower_id/type AND followable_id/type AND blocked = f` (`following?`, feed) | `(follower_id, follower_type)` only | `add_index :follows, [:follower_id, :follower_type, :followable_id, :followable_type], unique: true, name: 'index_follows_unique'` |

Without the `created_at` index, the default home-page listing sorts the whole `articles` table on every request, and that cost grows linearly with the data.

## 6. Tags endpoint aggregates every tagging on every request — Medium

**Where:** `TagsController#index`:
```ruby
render json: { tags: Article.tag_counts.most_used.map(&:name) }
```
`Article.tag_counts` builds a `GROUP BY` / `COUNT` over `taggings JOIN tags` (scoped to articles) on every call. `most_used` with no argument defaults to a limit of 20, but the aggregation itself still scans every tagging row. This endpoint is usually loaded on every page view of the front end.

**Fix:** the `tags` table already has a `taggings_count` counter cache (migration `add_taggings_counter_cache_to_tags`). Use `ActsAsTaggableOn::Tag.most_used(20).pluck(:name)`, which sorts the small `tags` table and never touches `taggings`. Add an index on `tags.taggings_count` if the table grows. Wrap the result in `Rails.cache.fetch('popular_tags', expires_in: 5.minutes)` and set HTTP caching (`expires_in 5.minutes, public: true`), since the response is identical for every user.

## 7. No uniqueness constraint on favorites and follows — Medium

**Where:** `User#favorite` (`favorites.find_or_create_by(article: article)`) and acts_as_follower `follow`.

`find_or_create_by` is a check-then-insert, so it races. Double-clicks or retries can insert duplicate `favorites` rows. Duplicates inflate `articles.favorites_count` through `counter_cache`, and every later `favorited?` / `unfavorite` has to deal with them. The same applies to `follows`. This is mainly a correctness problem, but it also grows the tables and inflates feed join cardinality.

**Fix:** add the unique composite indexes from finding #5 and rescue `ActiveRecord::RecordNotUnique` in `favorite`. Deduplicate existing rows before adding the index, then reset counters with `Article.reset_counters(id, :favorites)`.

## 8. A count query on every list request, and deep OFFSET pagination — Medium

**Where:** `ArticlesController#index` / `#feed`: `@articles_count = @articles.count`, and `.offset(params[:offset])`.

- Each list request runs a second full query just to count. With the `tagged_with` / `favorited_by` filters, that's a multi-join `COUNT`, and for `tagged_with` acts-as-taggable-on produces a `COUNT(DISTINCT ...)`-style query.
- `OFFSET n` makes the database read and discard n rows, so deep pages get progressively slower.

**Fix:** cache the unfiltered total (for example, `Rails.cache` with a short TTL, or a counter). Don't count on every page for filtered views, or cap it. For deep pagination, use keyset pagination (`WHERE created_at < :cursor ORDER BY created_at DESC LIMIT n`) on top of the `created_at` indexes. The RealWorld API contract requires `articlesCount`, so caching it is the realistic option.

## 9. Cascading deletes load rows one at a time, plus a bogus association — Medium

**Where:** `app/models/article.rb` and `app/models/user.rb`.

- `Article has_many :articles, dependent: :destroy` is meaningless. There's no `articles.article_id` column, so `Article#destroy` will run `SELECT articles.* WHERE articles.article_id = ?` and fail. **Remove it.** It's a bug that breaks `DELETE /api/articles/:slug`.
- `dependent: :destroy` on `favorites` / `comments` (Article) and on `articles` / `favorites` / `comments` (User) instantiates every child row and deletes it individually, running callbacks for each. That's one `DELETE` per row, and favorites also decrement the counter cache. Deleting a popular article or an active user runs thousands of statements inside one request and holds SQLite's write lock the whole time (finding #3).
- `User#unfavorite` uses `destroy_all` and then `article.reload`, which is two extra round-trips. It's acceptable but could be tighter.

**Fix:** use `dependent: :delete_all` where no callbacks are needed (comments). For favorites, `delete_all` skips the counter cache, but that doesn't matter when the parent article is being deleted anyway. Add DB-level foreign keys with `ON DELETE CASCADE` once you're on PostgreSQL. Move user deletion to a background job.

## 10. Production log level `:debug` — Low

**Where:** `config/environments/production.rb`: `config.log_level = :debug`.

This logs every SQL statement and every partial render. Combined with the N+1 patterns, a single list request writes hundreds of log lines. That costs synchronous I/O and disk space, and it buries useful logs.

**Fix:** `config.log_level = :info`, or set it through an environment variable.

## 11. Smaller items — Low

- **Login is CPU-heavy and unthrottled.** `config.stretches = 11` (bcrypt), and `SessionsController#create` runs `valid_password?` on every attempt, about 100–250 ms of CPU while holding a Puma thread. That's appropriate for security, but with no rate limiting (for example, `rack-attack`) it's an easy way to exhaust the thread pool. Add per-IP and per-email throttling.
- **Stale `favorites_count` after favoriting.** In `FavoritesController#create`, `counter_cache` increments the database column but not the in-memory `@article`, so the response shows the old count. `unfavorite` already reloads. Call `@article.reload` (or increment in memory) after `favorite`. This is a correctness issue that comes from how counter caching is used.
- **Per-request user lookup.** `current_user` does `super || User.find(@current_user_id)`. `super` invokes Devise/Warden first, which is pointless for JWT requests and adds overhead. Use `User.find(@current_user_id) if @current_user_id` directly. This is minor, but it runs on every authenticated request.
- **`underscore_params!`** runs `deep_transform_keys!` on all params for every request. It's negligible unless payloads are large; just be aware of it.
- **No HTTP caching.** `show` and `index` for anonymous users could use `fresh_when(@article)` / ETags. That would be a cheap win once the N+1s are gone.

---

## Recommended order of work

1. **Clamp `limit`/`offset`** (finding #2). Ten minutes, and it removes the DoS vector.
2. **Fix the pool/thread mismatch** (finding #3, short-term part) and **remove the bogus `has_many :articles`** (finding #9).
3. **Add the indexes** (finding #5) in a single migration, deduplicating favorites and follows first (finding #7).
4. **Remove the N+1s** in the article and comment serializers (findings #1 and #4): `cached_tag_list`, batched favorited/following sets, and `includes(:user)` on comments. Verify with `bullet` or by counting SQL lines in the log. The target is a constant query count per endpoint.
5. **Cache the tags endpoint** (finding #6) and reconsider the per-request count (finding #8).
6. **Plan the move from SQLite to PostgreSQL** before real production traffic.
