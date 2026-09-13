# PLA v1.2 Threat Model

## Scope

This document defines the security assumptions and residual risks for PLA v1.2.
PLA is a policy-constrained local capability runtime for a ChatGPT-native agent
loop. It is not an operating-system sandbox and does not attempt to make arbitrary
third-party code safe.

The production decision boundary remains:

```text
ChatGPT = reasoning / planning / capability selection
PLA     = policy / execution / state / audit / recovery
```

MCP Sampling remains outside the production mainline. The real ChatGPT client does
not currently expose sampling to PLA, and v1.2 does not rely on server-side model
execution for correctness.

## Assets

PLA protects or governs these assets:

1. local files inside configured roots;
2. local processes and reviewed program execution;
3. durable Task and Action Transaction state;
4. immutable Artifact Plane snapshots and provenance;
5. provider manifests, isolated provider environments, and capability catalogs;
6. Event, Observer, Gate, Elevation, and Lifecycle runtime state;
7. credentials and Secure MCP Tunnel configuration;
8. Git repository state and controlled release operations.

## Trust boundaries

### ChatGPT to PLA

ChatGPT reaches PLA through the Secure MCP Tunnel and the stable public MCP
surface. PLA does not assume that model-generated arguments are trustworthy.
Every capability invocation must still pass availability, confirmation,
transaction, artifact-contract, JSON-schema, and Gate policy checks.

### Stable Capability Broker boundary

Provider-specific tools are hidden behind:

```text
capability_search
capability_describe
capability_invoke
```

The broker is the main local execution-policy boundary. New provider tools do not
become usable merely because a downstream MCP server exposes them.

### Provider process boundary

Reviewed providers execute as child processes. Python providers use dedicated
`.provider_envs/<id>/` environments; reviewed native executables use explicit
manifest declarations. Provider processes still inherit host-user authority.

Provider isolation is dependency/process isolation, not an OS sandbox.

### External Observer boundary

External Observers receive only already-persisted EventEnvelope facts and execute
through bounded `python -m <module>` stdio. They cannot change Capability
arguments, results, or Gate decisions through the Observer API.

They still execute with host-user authority. A malicious Observer package could
perform unrelated side effects outside the Observer protocol. For this reason
v1.2 does not support external Gate plugins or arbitrary observer executables.

### Elevation boundary

The normal PLA HTTP runtime is not intended to run elevated. Windows UAC work is
delegated to the Interactive Elevation Broker, which accepts only reviewed request
shapes and does not expose arbitrary `runas`.

### Lifecycle boundary

HTTP self-restart is delegated to an independent Runtime Lifecycle Broker.
Restart requests cannot choose an executable, script, port, command, or target
PID. The broker always invokes the repository-owned restart script with the exact
current HTTP PID, and that script independently verifies listener ownership.

### Durable-state boundary

Task, Transaction, Event, Hook, Gate, Elevation, and Lifecycle state live under
protected runtime paths outside the user workspace. SQLite stores use explicit
locking and durable journaling where applicable.

A process running as the same Windows user may still be able to tamper with local
state outside PLA. PLA does not claim protection against a fully compromised
host-user account.

## Threats and controls

### Prompt-driven overreach

Threat: a model or prompt attempts an operation broader than the user's intended
local action.

Controls:

- named roots and protected paths;
- program and provider allowlists;
- explicit Capability schemas;
- confirmation requirements;
- transaction requirements;
- Gate Hooks before execution;
- exact-path Git and Artifact controls.

Residual risk: policy quality depends on reviewed Capability definitions.

### Provider tool injection or catalog drift

Threat: an upgraded or compromised downstream MCP provider exposes additional
tools or changes its catalog.

Controls:

- manifest tool allowlists;
- per-tool policy overrides;
- isolated environments;
- exact dependency pins where available;
- Provider Doctor drift checks;
- changed providers are hidden before rediscovery.

Residual risk: a compromised implementation of an already-allowlisted tool still
runs with that provider's authority.

### Confused transaction attribution

Threat: an invocation occurs in a Transaction but audit facts cannot prove which
Transaction owned it, or an ordinary caller forges Transaction identity.

Controls:

- Transaction Envelope is the only production adapter that supplies
  `transaction_context=True` with an internal transaction id;
- ordinary public `capability_invoke` does not accept transaction ids;
- EventEnvelope records the real transaction id for transaction-bound target
  invocations;
- EventStore supports transaction-id filtering;
- Action Transaction Store remains the recovery source of truth.

Residual risk: Event Plane is audit/observability, not the transaction state
machine itself.

### Sensitive data leakage through audit

Threat: Event or Hook persistence stores raw arguments, provider results, or
exception content.

Controls:

- Event Plane stores hashes and bounded structural metadata;
- Hook/Gate failure records hash exception text;
- raw Capability arguments/results are not persisted in EventStore;
- control-plane query capabilities exclude themselves from recursive audit.

Residual risk: non-secret metadata such as capability ids, provider ids, timing,
and argument-key names is intentionally observable.

### Observability failure

Threat: logging or Observer failure breaks useful local work.

Control: Event and Observer execution are fail-open relative to Capability
semantics.

Tradeoff: audit facts may be missing during an observability failure. This is
intentional so Event Plane availability does not become an execution dependency.

### Policy Gate failure

Threat: Gate infrastructure fails and silently permits an operation.

Control: Gate exceptions, malformed results, and Gate-decision persistence
failures are fail-closed under deny-overrides semantics.

Residual risk: v1.2 registers no default production policy Gate; Gate protection
exists only when a reviewed Gate is explicitly registered.

### Lifecycle confused-deputy or arbitrary process control

Threat: self-restart becomes a generic process-management primitive.

Controls:

- fixed restart request kind;
- exact current HTTP PID captured by the server;
- bounded request age and delayed execution;
- independent broker with a named mutex;
- repository-owned restart script only;
- listener/process-command-line ownership checks;
- no arbitrary PID/executable/argv/port fields;
- restart status is durable and explicitly verified after reconnect.

### Elevation abuse

Threat: PLA turns UAC mediation into arbitrary administrator execution.

Controls:

- separate interactive broker;
- reviewed request types only;
- exact executable/argument reconstruction;
- provider-specific post-action verification;
- no generic public `runas`.

### Recursive automation / loss of ChatGPT control flow

Threat: Event, Hook, or plugin execution recursively invokes more Capabilities and
turns PLA into an autonomous local planner.

Controls:

- Observer and Gate APIs cannot invoke PLA Capabilities;
- no event-to-capability trigger engine;
- control capabilities suppress self-recursion;
- ChatGPT remains the only production planner.

## Transport and authorization assumption

PLA's local HTTP server is intentionally bound to loopback and remote access is
provided through the Secure MCP Tunnel. Tunnel credentials and ingress controls
are deployment prerequisites rather than reimplemented inside the Capability
Broker.

PLA v1.2 does not claim to replace MCP transport authorization. If the deployment
model changes from the current reviewed tunnel to a general remote MCP endpoint,
authorization must be reassessed at the HTTP/transport boundary before release.

## Release security rule

A change requires a new security review if it introduces any of the following:

- arbitrary shell or executable selection;
- externally supplied process ids or privileged commands;
- external Gate plugins;
- plugin-triggered Capability execution;
- provider installation without reviewed manifests;
- raw argument/result persistence in global audit state;
- weakening confirmation or transaction requirements;
- new network ingress or a different tunnel/authentication model;
- execution under a more privileged Windows identity.

Such changes are not ordinary patch-level implementation details.
