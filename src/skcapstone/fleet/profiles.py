"""Profile kind model: role to install profile (epic 3bbf39ea, card de9cf1d0).

The fleet already knows how to schedule work onto nodes. What it never knew
is what a node of a given role is *supposed to have installed*: which
packages, which user-scope units may be enabled, which Syncthing folders
it joins, and which capauth identity class it holds. That is this module.

Two axes stay ORTHOGONAL and neither is derived from the other:

  service role  what runs here (control, builder-standby, worker-gpu, observer)
  state tier    how much sovereign state lives here (full-replica,
                control-bus, none)

Conflating them is exactly how a GPU worker ended up carrying agent memories,
sessions and stale source checkouts it never needed. A role is expressed by
the profile object's *name*; the tier is an explicit field on its spec.

The spec side only: this module is pure, and reads nothing from the host.
It runs no commands, opens no files and asks systemd nothing. Observation
lives in nodeinventory.py and the drift diff in profile_doctor.py, which is
what keeps a validator that can veto a node free of the machine it judges.

A spec that fails validation must never reach an actuation verb; callers
treat ProfileSpecError as "do not touch this node" (degrade-safe, the same
contract as services.ServiceSpecError).
"""

from __future__ import annotations

#: How much sovereign state a node of this profile carries. Independent of
#: the service role: a builder-standby holds a full replica while running
#: almost nothing, and a worker runs a lot while holding nothing.
STATE_TIERS = frozenset({"full-replica", "control-bus", "none"})

#: The capauth identity class the node's credential belongs to. `operator`
#: is the human/AI ops seat, `agent` a sovereign agent identity, `worker` a
#: least-privilege node credential, `observer` read-only.
IDENTITY_CLASSES = frozenset({"operator", "agent", "worker", "observer"})

#: The three name lists every package/unit set carries.
_NAME_LIST_FIELDS = ("required", "allowed", "mustNot")


class ProfileSpecError(ValueError):
    """A Profile spec is malformed and must not be converged against."""


def _name_lists(field: str, raw: object) -> dict:
    """Validate one {required, allowed, mustNot} block of names.

    Args:
        field: The owning spec field name, used in error messages.
        raw: The candidate block; None and {} both mean "three empty lists".

    Returns:
        A dict with required/allowed/mustNot as sorted, de-duplicated lists.

    Raises:
        ProfileSpecError: The block is not a dict, carries an unknown key,
            holds anything but non-empty strings, lists the same name in
            both allowed and mustNot, or requires a name it does not allow.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ProfileSpecError(f"spec.{field} must be a dict of name lists, got {raw!r}")
    unknown = sorted(set(raw) - set(_NAME_LIST_FIELDS))
    if unknown:
        raise ProfileSpecError(
            f"spec.{field} has unknown keys {unknown} (known: {list(_NAME_LIST_FIELDS)})"
        )
    out: dict[str, list[str]] = {}
    for key in _NAME_LIST_FIELDS:
        values = raw.get(key, [])
        if not isinstance(values, list):
            raise ProfileSpecError(f"spec.{field}.{key} must be a list, got {values!r}")
        for name in values:
            if not isinstance(name, str) or not name.strip():
                raise ProfileSpecError(
                    f"spec.{field}.{key} entries must be non-empty names, got {name!r}"
                )
        out[key] = sorted({name.strip() for name in values})

    # A name that is both allowed and forbidden makes the drift report
    # non-deterministic: converge could justify either verdict.
    contradictory = sorted(set(out["allowed"]) & set(out["mustNot"]))
    if contradictory:
        raise ProfileSpecError(
            f"spec.{field}: {contradictory} appear in both 'allowed' and 'mustNot'; "
            "a name cannot be permitted and forbidden at once"
        )

    # Requiring what you do not allow is the same contradiction one step out.
    # Not auto-widened: the manifest must say what it means.
    unallowed = sorted(set(out["required"]) - set(out["allowed"]))
    if unallowed:
        raise ProfileSpecError(
            f"spec.{field}: {unallowed} are in 'required' but not in 'allowed'; "
            "list every required name in 'allowed' too"
        )
    return out


def _str_list(field: str, raw: object) -> list[str]:
    """Validate a flat list of non-empty strings, sorted and de-duplicated."""
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ProfileSpecError(f"spec.{field} must be a list, got {raw!r}")
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise ProfileSpecError(
                f"spec.{field} entries must be non-empty strings, got {value!r}"
            )
    return sorted({value.strip() for value in raw})


def normalize_profile_spec(spec: dict) -> dict:
    """Return a full Profile spec with defaults applied, or raise.

    Defaults are deliberately conservative: empty name lists mean "this
    profile asserts nothing", which the drift report renders as no findings
    rather than as a fleet-wide uninstall. The two fields that carry real
    consequence, stateTier and capauthIdentityClass, have NO default: a
    profile that does not say how much state it holds or what credential it
    carries is a profile nobody should converge against.

    Args:
        spec: Raw Profile spec dict.

    Returns:
        Normalized dict with description, packages, units, unitsIgnore,
        stateTier, capauthIdentityClass, syncFolders and deleted.

    Raises:
        ProfileSpecError: spec is not a dict, stateTier or
            capauthIdentityClass is missing or unknown, description is not a
            string, or any name list fails validation.
    """
    if not isinstance(spec, dict):
        raise ProfileSpecError(f"profile spec must be a dict, got {spec!r}")

    state_tier = spec.get("stateTier")
    if state_tier is None:
        raise ProfileSpecError(
            "spec.stateTier is required (one of "
            f"{sorted(STATE_TIERS)}); it is orthogonal to the service role "
            "and must be stated, never inferred"
        )
    if state_tier not in STATE_TIERS:
        raise ProfileSpecError(
            f"unknown stateTier {state_tier!r} (known: {sorted(STATE_TIERS)})"
        )

    identity_class = spec.get("capauthIdentityClass")
    if identity_class is None:
        raise ProfileSpecError(
            "spec.capauthIdentityClass is required (one of "
            f"{sorted(IDENTITY_CLASSES)})"
        )
    if identity_class not in IDENTITY_CLASSES:
        raise ProfileSpecError(
            f"unknown capauthIdentityClass {identity_class!r} "
            f"(known: {sorted(IDENTITY_CLASSES)})"
        )

    description = spec.get("description", "")
    if not isinstance(description, str):
        raise ProfileSpecError(f"spec.description must be a string, got {description!r}")

    return {
        "description": description,
        "packages": _name_lists("packages", spec.get("packages")),
        "units": _name_lists("units", spec.get("units")),
        # fnmatch patterns for units this profile takes no position on, so a
        # desktop box full of gpg-agent sockets does not read as drift.
        "unitsIgnore": _str_list("unitsIgnore", spec.get("unitsIgnore")),
        "stateTier": state_tier,
        "capauthIdentityClass": identity_class,
        "syncFolders": _str_list("syncFolders", spec.get("syncFolders")),
        "deleted": bool(spec.get("deleted", False)),
    }
