# Object storage: S3, GCS, Azure Blob, and MinIO compared

Load after `databases/universal.md` and `databases/object-storage.md`.

This file contains **only what those files do not give you**: request-count-dominated cost,
key/prefix design, full-object-replacement semantics, multipart necessity, listing
limitations, and request/egress cost as first-class dimensions are covered there and are not
repeated here.

**This is a deliberately comparative file, not a single-engine one** (see
`docs/roadmap.md`). S3-compatible services, Google Cloud Storage, and Azure Blob Storage are
independently designed systems, not forks of one another — their consistency guarantees,
upload mechanics, and tier-pricing structures genuinely differ, and the category file
explicitly declines to assert any of them. **Confirm which provider is actually deployed
before applying a row from any table below** — a claim correct for one is frequently wrong
for another.

---

## 1. Detection signals

| Provider | Python | Node.js | Go | JVM | .NET |
|:--|:--|:--|:--|:--|:--|
| **S3-compatible** | `boto3` | `@aws-sdk/client-s3` | `aws-sdk-go-v2/service/s3` (v2), `aws-sdk-go/service/s3` (v1) | `com.amazonaws.services.s3` (v1), `software.amazon.awssdk:s3` (v2, Gradle notation only — see note) | (AWS SDK for .NET, connection-string/config-driven) |
| **GCS** | `google-cloud-storage` | `@google-cloud/storage` | `cloud.google.com/go/storage` | `google-cloud-storage` (Maven coordinate substring) | `Google.Cloud.Storage` |
| **Azure Blob** | `azure-storage-blob` | `@azure/storage-blob` | `azure-sdk-for-go/sdk/storage/azblob` | `azure-storage-blob` (Maven coordinate substring) | `Azure.Storage.Blobs` |
| **MinIO** | `minio` (shared token; MinIO's client libraries across languages are all named `minio`) | | | | |

Other signals: `s3://` / `gcs://` connection-scheme prefixes, `amazonaws.com/s3` /
`storage.googleapis.com` / `blob.core.windows.net` hostnames, container images
(`minio/minio`), infrastructure-as-code resource types (`AWS::S3::Bucket`,
`google_storage_bucket`, `azurerm_storage_account`).

**Known residual gap**: the AWS SDK v2 Java/Kotlin client
(`software.amazon.awssdk:s3`) only matches when declared in single-line Gradle notation. A
Maven `pom.xml` splits `groupId` and `artifactId` across separate XML elements with no
reliably matchable contiguous substring — check `pom.xml` by hand for
`software.amazon.awssdk` plus an `s3` artifact when the signal does not fire on a Java repo
using Maven.

---

## 2. What differs from the object-storage baseline, provider by provider

### Read-after-write consistency: resolved, per provider, where the category file declines to

The category file (§7) will not assert a default. Here is what each provider actually
documents:

| Provider | Documented guarantee |
|:--|:--|
| **Amazon S3** | Strong read-after-write consistency for all operations (PUTs and DELETEs), for both new and overwritten objects, since December 2020. Before that date, overwrites and deletes in some regions were only eventually consistent — relevant only to code or assumptions dating from before the change. |
| **Google Cloud Storage** | Strong consistency, including read-after-write and (since a subsequent strengthening) list consistency, since launch. |
| **Azure Blob Storage** | Strong consistency for standard operations. |
| **MinIO** | Depends entirely on the self-hosted deployment's own configuration and erasure-coding setup — there is no vendor SLA to cite; confirm against the specific cluster's documented behavior rather than assuming parity with a managed service. |

A codebase carrying defensive retry-after-write logic written for an object store's
eventually-consistent era, deployed against a provider (or a post-2020 S3 configuration)
that no longer needs it, is dead code rather than a bug — worth noting, not necessarily
worth removing without confirming nothing else depends on the delay.

### Multipart mechanics use different names, different limits, and — for GCS — a different extra capability

| Provider | Mechanism | Limits |
|:--|:--|:--|
| **S3** | Multipart upload: independently-uploaded parts, assembled server-side | Parts 5 MiB–5 GiB (last part may be smaller); up to 10,000 parts; 5 TiB max object size |
| **GCS** | Resumable uploads (chunked; chunks recommended in 256 KiB-aligned sizes) | No fixed part-count ceiling in the same shape as S3 |
| **GCS (additional)** | **Compose**: combine up to 32 existing objects into one, server-side, without re-uploading their data | A capability S3 and Azure do not have in this form — worth naming explicitly when a workload assembles large objects from parts already in the bucket |
| **Azure Blob** | Block blobs: `Put Block` + `Put Block List` | Up to 50,000 blocks per blob, each up to 4,000 MiB |
| **Azure Blob (additional)** | **Append blobs**: a distinct blob type optimized for append-only writes (log-shipping style workloads) | Not a general-purpose object type; choosing it is a real, checkable schema decision for append-heavy workloads |

Code that reimplements client-side "download everything, modify, re-upload" to avoid
learning a provider's actual assembly mechanism is paying full-object transfer cost the
provider's own API exists specifically to avoid.

### Storage-tier retrieval latency is a correctness-adjacent trap, not merely a cost lever

Every major provider offers a cold/archive tier at a much lower storage cost, with retrieval
that is **not synchronous**:

- **S3 Glacier / Glacier Deep Archive** — restoration takes minutes to many hours depending
  on the retrieval tier requested; the object is not readable until restoration completes.
- **Azure Archive tier** — rehydration takes hours; a blob must be moved to a hot/cool tier
  (or rehydrated in place) before it can be read.
- **GCS Coldline / Archive** — readable immediately (no rehydration step), but carries a
  **minimum storage duration**; deleting or moving an object out of the tier before that
  duration elapses incurs an early-deletion charge — a cost trap rather than a latency one.

**Application code that issues a synchronous read against an object that may be archived is
a checkable, high-severity finding on S3 and Azure specifically** — the read will fail or
hang far outside any reasonable timeout, not merely run slowly. This is the storage-tier
form of the category file's general tier/access-frequency trade-off (§6), sharpened into an
availability question for two of the three major providers.

### Request-rate partitioning can still produce a hot prefix, despite modern auto-scaling

The category file names prefix concentration as the object-storage form of a hot partition.
Provider-specific detail: S3 automatically scales request capacity per prefix in the
background, which has substantially relaxed the older guidance to hash-randomize key
prefixes for high request rates — but scaling is not instantaneous, so a **sudden** burst
concentrated on a narrow, newly-hot key range can still be throttled before partitioning
catches up. Azure Blob partitions similarly, by account, container, and blob-name prefix.
The finding is the same either way: check whether a workload's key design concentrates a
sharp burst of new traffic on one narrow prefix, not whether prefixes are randomized as a
blanket rule that current auto-scaling has made largely unnecessary.

### MinIO is the deployment-mode outlier: "is it fast" depends on infrastructure the review can actually see

Every provider above is a fully managed service — its underlying disks, network, and
partitioning are abstracted away, and a performance review has nothing to inspect there.
**MinIO is self-hosted and S3-API-compatible**, storing data via erasure coding across the
drives and nodes an operator provisions. This makes MinIO's own infrastructure a legitimate
part of the review in a way none of the managed providers are: drive type (spinning disk
versus SSD/NVMe), network bandwidth between nodes, and the configured erasure-coding parity
level all directly determine throughput and durability trade-offs, and are checkable
operational facts rather than unknowns to wave at a vendor SLA.

---

## 3. Diagnostics

| Provider | Command / source | What it shows | Production safety |
|:--|:--|:--|:--|
| S3 | CloudWatch S3 request metrics (`AllRequests`, `4xxErrors`, `5xxErrors`, and specifically `SlowDown` throttling responses) | Request volume and throttling — the direct evidence for a hot-prefix finding | **safe-on-production** |
| S3 | S3 Storage Lens | Account/bucket-level usage and activity trends, including request patterns | **safe-on-production** |
| S3 | `aws s3api head-object` | Object metadata (size, storage class, restore status) without downloading | **safe-on-production** |
| GCS | Cloud Monitoring `storage.googleapis.com/api/request_count` and latency metrics | Request volume and latency by method and response code | **safe-on-production** |
| GCS | `gsutil stat` / `gcloud storage objects describe` | Object metadata including storage class | **safe-on-production** |
| GCS | Cloud Storage usage and audit logs | Per-request logs when enabled | **safe-on-production** to read; enabling verbose logging has its own storage cost |
| Azure Blob | Azure Storage Insights / Metrics (transactions, latency, ingress/egress) | Request volume, latency, and error rate per account | **safe-on-production** |
| Azure Blob | `az storage blob show` | Blob metadata including access tier | **safe-on-production** |
| Azure Blob | Storage Analytics logging | Per-request logs when enabled | **safe-on-production** to read; enabling has its own cost |
| MinIO | `mc admin trace` | Live request trace against a cluster | **safe-on-production for a short, targeted trace** — a broad or prolonged trace adds real overhead |
| MinIO | Prometheus-format metrics endpoint | Throughput, error rates, per-drive and per-node health — exposed natively, an advantage of self-hosting | **safe-on-production** |
| MinIO | `mc admin info` | Cluster/drive health and erasure-set status | **safe-on-production** |

Every provider's request-count and error-rate metrics are the first thing worth reading for
a suspected hot-prefix or throttling finding — they answer it directly, without needing to
infer anything from application-side latency alone.

---

## 4. Common failure modes and their symptoms

| Symptom | Provider-specific cause to check first |
|:--|:--|
| A read hangs or fails far past any reasonable timeout | Object is in an archive tier (S3 Glacier, Azure Archive) requiring explicit, asynchronous rehydration before it is readable |
| Unexpected charges for deleted or moved objects | GCS Coldline/Archive early-deletion charge — object removed before its minimum storage duration elapsed |
| Sharp, sudden throttling (`SlowDown` on S3) during a traffic spike to new keys | Request-rate partitioning has not caught up with a burst concentrated on a narrow, newly-hot prefix |
| An "assemble large object from parts" workflow re-uploads data unnecessarily | Not using GCS's `compose` operation where available; falling back to client-side download-modify-reupload |
| A log-shipping or audit-trail workload performs poorly on Azure Blob | Using block blobs for an append-heavy pattern instead of append blobs |
| MinIO throughput is well below expectations | Underlying drive type, network bandwidth between nodes, or erasure-coding parity level not sized for the workload |
| Defensive retry-after-write logic never actually triggers | Dead code from an eventually-consistent era, running against a provider (or S3 post-2020) with strong consistency |

---

## 5. Configuration worth checking, and what it trades

| Setting | Trade-off |
|:--|:--|
| Multipart/chunk size (S3 parts, GCS resumable-upload chunks, Azure blocks) | Fewer round trips and better parallelism versus memory held per in-flight part and retry granularity on failure |
| Storage tier assignment versus actual access frequency | Storage cost versus retrieval latency (S3/Azure) or early-deletion risk (GCS) |
| Key/prefix design under bursty new-key traffic | Even request distribution versus the simplicity of a naturally-ordered (e.g. timestamp-leading) key scheme |
| GCS `compose` versus client-side reassembly | Server-side, no-re-upload assembly versus simpler client logic that pays full transfer cost |
| Azure blob type (block versus append) | Matching the actual write pattern versus defaulting to block blobs uniformly |
| MinIO erasure-coding parity level | Durability against drive loss versus usable capacity and write overhead |
| MinIO drive/network provisioning | Throughput and durability versus infrastructure cost — the one provider here where this is a review question at all |

---

## 6. Version differences worth knowing

- **S3's strong consistency (December 2020)** is a real landmark: any claim, blog post, or
  piece of defensive code referencing S3's *eventual* consistency for overwrites/deletes
  predates it and no longer describes current behavior.
- **Azure Data Lake Storage Gen2** (hierarchical namespace enabled on a storage account)
  changes Azure Blob's performance characteristics materially — directory rename and listing
  operations become real, atomic, hierarchical operations rather than the flat-namespace
  prefix emulation the category file describes as the norm for this category. Confirm
  whether hierarchical namespace is enabled before assuming flat-namespace reasoning applies
  to an Azure target.
- **GCS dual-region and turbo-replication options** trade write latency and cost against
  cross-region durability and failover characteristics — a deployment-configuration fact,
  not a code-level one, worth recording during discovery when it is relevant to the review.
- **MinIO's erasure-coding and versioning feature set has changed across releases**; confirm
  behavior against the deployed MinIO version's own documentation rather than assuming
  current documentation describes an older cluster.

**Confirm version- and configuration-specific claims against the current documentation for
the deployed provider and configuration.**

---

## 7. What this file does NOT cover

- Request-count-dominated cost, key/prefix design, full-object-replacement semantics,
  multipart necessity as a hard limit, listing's lack of a server-side filter beyond prefix,
  and request/egress cost as first-class dimensions — see `databases/object-storage.md`.
- Identity and access management (IAM policies, bucket policies, SAS tokens, signed URLs) in
  depth, beyond noting that request-signing exists.
- Encryption at rest and in transit, and key-management service integration.
- Cross-region replication configuration and its bandwidth/lag characteristics.
- Static website hosting and CDN integration (CloudFront, Cloud CDN, Azure CDN) in front of
  object storage.
- Exact current pricing for any provider or tier; this file names *what* is billed and *what
  trades against what*, never a rate, which changes independently of this content.
- MinIO cluster deployment and expansion mechanics.
- Specific numeric threshold recommendations beyond the documented limits and mechanisms
  cited above; recommend the measurement that determines the right value for a given
  workload.
