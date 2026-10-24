# ADR-039 — v3 admission, operator manifest and runtime fencing (I-024)

2026-10-23. Local implementation decision. No remote dataplane rollout approval.

V3 remains disabled unless its separate operator manifest is explicitly configured.
The manifest fixes node UUID, slot number, UDP endpoint, server public key and
header-protection key. It never contains client private keys. No user/agent request
selects an executable, namespace, endpoint or routing program. V2 profiles cannot
share a node with the v3 manifest. Existing subscription-v2 gate remains in force.

Each process startup creates a fresh random runtime UUID, closes every fixed slot
and removes native peers before accepting commands. Root-private SQLite FULL/WAL
journal and a process-lifetime flock protect intents, receipts and connection fences.
Intent is committed before opening a kernel gate. An interrupted intent produces
unknown evidence after confirmed closure, never a fabricated measured zero.
The fixed `services/node-agent/window_daemon.py` connects this state machine to
operator inventory and authenticated control API. It requires separately prepared
root-owned native cores/namespaces/ready files; it does not provision them or alter
host routing. Importing the module does not enable VPN. No daemon is installed on
the remote nodes by I-024.

Runtime registration is authenticated using the existing node-scoped credential,
nonce and timestamp contract over protected HTTPS transport. A new epoch specifies
the expected previous runtime. Registration atomically compares that value with the
current DB runtime, and records the transition in immutable history. An already
seen epoch cannot become current again. Delayed ordinary polls cannot register an
epoch. Registration retries of the same complete transition are idempotent.
After an uncertain registration result, a fresh authenticated inventory request may
read the current epoch before proposing another CAS. No plaintext public agent API.

New runtime starts reconciling. Old windows lacking closure evidence are returned
as fixed recovery commands. With all its gates closed and before new admissions,
the new runtime may confirm unknown closure for an old window on the same node.
It must not invent a measured counter after loss of the journal/kernel table.
Unknown closure permits slot lifecycle progress while its original bounded funding
hold remains unresolved. These are separate states. Epoch becomes ready only after
old windows have closure evidence; measured receipts debit only their pinned lots.
Node-scoped authentication and full receipt binding are mandatory even for old epochs.

Admission authenticates the ordinary user/device-bound session, consumes a fresh
device-key proof, checks user/device/node/epoch/heartbeat and atomically claims a
unique node slot. The same User lock protects funding. Connection IDs and full
window grants are durable before dispatch. One current plus one reserved next
window is the bound. Kernel opening subtracts 6s (5s native operation plus 1s
permitted clock skew) from the absolute funding deadline; a retry never resets
the budget or timeout.

The node runtime has only the existing pinned Pydantic dependency closure; pure
wire validation/key validation/journal/runtime modules do not import SQLAlchemy,
FastAPI or backend settings. `window-requirements.txt` pins hashes for the
Linux x86_64 CPython 3.13 deployment target. These are existing dependencies:
MIT except typing-extensions (PSF-2.0), verified from installed distribution
metadata. No new library; backend lock upgrades must explicitly refresh/review that file.
Native AWG hashes/licensing remain governed by the existing engine provenance.

Runtime registration takes the existing node-slot advisory lock and never locks
users. User admission takes User then slot advisory lock, without taking a node
row lock. Ordinary agent polls do not retain a slot advisory lock while locking
users. User/connection operations follow the existing User-first order. This avoids
the node/admission inverse-lock cycle. No network/native call runs in a DB transaction.

Revocation is durable connection intent before node dispatch. New/renewal commands
are suppressed when user/device/funding is invalid, runtime changes or deadline is
missed. A signed closure receipt or explicit recovery closure is required before
slot reuse. HTTP success on a revoke request is not proof of packet cutoff.
Device/admin revocation must enqueue v3 intent in the same transaction as existing
v2 intent; source histories and quota holds are not deleted.

DB restore must fence/rotate agent credentials and sessions before reconnection;
CAS history from an older snapshot alone cannot reject every pre-restore message.
Node startup cannot restore old grants into a fresh epoch. Actual full restore and
credential fencing drill remains I-027/I-028. Provider independence, WAN/Android
acceptance, secret relay and daily-service readiness are not established by this ADR.
