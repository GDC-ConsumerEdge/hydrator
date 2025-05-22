# Kubernetes Manifest Hydration

## Table of Contents

- [Architecture](#architecture)
  - [Source of Truth](#source-of-truth)
  - [Bases and Overlays](#bases-and-overlays)
  - [Jinja](#jinja)
  - [Kustomize](#kustomize)
  - [Oras](#oras)
  - [Gatekeeper](#gatekeeper)
- [Tips](#tips)
  - [Increase Rendering Speed with Concurrency](#increase-rendering-speed-with-concurrency)
  - [Filtering](#filtering)
  - [Output verbosity/suppression](#output-verbositysuppression)
  - [Hydration summary](#hydration-summary)
  - [Exit Codes](#exit-codes)
- [Internal App Logical Workflow](#internal-app-logical-workflow)
- [Suggested User Workflow](#suggested-user-workflow)
- [Help](#help)
- [Development](#development)
  - [Tests](#tests)
  - [Building Docker Container](#building-docker-container)

Hydrator is an opinionated Kubernetes resource hydration CLI and workflow for hydrating cluster-and-group specific
manifests. Its intended use case is the hydration of Kustomize-enabled packages of resources at a large scale (
thousands or tens of thousands of clusters) where cluster-level variance is high while enabling users to follow DRY
principles.

The app assumes the following:

* Resource packages adhere to an opinionated directory structure
* Resource packages make use of Kustomize and provide a `kustomization.yaml`
* Gator is installed and accessible in the $PATH
* Kustomize is installed and accessible in the $PATH
* A CSV source-of-truth (adhering to [this](#source-of-truth) section)
* Python 3.12

#### External Requirements

*   **Jinja2**: Used for templating resource files.
*   **Kustomize**: Essential for managing and customizing Kubernetes configurations.
*   **Gatekeeper/OPA/Gator**: For policy enforcement and validation of hydrated manifests. Gator is the specific CLI tool used.
*   **Oras**: To publish hydrated manifests to an OCI registry (optional).

## Architecture

This CLI makes design decisions which implement a highly opinionated workflow.

### Source of Truth

Hydrator requires a source of truth file to be present. This file must use CSV format. The full requirements may
differ based on the repository in which it is used, as this file may contain arbitrary customer-defined columns holding
vales to be used in template rendering. However, at a minimum, the following tables show columns that are required by
this tool for _all repositories_ in which it is used.

> _**Note:** Hydrator performs minimal source of truth data validation, requiring only that the below values exist. Validation of source of truth files is out of the scope of the hydrator tool and is deferred to the `csv-validator` tool instead._

#### Cluster Hydration SoT Required Values

| column        | purpose                                                                                                                          |
|---------------|----------------------------------------------------------------------------------------------------------------------------------|
| cluster_name  | globally-unique name of cluster                                                                                                  |
| cluster_group | Arbitrary grouping to which a cluster belongs. This field links a cluster to an overlay, enabling shared resource configurations. |
| cluster_tags  | Tags associated with a cluster, primarily used for filtering which clusters to hydrate.                                          |

#### Package Hydration SoT Required Values

| column | purpose                                                                                                                            |
|--------|------------------------------------------------------------------------------------------------------------------------------------|
| group  | Arbitrary grouping used during package hydration. Functionally similar to `cluster_group`, it enables sharing of resource packages. |
| tags   | Tags used for filtering purposes during package hydration. Functionally similar to `cluster_tags`.                                   |

### Bases and Overlays

#### Overview

The base library (`base_library/`) contains Kustomize packages of resources. Each package is expected to contain a
`kustomization.yaml` ([docs](https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/)). No naming
convention is specified, required, or enforced for package names or their directory structure; these are completely arbitrary and up to the user and their use case.
There is no intention of supporting environment-specific bases here: each base is
meant to be environment-agnostic, though there is nothing precluding this from being the pattern.

A good starting point is using meaningful names related to the use case. Is the package full of region-specific RBAC for
North America? Use `base_library/rbac-northam`. Package of a single application's container and service resources - for
example, a payments processing service called _payments_: `base_library/payments-app`.

The overlays directory (`overlays/`) contains group or single cluster configuration overlays. Each subdirectory within `overlays/` is
a _kustomization_ that represents a specific, opinionated configuration of base library packages.
A key aspect of this structure is the mapping of a cluster's `cluster_group` (defined in the [Source of Truth](#source-of-truth)) to its corresponding parent overlay directory within `overlays/`. For example, if a cluster has `cluster_group: prod-us-east`, its configuration will be sourced from the `overlays/prod-us-east/` kustomization.

An overlay may refer to a cluster, a group of clusters, or a specific environment. For example, the resources
for a group of lab clusters in North America may be encapsulated in an overlay package named `overlays/nonprod-lab-northam`.

The purpose of overlays is to group clusters together with the intent of configuring them in a _like_ way. This does not
mean that clusters in the same group cannot have cluster-specific values. In fact, any cluster may use a
cluster-specific value. Rather, grouping clusters with an overlay enables them to use the _exact same_
`kustomization.yaml` (within their respective overlay directory), and therefore receive the same resources, transformations, generations, common annotations, common
labels, and many other [features enabled](https://github.com/kubernetes-sigs/kustomize/tree/master/examples) by
Kustomize.

#### Use

* For each group of clusters which should share configuration, create a corresponding overlay directory (e.g., `overlays/<cluster_group_name>/`).
* Overlays refer to base library packages, effectively creating a collection of packages tailored for that group.

### Jinja

In Hydrator, Jinja serves as a powerful templating engine allowing for dynamic customization of Kubernetes manifest files. It enables you to inject values from the [Source of Truth](#source-of-truth) directly into your Kubernetes resource definitions or other files within your resource packages. Jinja is an optional templating feature provided by hydrator. Template designer docs for Jinja may be
found [here](https://jinja.palletsprojects.com/en/3.1.x/templates/).

Jinja is how one injects values from the [Source of Truth](#source-of-truth) into a file, regardless of its type.

Hydrator discovers Jinja files (e.g. `base_library/my-special-package/some_random_file.yaml.j2`) using the file
extension `.j2`. When hydrator encounters this extension during hydration, it immediately templates the file by passing
the current cluster (or package) configuration to Jinja. This configuration comes from the source of truth. Because this
data is processed for every row in the CSV on a per-cluster (or per-group for package hydration) basis, the entire row
is made available to Jinja. Once complete, hydrator then strips the `.j2` extension off the file.

For more information about the order in which hydrator executes hydration, check
the [Internal App Logical Workflow](#internal-app-logical-workflow) section.

### Kustomize

Kustomize is used in base library packages and overlays as described above. Kustomize is run in every directory that the
hydration CLI renders; it is not optional. The Kustomize binary must be in the path.

### Oras

[Oras](https://oras.land/) is leveraged to publish hydrated manifests to OCI registries. Currently, the only supported
OCI registry is GCP Artifact Registry and authentication is handled via Application Default
Credentials ([ADC](https://cloud.google.com/docs/authentication/provide-credentials-adc)).

### Gatekeeper

[Gatekeeper](https://open-policy-agent.github.io/gatekeeper/website/docs/) support has been added
via [gator](https://open-policy-agent.github.io/gatekeeper/website/docs/gator). This enables hydrated manifests to be
checked against a set of policy constraints after they are rendered by the internal hydration pipeline.

The Gatekeeper validation module uses the `gator test` subcommand to perform all its validation. Please refer to
`gator test` documentation to understand this feature in greater depth. How this validator module works is described in
detail here.

#### Invocation

The gatekeeper validator module is enabled by passing the `--gatekeeper-validation` flag to `hydrate`; if it is not
provided, no validation is performed.

#### Constraint Paths

There are two folders that the Gatekeeper validator module checks when invoked by default:

* `validation-gatekeeper/constraints`
* `validation-gatekeeper/template-library`

These may be overridden by using the `--gatekeeper-constraints` flag (as many times as needed) to point to all the files
and directories needed. Each value to the `--gatekeeper-constraints` flag is passed directly to gator

##### Understanding the default paths

`template-library` is where _Gatekeeper constraint
templates_ are stored. Please note that this is a core Gatekeeper concept explained in
their [documentation](https://open-policy-agent.github.io/gatekeeper/website/docs/howto). These files tell Gatekeeper
what rules to use when checking resources - it is the formal definition of a
_policy
check_. For example, this is how one would define a rule that fails if a label is missing, or a pod is missing resource
requests and limits.

`constraints` is where the tool expects _Gatekeeper
constraints_ to be defined. Please note that this is a core Gatekeeper concept explained in
their [documentation](https://open-policy-agent.github.io/gatekeeper/website/docs/howto). Constraints tell Gatekeeper to
take a
_constraint template_, which defines a _policy_, and to apply it against a specific set of matching Kubernetes
_resources_. For example, this would match a policy (i.e. required labels) against specific Kubernetes resources (pods,
services).

##### Unique Module Behavior

The Gatekeeper validator module enables global and group-based constraints if matching folders are found in the
constraints paths used by default. This also applies to user-provided paths.

First, the validator module checks for a folder called `all/`. If found, these policies are checked globally to all
clusters being processed by `hydrate`, regardless of the group to which a cluster belongs.

This is useful when you want to define a set of constraints that apply to the resources of all clusters
_globally_. For example, if you want to define a check that limits the amount of resources any pod can request - and
this is a
_universal_ constraint, place this constraint in a folder called all. For example:

```shell
validation-gatekeeper/
└── constraints
    └── all
        ├── disallowed-image-tags.yaml
        └── required-annotations.yaml
```

In the above example, all the resources in the `validation-gatekeeper/constraints/all` folder would be checked against
the constraints inside, `disallowed-image-tags.yaml` and `required-annotations.yaml`.

Next, the validation module enables _group-based_ policy to be defined. For example, say your cluster belongs to a group
`prod-us` - this is defined in the _source of truth_ for this cluster. When the gatekeeper validation module encounters
a folder in a constraint path which matches the group name, this folder is passed to Gator, just like the `all` folder.
For example:

```shell
validation-gatekeeper
└── constraints
    ├── all
    │   ├── disallowed-image-tags.yaml
    │   └── required-annotations.yaml
    └── prod-us
        ├── centos-max-mem.yaml
        ├── required-annotations.yaml
        └── ubuntu-max-mem.yaml
```

In the above example, once the validation module sees a `prod-us` folder, its contents are automatically included for clusters belonging to the `prod-us` group. The presence of an `all/` directory is significant: constraints within `all/` are applied globally to all clusters. Then, for clusters specifically in the `prod-us` group, the constraints from the `prod-us/` folder are applied *in addition* to those from `all/`. Any other group-specific folders (e.g., `dev-eu/`) or other folders at the same level that do not match the current cluster's group would be excluded from its validation process.

#### How it Works

After the validator module has crawled through the provided (or default) paths, each of these paths is passed to
`gator test`.

`gator` emits constraint violations directly to `stdout` and `stderr` and exits with a non-zero exit code. Any errors
will bubble up through the logs and be presented to the console, are tracked, and will be displayed as as a "wrap up"
summary on a per-cluster basis after hydration completes.

> **Important: Local Testing Considerations**
>
> Due to the limitations of `gator`, you must provide some boilerplate to your constraints when testing locally.
>
> Gator will evaluate its templates on ALL the resources it loads—including _its own_ constraint templates. This behavior can introduce noise and make it difficult to focus on policy violations relevant to your rendered manifests, as you generally do *not* want to validate Gatekeeper against itself.
>
> To mitigate this, every constraint you write *must* include a `namespace`. By convention, this documentation and internal tests use `gator-local`.
For example:

```yaml
apiVersion: templates.gatekeeper.sh/v1
kind: ConstraintTemplate
metadata:
  name: k8sdisallowedtags
  namespace: gator-local
  ^^^^^^^^^^^^^^^^^^^^^^
```

Then each constraint implementing this template must have a match *exclusion* pattern against this namespace. For
example:

```yaml
apiVersion: constraints.gatekeeper.sh/v1beta1
kind: K8sRequiredAnnotations
metadata:
  name: all-resource-required-annotations
  namespace: gator-local
spec:
  match:
    excludedNamespaces: [ "gator-local" ]
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
```

This ensures that when the constraint is matched, Gator considers this exclusion, avoiding checking your Gatekeeper
constraints against themselves.

## Tips

### Increase Rendering Speed with Concurrency

Hydrator has added support for asynchronous hydration of cluster and package manifests using event-based concurrency. It
is disabled by default. To use, pass the `--workers` flag during invocation. For example:

```shell
hydrate -v --workers=32 cluster sot.csv --gatekeeper-validation --split-output
```

The recommended number of workers to use is 2 for every CPU core available. If you are still not seeing the performance
improvements you would like, we recommend allocating more CPUs to hydrator and to increase the number of workers
accordingly.  For example, if you have allocated 16 (v)CPUS, use 32 workers.

> **Note:** Increasing the number of concurrent workers can dramatically reduce the usefulness of hydrator output, as the logs from concurrent tasks will be interleaved in the console. If you are troubleshooting a local developer workflow, it is suggested to disable hydration concurrency by omitting the `--workers` flag to ensure clear, sequential output.

### Filtering

`hydrate cluster` enables you to scope hydration to a subset of clusters in the source of truth by using a number of
flags:

* `--cluster-name` to scope hydration to a single cluster
* `--cluster-group` to limit hydration to a single group
* `--cluster-tag` to match against cluster tags; more than one tag may be provided by passing the flag more than once

`hydrate package` has similar flags with the same purpose as above:

* `--group`
* `--tag`

### Output verbosity/suppression

By default, `hydrate` provides a bare minimum amount of output. Only violations are output to the console. To increase
verbosity, use `-v`. Use `-vv` for the highest debug-level output.

To silence output to the bare-minimum, use the `--quiet` flag. Note that items like validation failures will not be
dumped to the console. You will still receive a hydration summary when complete.

### Hydration summary

When complete hydrating and validation, a summary is shown for all levels of verbosity. Each cluster with a failure is
listed with a summary of the steps that failed. The possible failed steps include jinja, kustomize, and gatekeeper.

Failed output example:

```
$ hydrate --quiet cluster --gatekeeper-validation source_of_truth.csv

Total 14 clusters - 12 rendered successfully, 2  unsuccessful

Cluster US76839CLS01 failed: gatekeeper
Cluster US22707CLS01 failed: gatekeeper
```

Success:

```shell
$ hydrate --quiet cluster --gatekeeper-validation --cluster-group prod-us source_of_truth.csv
5 clusters total, all rendered successfully
```

### Exit Codes

All failure scenarios exit with an exit code of `1` regardless of the number of failures. This includes failures to
validate. All successful runs exit `0`.

## Internal App Logical Workflow

The Hydrator CLI follows these steps to process your manifests:

1.  **Initialization**: The CLI is started with paths to the base library, overlays directory, and the source of truth CSV file.
2.  **Source of Truth Processing**: The CLI reads the source of truth CSV, processing one cluster (i.e., one row) at a time.
    1.  Each row contains a `cluster_group` field. This field's value directly corresponds to an overlay directory name within the `overlays/` directory (e.g., a `cluster_group` of `prod-us` means Hydrator will look for `overlays/prod-us/`).
    2.  This mapping tells the tool which overlay configuration to use for the current cluster.
3.  **Temporary Workspace**: For each cluster, a temporary working directory is created to isolate processing steps.
4.  **Jinja Configuration**: The cluster's configuration data (from its row in the source of truth CSV) is made available to the Jinja templating engine. This allows values from the CSV (like `cluster_name`, `cluster_group`, or any custom columns) to be used within Jinja templates (files ending in `.j2`).
5.  **File Collection**: Hydrator reads the contents of the cluster's specific overlay directory (e.g., `overlays/prod-us/`) and the entire `base_library/`. It keeps track of original file information, like source paths, and correctly handles relative and symbolic links.
6.  **Jinja Template Rendering**: Files ending with the `.j2` extension are identified as Jinja templates and are then rendered (i.e., processed by the Jinja engine using the data from step 4).
7.  **Writing to Temporary Directory**: The processed files are written to the cluster's temporary working directory.
    1.  For Jinja templates, the rendered output is saved with the original filename but without the `.j2` extension (e.g., `configmap.yaml.j2` becomes `configmap.yaml`). Other files are copied as-is.
8.  **Kustomize Build**: `kustomize build` is then run by Hydrator within the cluster's overlay directory (which now contains the Jinja-rendered files and other resources).
    1.  Hydrator executes this as a standard command ([subprocess](https://docs.python.org/3.12/library/asyncio-subprocess.html)).
    2.  Kustomize is directed to output the final, fully-hydrated Kubernetes resources (often a single YAML file or stream) to a designated location.
9.  **Output Organization**: Based on the output arguments provided to Hydrator, the fully-rendered Kubernetes resources from Kustomize are moved to a deterministic final output location:
    1.  The output structure can be `none` (e.g., `<output_dir>/<CLUSTER_NAME>.yaml`), `group` (e.g., `<output_dir>/<GROUP_NAME>/<CLUSTER_NAME>.yaml`), or `cluster` (e.g., `<output_dir>/<CLUSTER_NAME>/<CLUSTER_NAME>.yaml`).
10. **Splitting Output (Optional)**: If the `--split-output` flag is used, the resulting Kubernetes resources (from Kustomize) are parsed and split into individual manifest files. These files are organized based on resource type, name, and namespace within the cluster's output directory.
11. **Gatekeeper Validation (Optional)**: If the `--gatekeeper-validation` flag is provided, Hydrator runs `gator test` to validate the hydrated manifests against the specified Gatekeeper constraints.

## Suggested User Workflow

This app should be run from a template (dry) repository. The hydrated output of this app should be checked into a
hydrated repo from which config sync is syncing resources.

Using a pipeline:

1. Group changes into a single MR and run a pipeline when the MR is opened
    1. These may include SOT changes _and_ base and overlay changes
2. Pipeline runs the following steps:
    1. Run the CLI to rehydrate resources
    2. When complete, grab the output manifests
    3. Optionally perform manifest validation/checks
    4. Commit the output (hydrated) manifests

## Help

`hydration` is self-documented. Use the `--help` flag to see currently available options.

Note that functionality is split across subcommands:

* `hydrate package --help`
* `hydrate cluster --help`

```
$ hydrate --help
usage: hydrate [-h] [-v | -q] [--workers WORKERS] {cluster,package} ...

positional arguments:
  {cluster,package}
    cluster          hydrate cluster-specific resources
    package          hydrate resources for a package

options:
  -h, --help         show this help message and exit
  -v, --verbose      increase output verbosity; -vv for max verbosity
  -q, --quiet        output errors only
  --workers WORKERS
```

## Development

Consider the following which makes use of Python [virtualenvs](https://virtualenv.pypa.io/en/latest/). Python 3.12+
required.

```shell
cd resource-hydrate-cli

python3 -m venv venv

source venv/bin/activate

pip3 install -r requirements.txt --require-hashes

pip3 install -e .[dev]
```

### Tests

#### Static Analysis

Should pass pylint checks with the following:

```shell
pylint src
mypy src
```

#### Integration Tests

After installing the dev dependencies, you can run the integration test suite:

```shell
python3 -m unittest tests/*.py -vv
```

##### Async Testing and Performance

A set of async tests has been committed to `test/test_cluster_async.py` using test assets committed to
`tests/assets/platform_valid_async`. This directory contains a source of truth with 50 clusters. There is single test
case (`test_standard_args_sync_baseline`) which acts as a baseline to compare against asyncronous tests. As of the time
of this writing, asyncronous hydration using asyncio completes approximately 85% faster at ~4s compared to ~30s.

There is an additional set of tests assets at `tests/assets/platform_valid_async_perf_testing` which contain ~1000
clusters for evaluation and test of performance enhancements/improvements. Hydration of these resources completes in
approximately ~149s.

### Building Docker Container

_Note_: The provided `Dockerfile` installs the latest tested version of `kustomize` into the container image and may or
may not be desirable. Because the chosen upstream image is based on Alpine, only one version of `kustomize` is available
in its repositories. If you need another version, find your desired release id and pass it as a Docker build argument.
Build:

```shell
docker build -t hydrator --pull --no-cache .

# optionally, specify kustomize release version
#--build-arg KUSTOMIZE_VERSION=<RELEASE-VERSION>
```

Test image:

```shell
$ docker run -it hydrator --help

usage: hydrate ...
```

Run container against inputs from template (dry) repository:

```shell
# set paths to template (dry) and wet repos
$ DRY_REPO=/path/to/dry/repo
$ WET_REPO=/path/to/wet/repo

$ docker run -it \
  --user $(id -u):$(id -g) \
  -v ${DRY_REPO}/cluster-reg-template:/app/templates \
  -v ${WET_REPO}:/app/hydrated \
  hydrator \
  -b /app/templates/base_library \
  -o /app/templates/overlays \
  -y /app/hydrated/output \
  /app/templates/source_of_truth.csv
```
