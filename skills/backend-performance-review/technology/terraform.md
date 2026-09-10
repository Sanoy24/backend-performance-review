# Terraform

Load after `infrastructure/resources.md`. Terraform is not a runtime — it provisions the
infrastructure `infrastructure/resources.md`'s arithmetic reasons about. This file's role is
therefore different in kind from the other technology files in this directory: it is a map
from Terraform's own resource types to the specific arguments that feed that arithmetic,
across the three major cloud providers, so "check the infra config" becomes "read this
argument, in this resource block."

---

## 1. Detection signals

Any `.tf`/`.tf.json` file; a `terraform` CLI invocation in CI/CD; a `.terraform/` directory
or `terraform.tfstate`/`terraform.tfstate.backup` (state should never be read for content —
it commonly contains resource attribute values and, depending on the provider, may include
sensitive data; its *presence* is a signal, its content is not something this review opens).

---

## 2. What differs from the general resources baseline

### Terraform is where the numbers `infrastructure/resources.md`'s arithmetic needs are actually declared

The category file's central check is whether the runtime's configured behavior fits inside
an enforced limit — heap versus container memory, pool size versus connection limit, replica
count versus datastore capacity. When infrastructure is provisioned via Terraform, **these
values are not unknowns to be inferred; they are literal arguments in resource blocks**,
readable directly from the repository with no runtime evidence required. The concrete
mapping, by provider:

| What the arithmetic needs | AWS | GCP | Azure |
|:--|:--|:--|:--|
| Container memory/CPU limit | `aws_ecs_task_definition`'s `memory`/`cpu`; `aws_lambda_function`'s `memory_size` | `google_cloud_run_service`'s `resources.limits`; `google_cloudfunctions_function`'s `available_memory_mb` | `azurerm_container_group`'s `memory`/`cpu`; `azurerm_linux_function_app`'s plan-derived limits |
| Replica count / autoscaling bounds | `aws_appautoscaling_target`'s `min_capacity`/`max_capacity`; an `aws_autoscaling_group`'s `min_size`/`max_size`/`desired_capacity` | `google_cloud_run_service`'s autoscaling annotations (`minScale`/`maxScale`) | `azurerm_linux_web_app`'s scaling rules, or `azurerm_monitor_autoscale_setting`'s `capacity` block |
| Datastore connection ceiling | `aws_db_instance`'s `instance_class` (determines the engine's default `max_connections`, not set directly) | `google_sql_database_instance`'s `tier` (same indirect relationship) | `azurerm_mysql_flexible_server`/`azurerm_postgresql_flexible_server`'s `sku_name` (same) |
| Concurrency limit (serverless) | `aws_lambda_function`'s `reserved_concurrent_executions`; a separate `aws_lambda_provisioned_concurrency_config` for provisioned concurrency — see `technology/serverless.md` for the distinction between the two | `google_cloudfunctions_function`'s `max_instances`/`min_instances` | `azurerm_linux_function_app`'s plan-derived concurrency behavior |
| Timeout ceiling | `aws_lambda_function`'s `timeout`; `aws_lb_target_group`'s `deregistration_delay` and health-check timeouts | `google_cloudfunctions_function`'s `timeout` | `azurerm_linux_function_app`'s `functionAppScaleLimit`/host-level timeout settings |

**Confirm the provider and resource type actually in use before applying a row** — this
table names the common resource types per provider, not an exhaustive list, and provider
modules or newer resource types can expose the same underlying values under different
argument names.

### `count` and `for_each` are where the "how many" in the category file's replica arithmetic is actually declared

`infrastructure/resources.md` §2 says to size connection arithmetic against the autoscaler's
*maximum*, not current replica count. In Terraform, a fixed replica count is often expressed
as a literal `count = 3` or a `for_each` over a fixed set — a hardcoded value directly
readable from the file. An autoscaled maximum instead appears as a `max_capacity`/`maxScale`-
style argument (see the table above) rather than a `count`. Distinguishing "this is a fixed
count, this is the ceiling of an autoscaled range" is the first, purely mechanical step
before the category file's arithmetic can be applied at all.

### Modules can hide the actual values several layers deep

A root module frequently calls a child module (a local `module "x" { source = ... }` block,
or a published registry module) that itself declares the resource with the actual limit or
count argument, sometimes passed through as a variable with its own default, sometimes
hardcoded inside the module and not exposed as a variable at all. **Before concluding a
value is "unknown," trace whether it is set by a variable at the call site, defaulted inside
the module, or genuinely absent from both** — a module input that is never passed at the
call site uses the module's own default, which is a real, discoverable value, not an
unknown.

### `.tfvars` files and remote/environment-specific variable sources change which value is actually live

The same module can be instantiated with different `.tfvars` files (or variables sourced from
a remote backend, a CI/CD pipeline's own variable injection, or a workspace-specific
configuration) per environment — a `min_size` of `1` in a repository's checked-in
`dev.tfvars` says nothing about what `prod.tfvars` (or a CI-injected production value)
actually sets. Confirm which `.tfvars` file, workspace, or variable source corresponds to
the environment actually being reasoned about before treating a value found in the
repository as the production configuration.

---

## 3. Diagnostics

- **`terraform plan`** against the current state, read-only, to see what Terraform believes
  the *current* live configuration is versus what the `.tf` files declare — surfaces drift
  between the two before treating the file contents as ground truth for what is actually
  deployed. `safe-on-production` (a plan does not apply anything).
- **`terraform show` / `terraform state list`**, against a copy of state handled per this
  skill's rule against reading secret-bearing files for content — useful for confirming
  actual applied values without granting broader state access than necessary.
  `safe-on-production` when limited to attribute values already known to be non-sensitive
  for the resource type in question; treat as `not-safe-on-production` by default given state
  commonly contains values that should not be reproduced in a report.
- **`terraform graph`**, to trace a module's actual dependency and value-flow structure when
  a value's source is unclear across several layers of module calls. `safe-on-production`
  (static analysis of configuration, no infrastructure interaction).

---

## 4. Common failure modes and their symptoms

| Symptom | Likely cause |
|:--|:--|
| A connection-limit finding cites a value that turns out not to match production | The value was read from a `.tfvars` file or workspace that is not the one actually applied to production |
| "Unknown" is recorded for a limit that is actually set | The value is declared inside a child module rather than at the root, or defaulted inside the module rather than passed explicitly |
| A replica-count finding treats an autoscaled service's current count as its ceiling | Conflating a `count`/`for_each`-declared fixed number with an autoscaling `max_capacity`-style argument found elsewhere in the same configuration |
| A finding about a resource's configuration does not match what is actually running | Drift between the `.tf` files and applied state, uncaught because `terraform plan` was not run to check for it |

---

## 5. Configuration worth checking, and what it trades

- **Fixed `count`/`for_each` versus an autoscaling resource.** Not itself a trade Terraform
  imposes — it reflects the architecture choice `infrastructure/resources.md` §2 already
  reasons about — but confirming which one is declared is a prerequisite to applying that
  reasoning correctly.
- **Whether limit/capacity values are hardcoded, passed as variables with explicit
  per-environment `.tfvars`, or left at a module's internal default.** Trades traceability
  (a hardcoded value is unambiguous but requires editing code to change) against flexibility
  (a variable-driven value is easy to change per environment but requires tracing to find the
  live value) — a review's confidence in "what is actually deployed" depends on which pattern
  is in use.
- **Module source pinning** (an exact version versus a floating reference). Not a performance
  trade directly, but affects whether the configuration a reviewer reads is guaranteed to
  match what was actually applied at deploy time.

---

## 6. Version differences worth knowing

Terraform's own state format and provider-plugin protocol have changed across major
versions, and individual cloud providers' Terraform provider packages version
independently of Terraform core — a resource argument's availability or default value can
differ between provider versions even when the Terraform core version and the `.tf` syntax
are unchanged. Confirm both the Terraform core version (`required_version` in the
configuration, or `terraform version`) and the specific provider version
(`required_providers` block) before asserting a specific argument's default behavior, since
provider documentation is versioned separately from Terraform's own documentation.

---

## 7. What this file does NOT cover

- Terraform state management mechanics (remote backends, locking, state file performance at
  scale) — an operational concern for the Terraform tooling itself, not for the application
  performance the provisioned infrastructure serves.
- Terraform plan/apply execution speed at scale (large state files, many resources) — a
  developer-experience and CI/CD-pipeline concern, not a runtime performance one.
- Policy-as-code tooling layered on Terraform (Sentinel, OPA/Conftest) — a governance
  concern distinct from the resource-configuration reasoning here.
- Non-Terraform IaC tools (CloudFormation, Pulumi, CDK, Bicep, ARM templates) — a repository
  using one of these for provisioning is not detected by this signal and is not covered by
  this file's provider-specific tables, though the same underlying category-file arithmetic
  still applies once the equivalent values are located in whichever tool is actually in use.
- Provider-specific resource types beyond the common ones tabulated in §2 — the table names
  the frequently-encountered resource types per provider for the values that matter most to
  `infrastructure/resources.md`'s arithmetic, not an exhaustive provider reference.
