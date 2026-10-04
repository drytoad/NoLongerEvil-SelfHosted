# Gen-2 transport v3 compatibility

The source change preserves the confirmed nested inbound PUT parser and
implements the legacy outbound contract.

## Control path

Home Assistant discovery publishes temperature command topics. MQTT messages
reach `MqttIntegration._handle_ha_command()` and `execute_command()`.
The NLE web UI reaches the same function through POST `/command`.
`set_temperature()` updates `shared.SERIAL`, sets `target_change_pending`, and
increments revision/timestamp. State is persisted and subscribers are notified.
The thermostat receives the shared bucket on its transport subscription and
acknowledges the change through a device PUT. A queued notification alone does
not establish receipt; a server-side target alone is optimistic state.

The HA add-on Dockerfile installs SelfHosted from `nolongerevil/server`.
The compatibility implementation belongs in SelfHosted. Building the add-on
requires a submodule checkout containing it.
No HA wrapper version or submodule pointer was changed.

## Verified legacy response contract

The actual Display-2.8 / 4.0.6 `nlclient` binary was inspected read-only.
Its nested update parser is at 0x5c5b8; metadata extraction at 0x5c470 reads
`$version` and `$timestamp`; 0x5c35c strips dollar fields before applying values.
The hand-written nested scanner does not skip separator whitespace.

* v3 device GET: compact nested bucket/identifier/value JSON, including metadata.
* v3 PUT acknowledgement: compact nested bucket metadata only, without value echoes.
* v3 subscribe request: `keys` descriptors containing `key`, `version`, `timestamp`.
* v3 subscribe response: one bucket's plain value JSON, identified by
  `X-nl-skv-key`, `X-nl-skv-version`, and `X-nl-skv-timestamp` HTTP headers.
  The client header parser and single-bucket application functions are at
  0x570b8 and 0x56eb0/0x56e4c. Empty responses act as service tickles.
* Newer transports: existing `objects` descriptors and streaming behavior remain.

Version selection uses the explicit `/transport/v3/` path, not hardware identity.
PUT acknowledgements do not piggyback commands or notify subscriptions.
Legacy polling compares timestamps and revisions, returns one changed requested
bucket, and leaves other bucket changes available for the next poll. Disconnected
polls and timed-out subscriptions are removed.

## Evidence and verification

Original captures showed repeated successful inbound PUTs, optimistic commands,
and no subscription delivery. After deploying the compact bootstrap and metadata
responses, both test thermostats established v3 subscriptions. A client
log showed a successful BigGet, accepted PUT response, and an immediate subscribed
bucket response, followed by normal sleep instead of continuous repeated PUTs.

A temporary setpoint increase was delivered in a shared SKV response.
The device cleared `target_change_pending` in a subsequent delta PUT within one
second. Restoring the original target produced the same acknowledgement.
After the final deployment both test devices passed command-and-restore
validation. Each step produced a device-originated PUT clearing the pending
flag. The delta omitted
the target value itself, so this is device protocol acknowledgement, not an
independent reading of the thermostat display. Read-only SSH attempts afterward
were limited by sleeping-device connection timeouts.

Tests cover modern array/keyed PUT, legacy nested PUT, v3 metadata-only ACK and
compact serialization, v3/v7 GET bootstrap, legacy key descriptors, real HTTP
legacy polling and modern chunked target delivery, and focused diagnostic logs.
All 233 tests pass; changed Python files pass Ruff lint and formatting checks.

Recreating or updating the add-on requires rebuilding with the patched source.
Physical display verification and a separate MQTT command test should be recorded
separately from the confirmed server and device protocol checks.
