#!/usr/bin/env python3
"""
Audit partition 7 of 33 for card 799de053.
Selects cards where int(id, 16) modulo 33 equals 7.
Excludes cards labelled fleet-unblock-audit.
"""

import json
import os
import hashlib
import glob
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Set, Tuple

CARDSTORE_PATH = Path("/home/skuser01/.skcapstone/cards/")
COORDINATION_PATH = Path("/home/skuser01/.skcapstone/coordination/tasks/")
PARTITION_MODULUS = 33
PARTITION_TARGET = 7

def get_all_card_ids() -> List[str]:
    """Get all hexadecimal card IDs from CardStore."""
    card_dirs = [d for d in CARDSTORE_PATH.iterdir() if d.is_dir()]
    card_ids = [d.name for d in card_dirs if all(c in "0123456789abcdef" for c in d.name)]
    return sorted(card_ids)

def is_in_partition(card_id: str) -> bool:
    """Check if card belongs to partition 7 of 33."""
    try:
        numeric = int(card_id, 16)
        return numeric % PARTITION_MODULUS == PARTITION_TARGET
    except ValueError:
        return False

def has_fleet_unblock_audit_tag(card_id: str) -> bool:
    """Check if card has fleet-unblock-audit tag."""
    task_file = COORDINATION_PATH / f"{card_id}-*.json"
    matching_files = list(glob.glob(str(task_file)))
    for f in matching_files:
        try:
            with open(f, 'r') as fh:
                data = json.load(fh)
                tags = data.get('tags', [])
                if 'fleet-unblock-audit' in tags:
                    return True
        except Exception:
            continue
    return False

def read_card_events(card_id: str) -> List[Dict[str, Any]]:
    """Read all event files for a card."""
    events_dir = CARDSTORE_PATH / card_id / "events"
    events = []
    if not events_dir.exists():
        return events

    for event_file in sorted(events_dir.glob("*.jsonl")):
        try:
            with open(event_file, 'r') as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except Exception as e:
            continue
    return events

def get_card_lifecycle(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract lifecycle information from events."""
    lifecycle = {
        'created_at': None,
        'created_by': None,
        'state': 'unknown',
        'state_transitions': [],
        'last_updated': None
    }

    for event in events:
        if 'created_at' in event and not lifecycle['created_at']:
            lifecycle['created_at'] = event['created_at']
        if 'created_by' in event and not lifecycle['created_by']:
            lifecycle['created_by'] = event['created_by']
        if 'state' in event:
            lifecycle['state_transitions'].append({
                'state': event['state'],
                'timestamp': event.get('timestamp', event.get('created_at')),
                'by': event.get('agent', event.get('created_by', 'unknown'))
            })
            lifecycle['state'] = event['state']
        if 'timestamp' in event:
            ts = event['timestamp']
            if not lifecycle['last_updated'] or ts > lifecycle['last_updated']:
                lifecycle['last_updated'] = ts

    return lifecycle

def get_card_dependencies(card_id: str) -> List[str]:
    """Get dependencies from card metadata."""
    task_file = COORDINATION_PATH / f"{card_id}-*.json"
    matching_files = list(glob.glob(str(task_file)))
    deps = []
    for f in matching_files:
        try:
            with open(f, 'r') as fh:
                data = json.load(fh)
                deps.extend(data.get('dependencies', []))
        except Exception:
            continue
    return deps

def get_current_claim(card_id: str) -> Dict[str, Any]:
    """Get current agent claim on a card."""
    agents_dir = Path("/home/skuser01/.skcapstone/coordination/agents")
    for agent_file in agents_dir.glob("*.json"):
        try:
            with open(agent_file, 'r') as fh:
                data = json.load(fh)
                claimed = data.get('claimed_tasks', [])
                current = data.get('current_task')
                if card_id in claimed or current == card_id:
                    return {
                        'agent': data.get('agent'),
                        'host': data.get('host'),
                        'state': data.get('state'),
                        'last_seen': data.get('last_seen')
                    }
        except Exception:
            continue
    return None

def classify_obstruction(card_id: str, lifecycle: Dict, claim: Dict, dependencies: List, events: List) -> Dict[str, Any]:
    """Classify why a card is blocked."""
    classification = {
        'obstruction_type': 'unknown',
        'reason': '',
        'referent': None,
        'evidence': []
    }

    # Check for dependencies
    if dependencies:
        # Check if any dependency is incomplete
        for dep_id in dependencies:
            dep_dir = CARDSTORE_PATH / dep_id
            if not dep_dir.exists():
                classification['obstruction_type'] = 'void_dependency'
                classification['reason'] = f'Dependency {dep_id} does not exist in CardStore'
                classification['referent'] = f'card:{dep_id}'
                classification['evidence'].append(f'void_dependency:{dep_id}')
                return classification

            # Check dependency state
            dep_events = read_card_events(dep_id)
            dep_lifecycle = get_card_lifecycle(dep_events)
            if dep_lifecycle['state'] not in ['completed', 'closed', 'void']:
                classification['obstruction_type'] = 'dependency'
                classification['reason'] = f'Awaits completion of dependency {dep_id} (state: {dep_lifecycle["state"]})'
                classification['referent'] = f'card:{dep_id}'
                classification['evidence'].append(f'dependency:{dep_id}')
                return classification

    # Check for stale claim
    if claim:
        last_seen = claim.get('last_seen')
        if last_seen:
            try:
                claim_time = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                now = datetime.now(claim_time.tzinfo)
                delta = (now - claim_time).total_seconds()
                if delta > 3600:  # 1 hour stale
                    classification['obstruction_type'] = 'stale_claim'
                    classification['reason'] = f'Claim by {claim["agent"]} stale for {delta/60:.1f} minutes'
                    classification['referent'] = f'agent:{claim["agent"]}'
                    classification['evidence'].append(f'stale_claim:{claim["agent"]}')
                    return classification
            except Exception:
                pass

    # Check for awaiting review state
    if lifecycle['state'] in ['review', 'pending_review']:
        classification['obstruction_type'] = 'awaiting_review'
        classification['reason'] = f'Card is in {lifecycle["state"]} state awaiting human review'
        classification['referent'] = 'human:review'
        classification['evidence'].append(f'state:{lifecycle["state"]}')
        return classification

    # Check for human gates in events
    for event in events:
        if 'requires_human' in event and event['requires_human']:
            classification['obstruction_type'] = 'human_gate'
            classification['reason'] = f'Human gate required: {event.get("human_gate", "unspecified")}'
            classification['referent'] = f'human:{event.get("human_gate", "approval")}'
            classification['evidence'].append(f'human_gate:{event.get("human_gate", "unspecified")}')
            return classification

    # Check for card contradictions
    for event in events:
        if 'verdict' in event:
            verdict = event['verdict']
            if verdict == 'BLOCKED':
                blocked_on = event.get('blocked_on')
                if blocked_on:
                    if blocked_on.get('type') == 'card':
                        classification['obstruction_type'] = 'card_contradiction'
                        classification['reason'] = f'Card contradiction: {blocked_on.get("reason", "unspecified")}'
                        classification['referent'] = blocked_on.get('referent', 'card:contradiction')
                        classification['evidence'].append(f'card_contradiction:{blocked_on}')
                        return classification

    # If no obstruction found
    classification['obstruction_type'] = 'genuinely_assignable'
    classification['reason'] = 'No obstruction detected - card can be assigned'
    classification['referent'] = None
    classification['evidence'].append('assignable:true')

    return classification

def main():
    timestamp = datetime.utcnow().isoformat() + 'Z'

    print(f"AUDIT START: {timestamp}")
    print(f"Partition: {PARTITION_TARGET} of {PARTITION_MODULUS}")
    print(f"CardStore path: {CARDSTORE_PATH}")
    print(f"Coordination path: {COORDINATION_PATH}")
    print()

    all_card_ids = get_all_card_ids()
    print(f"Total cards in CardStore: {len(all_card_ids)}")

    # Filter to partition 7
    in_partition = [cid for cid in all_card_ids if is_in_partition(cid)]
    print(f"Cards in partition {PARTITION_TARGET}: {len(in_partition)}")

    # Exclude fleet-unblock-audit cards
    partition_cards = [cid for cid in in_partition if not has_fleet_unblock_audit_tag(cid)]
    print(f"Excluding fleet-unblock-audit: {len(in_partition) - len(partition_cards)}")
    print(f"Cards to audit: {len(partition_cards)}")
    print()

    # Build report
    report = {
        'audit_metadata': {
            'card_id': '7b7c990f',
            'partition': PARTITION_TARGET,
            'partition_modulus': PARTITION_MODULUS,
            'timestamp': timestamp,
            'cardstore_path': str(CARDSTORE_PATH),
            'coordination_path': str(COORDINATION_PATH),
            'total_cards': len(all_card_ids),
            'in_partition': len(in_partition),
            'excluded_audit_cards': len(in_partition) - len(partition_cards),
            'audited_cards': len(partition_cards)
        },
        'selection_algorithm': {
            'description': 'int(card_id, 16) modulo 33 equals 7',
            'exclude_tags': ['fleet-unblock-audit']
        },
        'cards': []
    }

    for card_id in sorted(partition_cards):
        print(f"Auditing card: {card_id}")

        events = read_card_events(card_id)
        lifecycle = get_card_lifecycle(events)
        dependencies = get_card_dependencies(card_id)
        claim = get_current_claim(card_id)
        obstruction = classify_obstruction(card_id, lifecycle, claim, dependencies, events)

        card_data = {
            'card_id': card_id,
            'lifecycle': lifecycle,
            'dependencies': dependencies,
            'current_claim': claim,
            'obstruction_classification': obstruction,
            'event_count': len(events)
        }

        # Check for evidence links
        evidence_links = []
        for event in events:
            if 'evidence' in event:
                evidence_links.append(event['evidence'])
            if 'verdict' in event and 'evidence_link' in event:
                evidence_links.append(event['evidence_link'])

        card_data['evidence_links'] = evidence_links
        report['cards'].append(card_data)

        print(f"  State: {lifecycle['state']}")
        print(f"  Obstruction: {obstruction['obstruction_type']}")
        print(f"  Dependencies: {len(dependencies)}")
        print(f"  Events: {len(events)}")
        print()

    # Write report
    report_path = Path("/home/skuser01/.skcapstone/fleet/workspaces/pi-glm-chiap04-7b7c990f/partition_07_audit_report.json")
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    # Calculate SHA-256
    with open(report_path, 'rb') as f:
        report_hash = hashlib.sha256(f.read()).hexdigest()

    print(f"Report written to: {report_path}")
    print(f"Report SHA-256: {report_hash}")
    print(f"AUDIT COMPLETE: {datetime.utcnow().isoformat()}Z")

    return report, report_hash

if __name__ == '__main__':
    report, report_hash = main()
