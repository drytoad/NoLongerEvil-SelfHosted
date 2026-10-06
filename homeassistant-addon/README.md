# NoLongerEvil Gen-2 Home Assistant add-on

This add-on builds the Gen-2 compatibility fixes into its Docker image.
The Dockerfile pins the server revision so rebuilding cannot silently switch
back to an incompatible upstream release.

Add `https://github.com/drytoad/NoLongerEvil-SelfHosted` to the Home Assistant
add-on store repositories, then install NoLongerEvil Gen-2. Set `api_origin`
to the LAN-facing server address and port. Thermostats use that address with
`/entry` appended. The default device-facing port is 9543.

When migrating an existing installation, stop the old add-on, copy its SQLite
database into the new add-on's persistent data directory, and apply its options
through Supervisor. Keep the original installation stopped with automatic
startup and updates disabled until the replacement is verified. Do not run
both installations on the same port or with the same MQTT device topics.

The local installation retains its source under `/addons/nolongerevil` and
its database under Supervisor-managed persistent storage. Rebuilding a local
installation uses those source files. Container recreation uses the installed
image; it does not require a package hotpatch.
