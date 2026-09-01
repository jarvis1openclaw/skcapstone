#!/usr/bin/env bash
set -u

read -ra hosts <<<"${SKFLEET_DISTRIBUTION_HOSTS:-chiap01 chiap02 chiap03 chiap04 chiap08}"
local_host=${SKFLEET_LOCAL_HOST:-chiap08}
state_dir="${SKFLEET_STATE_DIR:-$HOME/.skcapstone/fleet}"
log_dir="${SKFLEET_LOG_DIR:-$HOME/.skcapstone/evidence/fleet-distribution-watch}"
state_file="$state_dir/distribution-watch.state"
mkdir -p "$state_dir" "$log_dir"

declare -A candidate_ids=()
candidate_manifests_missing=0

reset_candidate_inventory() {
  candidate_ids=()
  candidate_manifests_missing=0
}

record_candidate_pool() {
  local expected_host=$1 pool=$2 manifest pool_host record_host cid ready
  local -a ids=()
  if [[ ! "$pool" =~ ^POOL\|([^|[:space:]]+)\|ready=([0-9]+)([[:space:]]|$) ]]; then
    candidate_manifests_missing=$((candidate_manifests_missing + 1))
    return
  fi
  pool_host=${BASH_REMATCH[1]}
  ready=${BASH_REMATCH[2]}
  if [[ ! "$pool" =~ (^|[[:space:]])POOL_IDS\|([^|[:space:]]+)\|ids=([^[:space:]]+)$ ]]; then
    candidate_manifests_missing=$((candidate_manifests_missing + 1))
    return
  fi
  record_host=${BASH_REMATCH[2]}
  manifest=${BASH_REMATCH[3]}
  if [[ "$pool_host" != "$expected_host" || "$record_host" != "$expected_host" ]]; then
    candidate_manifests_missing=$((candidate_manifests_missing + 1))
    return
  fi
  if [[ "$manifest" == - ]]; then
    if (( 10#$ready != 0 )); then
      candidate_manifests_missing=$((candidate_manifests_missing + 1))
    fi
    return
  fi
  if [[ ! "$manifest" =~ ^[0-9a-f]{8}(,[0-9a-f]{8})*$ ]]; then
    candidate_manifests_missing=$((candidate_manifests_missing + 1))
    return
  fi
  IFS=',' read -ra ids <<<"$manifest"
  if (( ${#ids[@]} != 10#$ready )); then
    candidate_manifests_missing=$((candidate_manifests_missing + 1))
    return
  fi
  for cid in "${ids[@]}"; do
    candidate_ids["$cid"]=1
  done
}

lane_from_session() {
  case "$1" in
    codex-auto-*) printf 'codex\n' ;;
    glm-auto-*) printf 'glm\n' ;;
    qwen-auto-*) printf 'qwen\n' ;;
    esc-auto-*) printf 'escalate\n' ;;
    *) return 1 ;;
  esac
}

has_live_pi_child() {
  local pane_pid=$1 child comm
  while read -r child; do
    [[ -n "$child" ]] || continue
    comm=$(ps -o comm= -p "$child" 2>/dev/null | xargs)
    [[ "$comm" == pi ]] && return 0
  done < <(pgrep -P "$pane_pid" 2>/dev/null || true)
  return 1
}

probe_host() {
  local host=$1 panes error_file tmux_state=ok result pool pool_ids
  local session pane_pid command lane card state
  error_file=$(mktemp)
  if panes=$(tmux list-panes -a -F '#{session_name}|#{pane_pid}|#{pane_current_command}' 2>"$error_file"); then
    tmux_state=ok
  elif grep -Eqi 'no server running|no sessions|failed to connect.*No such file' "$error_file"; then
    tmux_state=empty
    panes=
  else
    tmux_state=unavailable
    panes=
  fi
  rm -f "$error_file"

  result=$(systemctl --user show skfleet-rotate.service -p Result --value 2>/dev/null || true)
  pool=$(journalctl --user -u skfleet-rotate.service -n 60 --no-pager -o cat 2>/dev/null | grep 'POOL|' | tail -1)
  pool_ids=$(journalctl --user -u skfleet-rotate.service -n 60 --no-pager -o cat 2>/dev/null | grep 'POOL_IDS|' | tail -1)
  pool=${pool#"${pool%%[![:space:]]*}"}
  pool_ids=${pool_ids#"${pool_ids%%[![:space:]]*}"}
  printf 'META\t%s\t%s\t%s\n' "$host" "$tmux_state" "${result:-unavailable}"
  printf 'POOL\t%s\t%s %s\n' "$host" "$pool" "$pool_ids"

  while IFS='|' read -r session pane_pid command; do
    lane=$(lane_from_session "$session") || continue
    card=${session##*-}
    [[ "$card" =~ ^[0-9a-f]{8}$ ]] || continue
    state=live
    if [[ "$command" == bash || "$command" == sh ]]; then
      state=shell_only
      has_live_pi_child "$pane_pid" && state=live
    fi
    printf 'SESSION\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$host" "$session" "$card" "$lane" "$state" "$command"
  done <<<"$panes"
}

host_probe_source() {
  declare -f lane_from_session has_live_pi_child probe_host
  printf 'probe_host "$1"\n'
}

collect_claims() {
  python3 -c '
import os, re
from pathlib import Path
from skcoord.card_store import CardStore

home = Path(os.environ.get("SKCAPSTONE_HOME", "~/.skcapstone")).expanduser()
for card in CardStore(home).list_cards(include_archived=False):
    owner = card.owner or ""
    revision = card.meta.get("_claim_revision")
    status = getattr(card.status, "value", str(card.status))
    if not re.match(r"^pi-(?:codex|glm|qwen|esc|escalation)-", owner):
        continue
    if not isinstance(revision, str) or not revision:
        continue
    if status != "doing":
        continue
    print("CLAIM\t%s\t%s\t%s\t%s" % (card.id, owner, status, revision))
' 2>/dev/null
}

collect_gateway_activity() {
  if [[ -n "${SKFLEET_GATEWAY_ACTIVITY_FILE:-}" ]]; then
    cat "$SKFLEET_GATEWAY_ACTIVITY_FILE"
    return
  fi
  ssh -o BatchMode=yes -o ConnectTimeout=5 chiap01 python3 - 2>/dev/null <<'PY'
import os, re, sqlite3, time
path = os.path.expanduser("~/skgateway-codex/data/metrics.db")
db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
cutoff = int(time.time() * 1000) - 10 * 60 * 1000
rows = db.execute(
    "select agent_id, count(*) from request_log "
    "where started_at > ? and agent_id is not null group by agent_id",
    (cutoff,),
)
unattributed = db.execute(
    "select count(*) from request_log where started_at > ? and agent_id is null",
    (cutoff,),
).fetchone()[0]
for agent_id, count in rows:
    match = re.search(r"([0-9a-f]{8})$", agent_id or "")
    if match and re.match(r"^pi-(?:codex|glm|qwen|esc|escalation)-", agent_id):
        print("GATEWAY\t%s\t%s\t%s" % (match.group(1), agent_id, count))
    else:
        unattributed += count
print("GATEWAY_UNATTRIBUTED\t%s" % unattributed)
PY
}

parse_queue() {
  python3 -c '
import json, sys
data = json.load(sys.stdin)
pool = data.get("pool", data)
print("%s\t%s" % (pool["totalActive"], pool["totalQueued"]))
'
}

join_csv() {
  local value=${1:-}
  [[ -n "$value" ]] && printf '%s' "$value" || printf '-'
}

notify_transition() {
  local priority=$1 subject=$2 body=$3 recipient
  for recipient in lumina jarvis; do
    skmail send jarvis "$recipient" "$priority" "$subject" "$body" >/dev/null || true
  done
}

sample() {
  local host out kind result pool line card
  local total_workers=0 unavailable=0 tmux_fault=0 details=""
  local codex_workers=0 glm_workers=0 qwen_workers=0 escalate_workers=0
  local claims_fault=0 gateway_fault=0 queue_fault=0 gateway_unattributed=0
  local queue queue_active=unavailable queue_queued=unavailable now current previous total_ready
  local unmatched_sessions="" unmatched_claims="" unmatched_gateway=""
  declare -A sessions=() claims=() gateway=()
  reset_candidate_inventory

  for host in "${hosts[@]}"; do
    if [[ "$host" == "$local_host" ]]; then
      out=$(probe_host "$host")
    else
      out=$(host_probe_source | ssh -o BatchMode=yes -o ConnectTimeout=5 "$host" bash -s -- "$host" 2>/dev/null) || out=$'META\t'"$host"$'\tunreachable\tunavailable'
    fi
    while IFS=$'\t' read -r kind line; do
      case "$kind" in
        META)
          IFS=$'\t' read -r host result pool <<<"$line"
          details+="$host:tmux=$result,result=$pool;"
          [[ "$result" == unavailable ]] && tmux_fault=$((tmux_fault + 1))
          if [[ "$result" == unreachable || "$pool" != success ]]; then
            unavailable=$((unavailable + 1))
          fi
          ;;
        POOL)
          IFS=$'\t' read -r host pool <<<"$line"
          if [[ "$pool" =~ ready=[0-9]+ ]]; then
            record_candidate_pool "$host" "$pool"
          else
            candidate_manifests_missing=$((candidate_manifests_missing + 1))
          fi
          ;;
        SESSION)
          local session lane session_state command
          IFS=$'\t' read -r host session card lane session_state command <<<"$line"
          if [[ "$session_state" == live ]]; then
            sessions["$card"]="$host/$session"
            total_workers=$((total_workers + 1))
            case "$lane" in
              codex) codex_workers=$((codex_workers + 1)) ;;
              glm) glm_workers=$((glm_workers + 1)) ;;
              qwen) qwen_workers=$((qwen_workers + 1)) ;;
              escalate) escalate_workers=$((escalate_workers + 1)) ;;
            esac
          else
            details+="$host/$session=$session_state;"
          fi
          ;;
      esac
    done <<<"$out"
  done

  if out=$(collect_claims); then
    while IFS=$'\t' read -r kind line; do
      [[ "$kind" == CLAIM ]] || continue
      local owner status revision
      IFS=$'\t' read -r card owner status revision <<<"$line"
      claims["$card"]="$owner/$status/$revision"
    done <<<"$out"
  else
    claims_fault=1
  fi

  if out=$(collect_gateway_activity); then
    while IFS=$'\t' read -r kind line; do
      case "$kind" in
        GATEWAY)
          local agent count
          IFS=$'\t' read -r card agent count <<<"$line"
          gateway["$card"]="$agent/$count"
          ;;
        GATEWAY_UNATTRIBUTED) gateway_unattributed=$line ;;
      esac
    done <<<"$out"
  else
    gateway_fault=1
  fi

  queue=$(curl -fsS --max-time 8 http://chiap01:18790/queue 2>/dev/null || true)
  if out=$(parse_queue <<<"$queue" 2>/dev/null); then
    IFS=$'\t' read -r queue_active queue_queued <<<"$out"
  else
    queue_fault=1
  fi

  for card in "${!sessions[@]}"; do
    [[ -n "${claims[$card]:-}" ]] || unmatched_sessions+="${card}@${sessions[$card]},"
  done
  for card in "${!claims[@]}"; do
    [[ -n "${sessions[$card]:-}" ]] || unmatched_claims+="${card}@${claims[$card]},"
  done
  for card in "${!gateway[@]}"; do
    if [[ -z "${sessions[$card]:-}" || -z "${claims[$card]:-}" ]]; then
      unmatched_gateway+="${card}@${gateway[$card]},"
    fi
  done
  unmatched_sessions=${unmatched_sessions%,}
  unmatched_claims=${unmatched_claims%,}
  unmatched_gateway=${unmatched_gateway%,}

  total_ready=${#candidate_ids[@]}
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  current=up
  if (( unavailable >= ${#hosts[@]} )); then
    current=unavailable
  elif (( tmux_fault || claims_fault || gateway_fault || queue_fault )) \
    || [[ -n "$unmatched_sessions" || -n "$unmatched_claims" || -n "$unmatched_gateway" ]] \
    || { (( total_workers == 0 )) && [[ "$queue_active" =~ ^[0-9]+$ ]] && (( queue_active > 0 )); }; then
    current=collector_fault
  elif (( total_workers == 0 )); then
    current=zero
  fi
  previous=$(cat "$state_file" 2>/dev/null || true)

  printf '%s|state=%s|workers=%d|codex=%d|glm=%d|qwen=%d|escalate=%d|workable=%d|candidate_inventory_missing_hosts=%d|unavailable_hosts=%d|queue_active=%s|queue_queued=%s|gateway_unattributed=%s|unmatched_sessions=%s|unmatched_claims=%s|unmatched_gateway=%s|%s\n' \
    "$now" "$current" "$total_workers" "$codex_workers" "$glm_workers" "$qwen_workers" "$escalate_workers" \
    "$total_ready" "$candidate_manifests_missing" "$unavailable" "$queue_active" "$queue_queued" "$gateway_unattributed" \
    "$(join_csv "$unmatched_sessions")" "$(join_csv "$unmatched_claims")" "$(join_csv "$unmatched_gateway")" "$details" >>"$log_dir/watch.log"

  if [[ "$current" != "$previous" ]]; then
    local body
    body="Fleet watcher transition at $now: state=$current workers=$total_workers lanes=codex:$codex_workers,glm:$glm_workers,qwen:$qwen_workers,escalate:$escalate_workers unique_workable_cards=$total_ready candidate_inventory_missing_hosts=$candidate_manifests_missing unavailable_hosts=$unavailable queue_active=$queue_active queue_queued=$queue_queued unmatched_sessions=$(join_csv "$unmatched_sessions") unmatched_claims=$(join_csv "$unmatched_claims") unmatched_gateway=$(join_csv "$unmatched_gateway"). Gateway queue is request activity only and was not counted as workers. Details: $details."
    case "$current" in
      zero|unavailable) notify_transition urgent FLEET-DISTRIBUTION-DOWN "$body" ;;
      collector_fault) notify_transition urgent FLEET-DISTRIBUTION-COLLECTOR-FAULT "$body" ;;
      *) [[ -n "$previous" ]] && notify_transition normal FLEET-DISTRIBUTION-RECOVERED "$body" ;;
    esac
    printf '%s\n' "$current" >"$state_file"
  fi
  printf 'state=%s workers=%d codex=%d glm=%d qwen=%d escalate=%d workable=%d candidate_inventory_missing_hosts=%d unavailable_hosts=%d queue_active=%s queue_queued=%s unmatched_sessions=%s unmatched_claims=%s unmatched_gateway=%s\n' \
    "$current" "$total_workers" "$codex_workers" "$glm_workers" "$qwen_workers" "$escalate_workers" \
    "$total_ready" "$candidate_manifests_missing" "$unavailable" "$queue_active" "$queue_queued" \
    "$(join_csv "$unmatched_sessions")" "$(join_csv "$unmatched_claims")" "$(join_csv "$unmatched_gateway")"
}

if [[ "${SKFLEET_DISTRIBUTION_WATCH_LIB_ONLY:-0}" == 1 ]]; then
  return 0 2>/dev/null || exit 0
fi

if [[ "${1:-}" == --once ]]; then
  sample
  exit
fi

while true; do
  sample
  sleep 300
done
